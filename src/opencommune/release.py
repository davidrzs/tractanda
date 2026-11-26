"""Versions of the published data: the ledger `releases.csv` in the configuration and `release`.

A version is a serving database and the full-text index built from it, deployed together. It is
named by the day it is released, `2026.11.26`, and `2026.11.26.2` for a second one that day. The
ledger in git holds one row per version, rows only added: the counts the site shows, the
classification run and the commit of the code that built both files. `build` copies the ledger
into the serving database; the version that database belongs to is released after both files are
built, so `release` writes its row into the full-text index's `build` table too, and the server
shows it from there.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import logging
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import asdict, dataclass, fields
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Release:
    version: str
    date: str  # ISO date of the release
    documents: int  # documents listed
    pages: int  # their pages
    communes: int  # communes with at least one listed document
    searchable_pages: int  # pages in the full-text index
    ocr_pages: int  # of those, pages whose text came from OCR
    classification_run: str
    git_commit: str | None  # with "-dirty" when tracked files differed from it; empty when unknown
    note: str | None  # for the maintainers; the site does not show it


COLUMNS = [f.name for f in fields(Release)]
INTEGERS = {f.name for f in fields(Release) if f.type == "int"}


def load(path: Path) -> list[Release]:
    """The ledger's rows in file order; none when there is no ledger."""
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != COLUMNS:
            raise ValueError(f"{path}: the columns must be {', '.join(COLUMNS)}")
        return [
            Release(**{k: int(v) if k in INTEGERS else (v or None) for k, v in row.items()})
            for row in reader
        ]


def code_commit() -> str | None:
    """The commit of the running code, with "-dirty" when tracked files differ from it; None
    outside a git checkout."""
    root = Path(__file__).resolve().parents[2]
    try:
        head, changed = (
            subprocess.run(
                ["git", *args], cwd=root, capture_output=True, text=True, check=True
            ).stdout.strip()
            for args in (["rev-parse", "HEAD"], ["status", "--porcelain", "--untracked-files=no"])
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return head + ("-dirty" if changed else "")


def next_version(day: dt.date, taken: set[str]) -> str:
    base = day.strftime("%Y.%m.%d")
    version, n = base, 1
    while version in taken:
        n += 1
        version = f"{base}.{n}"
    return version


def run(
    ledger: Path, serving_path: Path, fulltext_path: Path, note: str, day: dt.date | None = None
) -> Release:
    """Release the serving database and its full-text index: append their row to the ledger and
    write it into the index. Refuses a pair that is not one build of the current code and ledger."""
    rows = load(ledger)
    with closing(sqlite3.connect(f"file:{serving_path}?mode=ro", uri=True)) as serving:
        built = dict(serving.execute("SELECT key, value FROM build"))
        listed = [v for (v,) in serving.execute("SELECT version FROM releases ORDER BY rowid")]
        documents, pages = serving.execute(
            "SELECT count(*), coalesce(sum(page_count), 0) FROM documents"
        ).fetchone()
        (communes,) = serving.execute(
            "SELECT count(DISTINCT bfs_id) FROM document_communes"
        ).fetchone()
        runs = [r for (r,) in serving.execute("SELECT id FROM runs ORDER BY id")]
    with closing(sqlite3.connect(fulltext_path)) as index:
        indexed = dict(index.execute("SELECT key, value FROM build"))
        if "release" in indexed:
            version = json.loads(indexed["release"])["version"]
            raise ValueError(f"this full-text index is already released, as {version}")
        if listed != [r.version for r in rows]:
            raise ValueError(
                "the serving database was built from another ledger; "
                "run build and build-fulltext again"
            )
        if indexed.get("serving_db_sha256") != _sha256(serving_path):
            raise ValueError("the full-text index was built from another serving database")
        if indexed.get("git_commit") != built.get("git_commit"):
            raise ValueError("the two files were built by different code; build both again")
        searchable, ocr = index.execute(
            "SELECT count(*), count(*) FILTER (WHERE source = 'ocr') FROM pages"
        ).fetchone()
        day = day or dt.date.today()
        release = Release(
            version=next_version(day, {r.version for r in rows}),
            date=day.isoformat(),
            documents=documents,
            pages=pages,
            communes=communes,
            searchable_pages=searchable,
            ocr_pages=ocr,
            classification_run=", ".join(runs),
            git_commit=built.get("git_commit") or None,
            note=note or None,
        )
        with index:
            index.execute("INSERT INTO build VALUES ('release', ?)", (json.dumps(asdict(release)),))
            _append(ledger, release)
    log.info("release: %s", release.version)
    return release


def _append(ledger: Path, release: Release) -> None:
    new = not ledger.exists()
    with ledger.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, lineterminator="\n")
        if new:
            writer.writerow(COLUMNS)
        writer.writerow(["" if v is None else v for v in asdict(release).values()])


def _sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()
