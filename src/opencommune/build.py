"""Build stage: the serving database the Rust server reads (`serving.sql`), written from scratch into
a new file that replaces the old one only when it is complete.

Sources: the communes and the catalogue in the main database (`opencommune catalog`), the URL
index (when a file was first downloaded), the classification answers in the processing database,
the prompt record of the classification run, the coverage register, the access observations and
the ledger of released versions (`release`).

Only files with an answer from the classification run are listed (David, 2026-11-13). From an
answer, the enumerated fields, the page numbers and the title and body as printed reach this file;
the prompt has the model write private names as [name] in every field.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import astuple
from importlib.resources import files
from pathlib import Path

from . import release
from .classify import record as records
from .classify.render import pages_to_send
from .communes import load_communes
from .config import settings
from .crawl import access
from .crawl.store import index_path
from .register import RegisterEntry, load_register, publication_note
from .schemas import RawSidecar

log = logging.getLogger(__name__)

# The classification the site shows: cls-2, the whole archive at 75 dpi (David, 2026-11-11).
CLASSIFICATION = "classify-65e415a748e2"
# The family of each section type, for display and the commune overview (David, 2026-11-13). The one
# place this mapping lives: the server and the website read the family from the serving database.
FAMILIES = {
    "minutes": "deliberation",
    "decisions": "deliberation",
    "vote_result": "deliberation",
    "budget": "money",
    "accounts": "money",
    "proposal": "proposals",
    "member_request": "proposals",
    "invitation_agenda": "proposals",
    "regulation": "rules",
    "report": "information",
    "notice": "information",
    "news": "information",
    "plan_or_map": "information",
    "other": "other",
    "unclear": "other",
}
TABLES = (
    "communes",
    "runs",
    "documents",
    "document_communes",
    "sections",
    "register",
    "register_publications",
    "access_observations",
    "releases",
    "downloads",
)


def register_note(entry: RegisterEntry, body: str | None) -> str | None:
    """The note the commune list shows: for the commune's configured body, else for the first
    publication with an obstacle or a `how`."""
    pub = entry.publication_for(body) if body else None
    if pub is None:
        pub = next((p for p in entry.publications if p.obstacle or p.how), None)
    return publication_note(entry, pub)


def run_note(record: records.PromptRecord) -> str:
    """What the run's settings do to the data, in plain sentences, derived from the settings."""
    s = record.settings
    head, tail = s.max_pages // 3, s.max_pages // 6
    middle = s.max_pages - head - tail
    qualities = s.jpeg_quality_first_then_if_too_large
    return (
        f"Classified by {s.model} from page images at {s.dpi} dpi. Pages larger than "
        f"{s.max_patches} image patches were scaled down. Images were JPEG quality {qualities[0]}, "
        f"or {qualities[-1]} when a request would exceed {s.max_request_bytes // 1_000_000} MB. "
        f"Documents with more than {s.max_pages} pages were sampled: the model saw the first {head}, "
        f"the last {tail} and {middle} pages spread evenly between; sections over pages it did "
        "not see are its estimate and are marked partly_unseen. Documents that could not be "
        "rendered were not sent. Dates, years, kinds and sections are as the model read them; "
        "no person has checked them."
    )


def run(serving_path: Path, database_path: Path, process_path: Path) -> dict[str, int]:
    """Write the serving database to `serving_path`; return the row count per table."""
    tmp = serving_path.with_name(serving_path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        with closing(sqlite3.connect(tmp)) as db:
            with db:
                db.executescript(files(__package__).joinpath("serving.sql").read_text())
                register = load_register()
                first_seen = _first_seen()
                _communes(db, database_path, register)
                _documents(db, database_path, process_path, first_seen)
                _register(db, register)
                _history(db, first_seen)
                # The title index reads `documents` (serving.sql), so it comes last.
                db.execute("INSERT INTO documents_fts (documents_fts) VALUES ('rebuild')")
            counts = {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}
            db.execute("PRAGMA journal_mode=DELETE")
        os.replace(tmp, serving_path)
    finally:
        tmp.unlink(missing_ok=True)
    for table, n in counts.items():
        log.info("build: %s %d rows", table, n)
    return counts


def _communes(db, database_path: Path, register) -> None:
    """The configured communes, then every other commune of the register: each Swiss commune has a
    page, also one that refuses the crawler or has published nothing yet."""
    with closing(sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)) as main:
        rows = main.execute(
            "SELECT bfs_id, name, canton, nullif(language, ''), website, vendor, "
            "publicity_regime, lat, lon FROM communes"
        ).fetchall()
    db.executemany("INSERT INTO communes VALUES (?,?,?,?,?,?,?,?,?)", rows)
    configured = {row[0] for row in rows}
    db.executemany(
        "INSERT INTO communes (bfs_id, name, canton, website, vendor) VALUES (?,?,?,?,?)",
        [
            (e.bfs_id, e.name, e.canton, e.website, e.vendor)
            for e in register
            if e.bfs_id not in configured
        ],
    )


def _first_seen() -> dict[str, str]:
    """sha256 -> the date of its first download, from the URL index."""
    first: dict[str, str] = {}
    path = index_path()
    if not path.exists():
        return first
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            sha, at = entry.get("sha256"), (entry.get("fetched_at") or "")[:10]
            if sha and at and (sha not in first or at < first[sha]):
                first[sha] = at
    return first


def _iso_date(value) -> str | None:
    try:
        return dt.date.fromisoformat(value).isoformat() if isinstance(value, str) else None
    except ValueError:
        return None


def _text(value) -> str | None:
    value = " ".join(value.split()) if isinstance(value, str) else ""
    return value or None


def _year(date: str | None, years: list, listing_date: str | None) -> int | None:
    """The year of the date; else the one year the classifier read; else of the listing date."""
    if date:
        return int(date[:4])
    if len(years) == 1 and isinstance(years[0], int):
        return years[0]
    return int(listing_date[:4]) if listing_date else None


def _documents(db, database_path: Path, process_path: Path, first_seen: dict[str, str]) -> None:
    record = records.load(CLASSIFICATION)
    with closing(sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)) as main:
        catalogue = main.execute("SELECT sha256, source_date FROM pdf_catalog").fetchall()
        links = main.execute(
            "SELECT sha256, commune_bfs_id, source_url FROM pdf_catalog_communes"
        ).fetchall()
    with closing(sqlite3.connect(f"file:{process_path}?mode=ro", uri=True)) as process:
        answers = {
            sha: (json.loads(answer), model, pages, sent)
            for sha, answer, model, pages, sent in process.execute(
                "SELECT sha256, answer, model, pages, pages_sent FROM answers WHERE version = ?",
                (CLASSIFICATION,),
            )
        }

    known = {row[0] for row in db.execute("SELECT bfs_id FROM communes")}
    # Nothing is dated after the day of the build: a later date or year is a misreading (a target
    # year, a typo) and would otherwise sort first in every list.
    today = dt.date.today()
    models: set[str] = set()
    listed: set[str] = set()
    for sha, listing_date in catalogue:
        found = answers.get(sha)
        if found is None:
            continue  # not classified: not listed
        answer, model, pages, sent = found
        models.add(model or record.settings.model)
        listed.add(sha)
        date = _iso_date(answer.get("date"))
        if date and date > today.isoformat():
            date = None
        years = [
            y for y in answer.get("years") or [] if not (isinstance(y, int) and y > today.year)
        ]
        if listing_date and listing_date > today.isoformat():
            listing_date = None
        db.execute(
            "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sha,
                _text(answer.get("title")),
                _text(answer.get("body_name")),
                first_seen.get(sha) or _sidecar_date(sha),
                listing_date,
                pages,
                CLASSIFICATION,
                answer.get("document_kind"),
                answer.get("body_kind"),
                date,
                ",".join(str(y) for y in years),
                _year(date, years, listing_date),
                ",".join(answer.get("languages") or []),
                int(answer.get("personal_data") == "yes"),
                sent,
            ),
        )
        shown = None
        if pages and sent is not None and sent < pages:
            shown = {i + 1 for i in pages_to_send(pages, record.settings.max_pages)}
        counted: set[int] = set()  # pages an earlier section of this document already counts
        for position, section in enumerate(answer.get("sections") or [], 1):
            first, last = section["first_page"], section["last_page"]
            span = range(min(first, last), max(first, last) + 1)
            unseen = shown is not None and any(p not in shown for p in span)
            own = {p for p in span if p >= 1 and (not pages or p <= pages)} - counted
            counted |= own
            db.execute(
                "INSERT INTO sections VALUES (?,?,?,?,?,?,?,?)",
                (
                    sha,
                    position,
                    section["type"],
                    FAMILIES[section["type"]],
                    first,
                    last,
                    int(unseen),
                    len(own),
                ),
            )
    db.executemany(
        "INSERT INTO document_communes VALUES (?,?,?)",
        [(sha, bfs, url) for sha, bfs, url in links if sha in listed and bfs in known],
    )
    db.execute(
        "INSERT INTO runs VALUES (?,?,?,?,?,?)",
        (
            CLASSIFICATION,
            "classification",
            ", ".join(sorted(models)) or record.settings.model,
            record.settings.model_dump_json(),
            run_note(record),
            len(listed),
        ),
    )


def _history(db, first_seen: dict[str, str]) -> None:
    """The released versions as the ledger has them now, the files first downloaded each day, and
    how this file was built."""
    db.executemany(
        f"INSERT INTO releases VALUES ({','.join('?' * len(release.COLUMNS))})",
        [astuple(r) for r in release.load(settings.releases_file)],
    )
    db.executemany(
        "INSERT INTO downloads VALUES (?, ?)", sorted(Counter(first_seen.values()).items())
    )
    db.executemany(
        "INSERT INTO build VALUES (?, ?)",
        [
            ("built_at", dt.datetime.now(dt.UTC).isoformat(timespec="seconds")),
            ("git_commit", release.code_commit() or ""),
        ],
    )


def _sidecar_date(sha: str) -> str:
    """The download date from the sidecar, for a file the URL index does not know."""
    sidecar = RawSidecar.model_validate_json((settings.raw_dir / f"{sha}.json").read_text())
    return sidecar.fetched_at.date().isoformat()


def _register(db, register) -> None:
    bodies = {c.bfs_id: c.body for c in load_communes()}
    for e in register:
        db.execute(
            "INSERT INTO register VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                e.bfs_id,
                e.name,
                e.canton,
                e.status,
                e.website,
                e.vendor,
                None if e.reachable is None else int(e.reachable),
                e.checked.isoformat() if e.checked else None,
                e.checked_by,
                e.notes,
                register_note(e, bodies.get(e.bfs_id)),
            ),
        )
        for i, p in enumerate(e.publications):
            d = p.model_dump(by_alias=True)
            db.execute(
                "INSERT INTO register_publications VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    e.bfs_id,
                    i,
                    d["body"],
                    d["kind"],
                    d["status"],
                    d["listing_url"],
                    d["from"],
                    d["to"],
                    d["cadence"],
                    d["obstacle"],
                    d["how"],
                ),
            )
    for bfs_id, sites in access.by_commune(register).items():
        db.executemany(
            "INSERT INTO access_observations VALUES (?,?,?,?,?,?)",
            [
                (
                    bfs_id,
                    site["source_url"],
                    site["status"],
                    site["checked_at"],
                    site["robots_url"],
                    site["restricted_at"],
                )
                for site in sites
            ],
        )
