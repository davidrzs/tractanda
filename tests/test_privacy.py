import hashlib
import json
import sqlite3
import time
import tomllib
from collections import namedtuple
from contextlib import closing
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opencommune.cli import app
from opencommune.config import settings
from opencommune.privacy import detect, redact
from opencommune.text.queue import MAX_TRIES, Queue
from tests.scope_data import A, D, serving_db, text_db

Span = namedtuple("Span", "label start end score")


class Names:
    """A stand-in for gheim's detector: it reports the word "Gemeinderat" as a person."""

    def __init__(self):
        self.seen = []

    def detect(self, text):
        self.seen.append(text)
        start = text.find("Gemeinderat")
        return [] if start < 0 else [Span("private_person", start, start + 11, 0.912345)]


class Broken:
    def detect(self, text):
        raise RuntimeError("CUDA error: an illegal memory access was encountered")


def make_queue(tmp_path, monkeypatch, chunk_pages=2):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    serving_db(tmp_path / "serving.db")
    text_db(tmp_path / "text.db")
    made = CliRunner().invoke(app, ["privacy", "queue", "q", "--chunk-pages", str(chunk_pages)])
    assert made.exit_code == 0, made.output
    return Queue(tmp_path / "privacy" / "queues" / "q")


def test_the_queue_carries_the_pages_with_usable_text_as_the_index_stores_it(tmp_path, monkeypatch):
    queue = make_queue(tmp_path, monkeypatch)
    items = [item for chunk in queue.chunks() for item in queue.items(chunk)]
    assert [(item["sha256"], item["page"]) for item in items] == [(A, 1), (A, 2), (A, 6), (D, 1)]
    assert items[0]["text"] == "Crédit d'engagement pour la route"  # the OCR answer counts
    assert items[1]["text"] == "Der Gemeinderat beschliesst den Steuerfuss.\nGemeinde-\nRat"
    assert (queue.settings["pages"], queue.settings["chunks"]) == (4, 2)
    wanted = detect.detector_settings()
    assert {key: queue.settings[key] for key in wanted} == wanted
    assert CliRunner().invoke(app, ["privacy", "queue", "q"]).exit_code != 0


def test_a_worker_records_the_spans_of_every_page_once(tmp_path, monkeypatch):
    queue = make_queue(tmp_path, monkeypatch)
    names = Names()
    result = detect.run(queue, "one", None, detector=names)
    assert result["stopped"] == "no chunk left"
    assert (result["ok"], result["chunks_finished"]) == (4, 2)
    assert detect.run(queue, "two", None, detector=names)["stopped"] == "no chunk left"
    assert len(names.seen) == 4
    assert all(queue.is_done(chunk) for chunk in queue.chunks())
    answer = next(r for r in queue.answers("00000") if r["page"] == 2)
    assert answer["spans"] == [["private_person", 4, 15, 0.9123]]
    text = queue.items("00000")[1]["text"]
    assert answer["text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert answer["worker"].startswith("one@")
    assert queue.status()["pages"] == {"total": 4, "answered": 4, "ok": 4}
    worker = json.loads((queue.root / "workers" / "one" / "worker.json").read_text())
    assert worker["ok"] == 4 and worker["source_sha256"]


def test_errors_are_tried_again_and_a_failing_detector_stops_the_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, "MAX_FAILURES_IN_A_ROW", 2)
    queue = make_queue(tmp_path, monkeypatch, chunk_pages=4)
    failed = detect.run(queue, "one", None, detector=Broken())
    assert (failed["stopped"], failed["error"], failed["chunks_released"]) == (detect.FAILED, 2, 1)
    assert not queue.is_done("00000") and not list((queue.root / "claims").iterdir())
    assert queue.status()["pages"] == {"total": 4, "answered": 0, "error_retry": 2}
    assert detect.run(queue, "two", None, detector=Names())["ok"] == 4
    assert queue.is_done("00000")


def test_a_page_that_fails_every_try_is_given_up(tmp_path, monkeypatch):
    queue = make_queue(tmp_path, monkeypatch, chunk_pages=4)
    result = detect.run(queue, "one", None, detector=Broken())
    assert (result["stopped"], result["error"]) == ("no chunk left", 4 * MAX_TRIES)
    assert queue.is_done("00000")
    assert queue.status()["pages"] == {"total": 4, "answered": 4, "error": 4}


def test_a_worker_stops_at_its_time_and_releases_its_chunk(tmp_path, monkeypatch):
    queue = make_queue(tmp_path, monkeypatch)
    result = detect.run(queue, "one", time.time() - 1, detector=Names())
    assert result["stopped"] == "time is up" and "ok" not in result
    assert not queue.is_done("00000") and not list((queue.root / "claims").iterdir())


def test_a_worker_refuses_a_queue_made_for_another_model(tmp_path, monkeypatch):
    queue = make_queue(tmp_path, monkeypatch)
    monkeypatch.setattr(detect, "REVISION", "0" * 40)
    with pytest.raises(RuntimeError, match="made for"):
        detect.run(queue, "one", None, detector=Names())


def test_the_privacy_extra_pins_the_gheim_the_worker_wants():
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    extra = pyproject["project"]["optional-dependencies"]["privacy"]
    assert f"gheim[local]=={detect.GHEIM}" in extra


def test_the_privacy_commands_check_count_and_import(tmp_path, monkeypatch):
    make_queue(tmp_path, monkeypatch)
    runner = CliRunner()

    def run(*args):
        return runner.invoke(app, ["privacy", *args])

    monkeypatch.setattr(detect, "MAX_FAILURES_IN_A_ROW", 1)
    monkeypatch.setattr(detect, "load", lambda: (Broken(), {"gheim": "test"}))
    assert run("detect", "q", "--worker", "1").exit_code == 2
    monkeypatch.setattr(detect, "load", lambda: (Names(), {"gheim": "test"}))
    ran = run("detect", "q", "--worker", "2")
    assert ran.exit_code == 0, ran.output
    assert json.loads(ran.stdout.splitlines()[-1])["ok"] == 4
    assert json.loads(run("status", "q").stdout)["pages"]["ok"] == 4
    assert run("status", "../q").exit_code != 0 and run("detect", "nope").exit_code != 0
    imported = run("import", "q")
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.stdout) == {"run": "q", "pages": {"ok": 4}}
    assert json.loads(run("import", "q").stdout) == {"run": "q", "pages": {"ok": 4}}
    # the next queue holds only the pages without a finding for their text
    assert json.loads(run("queue", "again").stdout)["pages"] == 0
    assert json.loads(run("queue", "everything", "--all").stdout)["pages"] == 4
    with closing(sqlite3.connect(settings.privacy_db)) as db, db:
        db.execute("UPDATE findings SET text_sha256 = ? WHERE page = 2", ("0" * 64,))
    assert json.loads(run("queue", "changed").stdout)["pages"] == 1
    settings_json = json.loads(
        (settings.privacy_dir / "queues" / "changed" / "settings.json").read_text()
    )
    assert len(settings_json["privacy_db_sha256"]) == 64
    with closing(sqlite3.connect(settings.privacy_db)) as db:
        spans, detail = db.execute(
            "SELECT spans, detail FROM findings WHERE sha256 = ? AND page = 2", (A,)
        ).fetchone()
        assert json.loads(spans) == [["private_person", 4, 15, 0.9123]]
        assert json.loads(detail)["worker"].startswith("2@")
        assert db.execute("SELECT name, pages FROM runs").fetchall() == [("q", 4)]


def test_redaction_marks_names_addresses_and_emails_and_keeps_the_rest():
    text = (
        "Herr Hans Muster, Dorfstrasse 4, Tel. 044 123 45 67, am 3. März 2026, www.dorf.ch, "
        "Konto 4122, hans@example.ch"
    )

    def span(label, words, score=0.9):
        start = text.index(words)
        return [label, start, start + len(words), score]

    spans = [
        span("private_url", "www.dorf.ch"),
        span("private_person", "Hans Muster", 0.31),  # any score counts
        span("private_person", "Muster"),  # inside the span before
        span("private_address", "Dorfstrasse 4"),
        span("private_phone", "044 123 45 67"),
        span("private_email", "hans@example.ch"),
        span("private_date", "3. März 2026"),
        span("account_number", "4122"),
    ]
    redacted, marks = redact.redact(text, spans)
    assert redacted == (
        "Herr \uff3bname\uff3d, \uff3baddress\uff3d, Tel. 044 123 45 67, am 3. März 2026, "
        "www.dorf.ch, Konto 4122, \uff3bemail\uff3d"
    )
    assert marks == {"private_person": 1, "private_address": 1, "private_email": 1}
    assert redact.redact(text, []) == (text, {})
    with pytest.raises(ValueError, match="no rule"):
        redact.redact(text, [["passport", 0, 4, 0.9]])


def test_what_the_check_found_once_is_replaced_wherever_else_it_stands_in_the_document():
    name, address, email = (
        redact.MARKS[label] for label in ("private_person", "private_address", "private_email")
    )
    first = (
        "Baugesuch von Hans Muster, Dorfstrasse 4, hans@example.ch. "
        "Gemeinderat Peter Keller (SVP) aus Wädenswil."
    )
    later = (
        "Herr Muster zieht das Gesuch zurück; Musters Nachbar Hans Peter Muster auch.\n"
        "Muster\nHans antwortet. Gemeinderat Keller stimmt zu. Die SVP und Wädenswil, "
        "Dorfstrasse 4, hans@example.ch. Musterhaus bleibt."
    )

    def span(label, words):
        start = first.index(words)
        return [label, start, start + len(words), 0.9]

    spans = [
        span("private_person", "Hans Muster"),
        span("private_address", "Dorfstrasse 4"),
        span("private_email", "hans@example.ch"),
        # a span that runs over a party and a commune: only the names count
        span("private_person", "Peter Keller (SVP) aus Wädenswil"),
    ]
    others = redact.mentions([(first, spans), (later, [])], keep=frozenset({"Wädenswil"}))
    assert redact.redact(first, spans, others) == (
        f"Baugesuch von {name}, {address}, {email}. Gemeinderat {name}.",
        {"private_person": 2, "private_address": 1, "private_email": 1},
    )
    redacted, marks = redact.redact(later, [], others)
    assert redacted == (
        f"Herr {name} zieht das Gesuch zurück; {name} Nachbar {name} {name} {name} auch.\n"
        f"{name} antwortet. Gemeinderat {name} stimmt zu. Die SVP und Wädenswil, {address}, "
        f"{email}. Musterhaus bleibt."
    )
    assert marks == {"private_person": 7, "private_address": 1, "private_email": 1}
    assert redact.mentions([(later, [])]) == {}


def test_dates_of_birth_the_names_before_them_and_mobile_numbers_are_found_by_their_form():
    name, date, phone = (
        redact.MARKS[label] for label in ("private_person", "birth_date", "mobile_phone")
    )
    text = (
        "Gesuchsteller (geb. 12.03.1956), née le 3 mars 1950, Natel 079 123 45 67 oder "
        "+41 79 123 45 68, Tel. 044 123 45 67, Konto 0790.3100.00, Total -123'076 23410.50, "
        "Sitzung vom 12.03.2026, geboren am 1. Januar 1960.\n"
        "Leopold, geboren am 3. Juni 1950; Schielke Ronald, geb. 4. Februar 1960; "
        "Hans Muster (geb. 1956)."
    )
    redacted = (
        f"{name} (geb. {date}), née le {date}, Natel {phone} oder {phone}, "
        "Tel. 044 123 45 67, Konto 0790.3100.00, Total -123'076 23410.50, "
        f"Sitzung vom 12.03.2026, geboren am {date}.\n"
        f"{name}, geboren am {date}; {name}, geb. {date}; {name} (geb. 1956)."
    )
    marks = {"birth_date": 5, "mobile_phone": 2, "private_person": 4}
    assert redact.redact(text, []) == (redacted, marks)


def test_names_listed_with_a_year_of_birth_are_found_by_their_form():
    name = redact.MARKS["private_person"]
    text = (
        "L'attinenza comunale di Vacallo al signor Bruno Rossi Dias, 2003, celibe, di "
        "nazionalita portoghese; alla signora Anna Verdi, 1951, coniugata; Caterina Bianchi nata "
        "Neri, 1961, coniugata; al signor Marco Gialli, 1979, alle condizioni.\n"
        "Comune di Vacallo, 2020, preventivo; Egregio signor Presidente, 12 membri."
    )
    redacted = (
        f"L'attinenza comunale di Vacallo al signor {name}, 2003, celibe, di "
        f"nazionalita portoghese; alla signora {name}, 1951, coniugata; {name}, 1961, coniugata; "
        f"al signor {name}, 1979, alle condizioni.\n"
        "Comune di Vacallo, 2020, preventivo; Egregio signor Presidente, 12 membri."
    )
    assert redact.redact(text, []) == (redacted, {"private_person": 4})


def test_places_titles_and_ordinary_words_are_not_taken_for_names_elsewhere():
    name, address = redact.MARKS["private_person"], redact.MARKS["private_address"]
    first = "Mme Anne Page, Bahnhofstrasse 5, 3018 Bern, Luzern; Wald Forst-Zillis."
    later = (
        "Mme Dupont et Anne. Page 2 de la page. Bahnhofstrasse 5 bleibt. 3018 Bern, Luzern, "
        "Forst-Zillis-Schamserberg."
    )

    def span(label, words):
        start = first.index(words)
        return [label, start, start + len(words), 0.9]

    spans = [
        span("private_person", "Mme Anne Page"),
        span("private_address", "Bahnhofstrasse 5"),
        span("private_address", "3018 Bern"),  # a postcode and a commune
        span("private_address", "Luzern"),  # a place taken for an address
        span("private_person", "Forst-Zillis"),
    ]
    keep = frozenset({"Bern", "Luzern", "Zillis-Reischen", "Zillis", "Reischen"})
    others = redact.mentions([(first, spans), (later, [])], keep)
    assert redact.redact(later, [], others)[0] == (
        f"Mme Dupont et {name}. Page 2 de la page. {address} bleibt. 3018 Bern, Luzern, "
        "Forst-Zillis-Schamserberg."
    )
