import json
import sqlite3

from opencommune import build, release
from opencommune.classify.store import Store
from opencommune.config import settings
from opencommune.crawl import access
from opencommune.db import _make_engine, init_db
from opencommune.register import Publication, RegisterEntry

REGISTER = [
    RegisterEntry(
        bfs_id=1,
        name="Alpha",
        canton="BE",
        status="blocked_robots",
        website="https://www.alpha.example",
        publications=[
            Publication(
                body="assembly",
                kind="minutes",
                status="blocked_robots",
                listing_url="https://www.alpha.example/protokolle",
                obstacle="robots.txt disallows the minutes listing",
            )
        ],
    ),
    # Two communes on one host: an observation there belongs to neither.
    RegisterEntry(bfs_id=2, name="Beta", canton="ZH", website="https://shared.example/beta"),
    RegisterEntry(bfs_id=3, name="Gamma", canton="ZH", website="https://shared.example/gamma"),
]
SAMPLED, UNREADABLE, EXPIRED, PENDING, BUDGET = ("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64)
ANSWER = {
    "document_kind": "meeting_booklet",
    "sections": [
        {"type": "minutes", "first_page": 1, "last_page": 10},
        {"type": "budget", "first_page": 60, "last_page": 70},
    ],
    "title": "Gemeindeversammlung  vom 28. November 2024,\n Anträge von [name]",
    "body_name": "Gemeindeversammlung Alpha",
    "body_kind": "citizens_assembly",
    "years": [2024, 2025],
    "date": "2024-11-28",
    "languages": ["de"],
    "personal_data": "yes",
}


def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.ensure_dirs()
    monkeypatch.setattr(build, "load_register", lambda: REGISTER)
    monkeypatch.setattr(build, "load_communes", lambda: [])
    main = tmp_path / "opencommune.db"
    engine = _make_engine(f"sqlite:///{main}")
    init_db(engine)
    engine.dispose()
    with sqlite3.connect(main) as db:
        db.execute(
            "INSERT INTO communes (bfs_id, name, canton, language, website) "
            "VALUES (1, 'Alpha', 'BE', 'de', 'https://www.alpha.example')"
        )
        for sha in (SAMPLED, UNREADABLE, EXPIRED, PENDING, BUDGET):
            db.execute(
                "INSERT INTO pdf_catalog (sha256, commune_bfs_id, commune_name, canton, body, "
                "source_date, date_precision, title, source_url, provenance, indexed_at) "
                "VALUES (?, 1, 'Alpha', 'BE', 'assembly', '2024-12-01', 'day', 'Protokoll 2024', "
                "'https://www.alpha.example/x.pdf', 'html_links', '2026-01-01')",
                (sha,),
            )
            db.execute(
                "INSERT INTO pdf_catalog_communes VALUES (?, 1, 'https://www.alpha.example/x.pdf')",
                (sha,),
            )
        # the same file linked by a register commune that is not configured
        db.execute(
            "INSERT INTO pdf_catalog_communes VALUES (?, 2, 'https://shared.example/beta/x.pdf')",
            (SAMPLED,),
        )
    lines = [
        {"sha256": SAMPLED, "fetched_at": "2026-02-03T10:00:00+00:00"},
        {"sha256": SAMPLED, "fetched_at": "2026-01-05T10:00:00+00:00"},
        *(
            {"sha256": s, "fetched_at": "2026-01-01T00:00:00+00:00"}
            for s in (UNREADABLE, EXPIRED, PENDING, BUDGET)
        ),
    ]
    (settings.raw_dir / "urls.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    with Store(tmp_path / "process.db") as store, store.transaction():
        version = build.CLASSIFICATION
        store.add_answer(
            SAMPLED, version, "cls-2", ANSWER, {}, model="m", pages=200, pages_sent=150
        )
        store.add_failure(UNREADABLE, version, "cls-2", "request", "unreadable")
        budget = {
            **ANSWER,
            "title": None,
            "body_name": " ",
            "date": None,
            "years": [2026],
            "personal_data": "no",
            "sections": [{"type": "budget", "first_page": 1, "last_page": 9}],
        }
        store.add_answer(BUDGET, version, "cls-2", budget, {}, model="m", pages=9, pages_sent=9)
        store.add_failure(EXPIRED, version, "cls-2", "request", "batch_expired")
    return main, tmp_path / "process.db"


def test_build_writes_the_serving_database_from_scratch(tmp_path, monkeypatch):
    main, process = corpus(tmp_path, monkeypatch)
    access.record("https://alpha.example/protokolle?page=2", "restricted")
    access.record("https://alpha.example/files/private-name.pdf", "rate_limited")
    access.record("https://shared.example/beta/files", "restricted")
    serving = tmp_path / "serving.db"
    baseline = release.Release("2026.01.02", "2026-01-02", 1, 9, 1, 9, 0, "cls", None, None)
    release._append(settings.releases_file, baseline)
    monkeypatch.setattr(release, "code_commit", lambda: "abc")

    counts = build.run(serving, main, process)
    assert counts == {
        "communes": 3,
        "runs": 1,
        "documents": 2,
        "document_communes": 3,
        "sections": 3,
        "register": 3,
        "register_publications": 1,
        "access_observations": 1,
        "releases": 1,
        "downloads": 2,
    }
    assert build.run(serving, main, process) == counts  # a rebuild replaces, never appends
    assert not (tmp_path / "serving.db.tmp").exists()

    db = sqlite3.connect(serving)
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    # only classified files are listed
    assert {r[0] for r in db.execute("SELECT sha256 FROM documents")} == {SAMPLED, BUDGET}
    row = db.execute(
        "SELECT title, body_name, first_seen, listing_date, page_count, run_id, kind, body_kind, "
        "date, years, year, languages, personal_data, pages_seen FROM documents WHERE sha256 = ?",
        (SAMPLED,),
    ).fetchone()
    assert row == (
        "Gemeindeversammlung vom 28. November 2024, Anträge von [name]",
        "Gemeindeversammlung Alpha",
        "2026-01-05",
        "2024-12-01",
        200,
        build.CLASSIFICATION,
        "meeting_booklet",
        "citizens_assembly",
        "2024-11-28",
        "2024,2025",
        2024,
        "de",
        1,
        150,
    )
    # no date: the one year the classifier read; no title or a blank body: NULL
    assert db.execute(
        "SELECT title, body_name, date, year, personal_data FROM documents WHERE sha256 = ?",
        (BUDGET,),
    ).fetchone() == (None, None, None, 2026, 0)

    # the title index: three or more characters of the title or the body, diacritics ignored
    def titled(query):
        return db.execute(
            "SELECT d.sha256 FROM documents_fts f JOIN documents d ON d.rowid = f.rowid "
            "WHERE documents_fts MATCH ?",
            (query,),
        ).fetchall()

    assert titled("antrage") == titled("VERSAMMLUNG ALPHA") == [(SAMPLED,)]
    assert titled("budget") == []
    db.execute("INSERT INTO documents_fts (documents_fts, rank) VALUES ('integrity-check', 1)")
    # the first 50 pages were all shown; pages 60 to 70 lie where only some were
    assert db.execute(
        "SELECT type, family, first_page, last_page, partly_unseen FROM sections "
        "WHERE sha256 = ? ORDER BY position",
        (SAMPLED,),
    ).fetchall() == [("minutes", "deliberation", 1, 10, 0), ("budget", "money", 60, 70, 1)]
    assert set(build.FAMILIES) == set(
        json.loads((build.records.PROMPTS / build.CLASSIFICATION / "schema.json").read_text())[
            "properties"
        ]["sections"]["items"]["properties"]["type"]["enum"]
    )
    assert db.execute(
        "SELECT c.name FROM document_communes d JOIN communes c USING (bfs_id) "
        "WHERE d.sha256 = ? ORDER BY c.name",
        (SAMPLED,),
    ).fetchall() == [("Alpha",), ("Beta",)]
    # every commune of the register has a row, also one without documents
    assert db.execute("SELECT name FROM communes WHERE bfs_id = 3").fetchone() == ("Gamma",)
    run_id, settings_json, note, documents = db.execute(
        "SELECT id, settings, note, documents FROM runs"
    ).fetchone()
    assert run_id == build.CLASSIFICATION and documents == 2
    assert json.loads(settings_json)["max_pages"] == 150
    assert "more than 150 pages" in note and "the first 50" in note
    status, note = db.execute("SELECT status, note FROM register WHERE bfs_id = 1").fetchone()
    assert status == "blocked_robots" and "robots.txt" in note
    # one row per website: the latest check's status, the restriction kept, no document address
    row = db.execute(
        "SELECT bfs_id, source_url, status, restricted_at IS NOT NULL FROM access_observations"
    ).fetchone()
    assert row == (1, "https://alpha.example/", "rate_limited", 1)
    # the ledger as it stands, every file by the day of its first download, and the code
    assert db.execute("SELECT * FROM releases").fetchall() == [
        ("2026.01.02", "2026-01-02", 1, 9, 1, 9, 0, "cls", None, None)
    ]
    assert db.execute("SELECT * FROM downloads").fetchall() == [
        ("2026-01-01", 4),
        ("2026-01-05", 1),
    ]
    built = dict(db.execute("SELECT key, value FROM build"))
    assert built["git_commit"] == "abc" and built["built_at"].startswith("20")
    db.close()


def test_a_date_or_year_after_the_build_day_is_left_out(tmp_path, monkeypatch):
    main, process = corpus(tmp_path, monkeypatch)
    misread = {**ANSWER, "date": "2204-10-25", "years": [2024, 2204]}
    with Store(process) as store, store.transaction():
        store.add_answer(
            PENDING, build.CLASSIFICATION, "cls-2", misread, {}, model="m", pages=2, pages_sent=2
        )
    with sqlite3.connect(main) as db:
        db.execute("UPDATE pdf_catalog SET source_date = '2999-01-01' WHERE sha256 = ?", (PENDING,))
    serving = tmp_path / "serving.db"

    build.run(serving, main, process)

    db = sqlite3.connect(serving)
    # the date, the year 2204 and the listing date are left out; the one year left counts
    assert db.execute(
        "SELECT date, years, year, listing_date FROM documents WHERE sha256 = ?", (PENDING,)
    ).fetchone() == (None, "2024", 2024, None)


def test_each_page_counts_once_for_the_first_section_that_covers_it(tmp_path, monkeypatch):
    main, process = corpus(tmp_path, monkeypatch)
    overlapping = {
        **ANSWER,
        "sections": [
            {"type": "proposal", "first_page": 1, "last_page": 31},
            {"type": "decisions", "first_page": 31, "last_page": 32},
            {"type": "notice", "first_page": 30, "last_page": 40},
        ],
    }
    with Store(process) as store, store.transaction():
        store.add_answer(
            PENDING,
            build.CLASSIFICATION,
            "cls-2",
            overlapping,
            {},
            model="m",
            pages=33,
            pages_sent=33,
        )
    serving = tmp_path / "serving.db"

    build.run(serving, main, process)

    db = sqlite3.connect(serving)
    # page 31 belongs to the proposal; the notice keeps only page 33, the last page of the file
    assert db.execute(
        "SELECT type, pages FROM sections WHERE sha256 = ? ORDER BY position", (PENDING,)
    ).fetchall() == [("proposal", 31), ("decisions", 1), ("notice", 1)]
    assert db.execute(
        "SELECT pages FROM sections WHERE sha256 = ? ORDER BY position", (SAMPLED,)
    ).fetchall() == [(10,), (11,)]
