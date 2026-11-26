import datetime as dt
import json
import sqlite3
from contextlib import closing
from dataclasses import astuple, asdict

import pytest

from opencommune import fulltext, release
from tests.scope_data import DOCUMENTS, privacy_db, serving_db, text_db

DAY = dt.date(2026, 11, 26)
BASELINE = release.Release("2026.11.17", "2026-11-17", 1, 2, 1, 2, 0, "cls", None, "baseline")


def pair(tmp_path, monkeypatch, commit="abc"):
    """A ledger with one version, and a serving database and full-text index built from it."""
    ledger = tmp_path / "releases.csv"
    release._append(ledger, BASELINE)
    serving = serving_db(tmp_path / "serving.db")
    with closing(sqlite3.connect(serving)) as db, db:
        db.execute("INSERT INTO releases VALUES (?,?,?,?,?,?,?,?,?,?)", astuple(BASELINE))
        db.execute("INSERT INTO build VALUES ('git_commit', ?)", (commit,))
        db.execute("INSERT INTO communes (bfs_id, name, canton) VALUES (1, 'Alpha', 'BE')")
        db.executemany(
            "INSERT INTO document_communes VALUES (?, 1, NULL)", [(d[0],) for d in DOCUMENTS[:2]]
        )
    text = text_db(tmp_path / "text.db")
    privacy = privacy_db(tmp_path / "privacy.db", serving, text, lambda text: [])
    monkeypatch.setattr(fulltext, "code_commit", lambda: "abc")
    index = tmp_path / "fulltext.db"
    fulltext.run(index, serving, text, privacy)
    return ledger, serving, index


def test_a_release_appends_its_row_to_the_ledger_and_the_index(tmp_path, monkeypatch):
    ledger, serving, index = pair(tmp_path, monkeypatch)

    row = release.run(ledger, serving, index, "first OCR pages", DAY)
    assert row == release.Release(
        version="2026.11.26",
        date="2026-11-26",
        documents=len(DOCUMENTS),
        pages=sum(d[1] or 0 for d in DOCUMENTS),
        communes=1,
        searchable_pages=4,
        ocr_pages=1,
        classification_run="cls",
        git_commit="abc",
        note="first OCR pages",
    )
    assert release.load(ledger) == [BASELINE, row]
    assert ledger.read_text().splitlines()[0] == ",".join(release.COLUMNS)
    with closing(sqlite3.connect(index)) as db:
        (written,) = db.execute("SELECT value FROM build WHERE key = 'release'").fetchone()
    assert json.loads(written) == asdict(row)

    with pytest.raises(ValueError, match="already released, as 2026.11.26"):
        release.run(ledger, serving, index, "again", DAY)
    assert release.load(ledger) == [BASELINE, row]


def test_a_release_refuses_files_that_are_not_one_build(tmp_path, monkeypatch):
    ledger, serving, index = pair(tmp_path, monkeypatch, commit="other")
    with pytest.raises(ValueError, match="different code"):
        release.run(ledger, serving, index, "", DAY)

    with closing(sqlite3.connect(serving)) as db, db:
        db.execute("UPDATE build SET value = 'abc'")
    with pytest.raises(ValueError, match="another serving database"):
        release.run(ledger, serving, index, "", DAY)  # changed after the index was built

    later = release.Release("2026.11.20", "2026-11-20", 1, 2, 1, 2, 0, "cls", None, None)
    release._append(ledger, later)
    with pytest.raises(ValueError, match="another ledger"):
        release.run(ledger, serving, index, "", DAY)
    assert release.load(ledger) == [BASELINE, later]


def test_versions_are_named_by_day():
    assert release.next_version(DAY, set()) == "2026.11.26"
    assert release.next_version(DAY, {"2026.11.26", "2026.11.26.2"}) == "2026.11.26.3"


def test_a_ledger_with_other_columns_is_refused(tmp_path):
    ledger = tmp_path / "releases.csv"
    ledger.write_text("version,date\n2026.11.17,2026-11-17\n")
    with pytest.raises(ValueError, match="columns"):
        release.load(ledger)
    assert release.load(tmp_path / "none.csv") == []
