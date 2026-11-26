"""The full-text index, DATA_DIR/fulltext.db: the text of the pages the search covers.

`run` writes it from scratch into a new file and renames that over the old index only when it is
complete. It reads the serving database, text.db and privacy.db and changes none of them.

The pages and their text come from `scope.pages`: a page is indexed when it is in scope, its text
is usable, and the privacy check has read that same text. The index holds the text redacted
(`privacy.redact`), never the original. A page inside two sections is indexed once. Section types,
communes, years and dates are not copied: the server joins the serving database on sha256 and page.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path

from . import scope
from .privacy import redact, store
from .release import code_commit

log = logging.getLogger(__name__)

SCHEMA = """
-- One row per indexed page, in document and page order (the server relies on a document's ids
-- being neighbours). Its id is the rowid of the page's text in page_fts.
CREATE TABLE pages (
    id INTEGER PRIMARY KEY,
    sha256 TEXT NOT NULL,
    page INTEGER NOT NULL,          -- 1 for the first page
    source TEXT NOT NULL,           -- layer | ocr
    run TEXT NOT NULL,              -- the text.db run the text came from
    UNIQUE (sha256, page)
);
-- The text of each page, redacted. It is stored, so that the server can show snippets and whole
-- pages. The full-width brackets of the redaction marks (privacy.redact) are part of a word.
CREATE VIRTUAL TABLE page_fts USING fts5(
    text, tokenize = "unicode61 remove_diacritics 2 tokenchars '［］'"
);
-- How the file was built: the checksums of the inputs, the commit of the code, the counts (JSON)
-- and the rules; once released, the version's row of the ledger (`release`, JSON).
CREATE TABLE build (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""  # noqa: RUF001 - the brackets are meant

RULES = (
    f"A page is indexed when it lies inside a section of one of these types: {', '.join(scope.TYPES)}. "
    "The sections come from the serving database. "
    "Documents whose kind is bulletin are left out. "
    "Documents whose body is private are left out. "
    f"Pages whose text has fewer than {scope.MIN_CHARS} non-space characters are left out. "
    "The text is the page_text of text.db: the OCR answer if one is ok, else the text layer. "
    "An OCR answer is read as plain text: each table row on a line with its cells apart by ' | ', "
    "headings, bold marks, images and HTML tags removed, entities decoded. "
    "A word split at a line end is joined: a letter, '-', a line break and a lower-case letter "
    "become one word, unless the next word is und, oder, bis, bzw or sowie (a shortened compound "
    "such as 'Ein- und Ausgaben'). Apart from that and the redaction below, the text is not "
    "changed. "
    "A page inside two sections is indexed once. " + redact.RULE
)

BATCH = 10_000


def run(fulltext_path: Path, serving_path: Path, text_path: Path, privacy_path: Path) -> dict:
    """Write the index to `fulltext_path`; return the counts it records."""
    record = {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "serving_db_sha256": _sha256(serving_path),
        "text_db_sha256": _sha256(text_path),
        "privacy_db_sha256": _sha256(privacy_path),
        "git_commit": code_commit() or "",
    }
    tmp = fulltext_path.with_name(fulltext_path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        with closing(sqlite3.connect(tmp)) as db, closing(store.read_only(privacy_path)) as privacy:
            with db:
                db.executescript(SCHEMA)
                keep = _commune_words(serving_path)
                counts = _index(db, scope.pages(serving_path, text_path), privacy, keep)
                log.info("fulltext: merging the index")
                db.execute("INSERT INTO page_fts (page_fts) VALUES ('optimize')")
                rows = {**record, **{k: json.dumps(v) for k, v in counts.items()}, "rules": RULES}
                db.executemany("INSERT INTO build VALUES (?, ?)", rows.items())
            db.execute("PRAGMA journal_mode=DELETE")
        os.replace(tmp, fulltext_path)
    finally:
        tmp.unlink(missing_ok=True)
    log.info("fulltext: %d pages of %d documents", counts["pages"]["indexed"], counts["documents"])
    return counts


def _commune_words(serving_path: Path) -> frozenset[str]:
    """The words of every commune's name in the serving database and the parts of those with a
    hyphen (Châtel-Saint-Denis: Châtel, Saint, Denis): never taken for a person's name."""
    with closing(sqlite3.connect(f"file:{serving_path}?mode=ro", uri=True)) as db:
        names = [name for (name,) in db.execute("SELECT name FROM communes")]
    words = {word for name in names for word in redact.WORD.findall(name)}
    return frozenset(words | {part for word in words for part in word.split("-") if part})


def _sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _index(
    db: sqlite3.Connection,
    pages_in_scope,
    privacy: sqlite3.Connection,
    keep: frozenset[str] = frozenset(),
) -> dict:
    """Write the usable pages the privacy check has read, redacted, into `pages` and `page_fts`;
    return the counts. A document's pages are redacted together, so that what the check found on
    one page is replaced on the others too (`redact.mentions`); `keep`: words that are never
    names there."""
    pages = {"in_sections": 0, "no_text": 0, "too_short": 0, "not_checked": 0, "indexed": 0}
    by_type = {kind: {"pages": 0, "indexed": 0} for kind in scope.TYPES}
    marks: Counter = Counter()
    documents: set[str] = set()
    rows: list[tuple] = []
    texts: list[tuple] = []
    for sha256, document in groupby(pages_in_scope, key=lambda page: page.sha256):
        found = None
        checked = []
        for page in document:
            pages["in_sections"] += 1
            for kind in page.types:
                by_type[kind]["pages"] += 1
            if page.raw is None:
                pages["no_text"] += 1
                continue
            if not page.usable:
                pages["too_short"] += 1
                continue
            if found is None:
                found = store.checked(privacy, sha256)
            text = page.text
            spans = found.get((page.page, hashlib.sha256(text.encode()).hexdigest()))
            if spans is None:
                pages["not_checked"] += 1
                continue
            checked.append((page, text, spans))
        others = redact.mentions(((text, spans) for _, text, spans in checked), keep)
        for page, text, spans in checked:
            text, marked = redact.redact(text, spans, others)
            marks.update(marked)
            pages["indexed"] += 1
            for kind in page.types:
                by_type[kind]["indexed"] += 1
            documents.add(sha256)
            rows.append((pages["indexed"], sha256, page.page, page.source, page.run))
            texts.append((pages["indexed"], text))
            if len(rows) >= BATCH:
                _insert(db, rows, texts)
                log.debug("fulltext: %d pages of %d documents", pages["indexed"], len(documents))
    _insert(db, rows, texts)
    return {
        "documents": len(documents),
        "pages": pages,
        "section_types": by_type,
        "marks": dict(sorted(marks.items())),
    }


def _insert(db: sqlite3.Connection, rows: list[tuple], texts: list[tuple]) -> None:
    db.executemany("INSERT INTO pages VALUES (?, ?, ?, ?, ?)", rows)
    db.executemany("INSERT INTO page_fts (rowid, text) VALUES (?, ?)", texts)
    rows.clear()
    texts.clear()
