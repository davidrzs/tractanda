import base64
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pymupdf
import pytest
from typer.testing import CliRunner

from opencommune import scope
from opencommune.cli import app
from opencommune.config import settings
from opencommune.text import layer, ocr, store
from opencommune.text.queue import MAX_TRIES, STALE_SECONDS, Queue
from tests import scope_data


def stored_pdf(raw, texts):
    """A PDF with one page per text (empty: no text layer), named by its SHA-256 as in raw/."""
    doc = pymupdf.open()
    for text in texts:
        page = doc.new_page(width=595, height=842)
        if text:
            page.insert_text((72, 72), text, fontsize=12)
    data = doc.tobytes()
    sha = hashlib.sha256(data).hexdigest()
    (raw / f"{sha}.pdf").write_bytes(data)
    return sha


def make_queue(tmp_path, pages, chunk_pages=2):
    chosen = {**ocr.reading_settings(), "dpi": 36, "created": "2026-11-14T16:00:00+00:00"}
    items = [{"sha256": sha, "page": page} for sha, page in pages]
    return Queue.create(tmp_path / "queue", items, chunk_pages, chosen)


@contextmanager
def paddlex(mode="ok"):
    """A stand-in for PaddleX's PaddleOCR-VL service on loopback. Yields its URL and, per request,
    its body and the PDF sent. "reject-batches" refuses requests of more than one page."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(200, {"errorCode": 0, "errorMsg": "Healthy"})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            pdf = base64.b64decode(body.pop("file"))
            seen.append((body, pdf))
            pages = len(re.findall(rb"/Type\s*/Page(?!s)", pdf))  # no PyMuPDF off the main thread
            if mode == "reject" or (mode == "reject-batches" and pages > 1):
                self._send(422, {"errorCode": 422, "errorMsg": "Invalid input file"})
            elif mode == "fail":
                self._send(500, {"errorCode": 500, "errorMsg": "engine dead"})
            else:
                if mode in ("slow", "hang"):
                    time.sleep(3)
                block = {"block_label": "text", "block_bbox": [1, 2, 3, 4], "block_content": "x"}
                page = {
                    "prunedResult": {
                        "parsing_res_list": [block],
                        "doc_preprocessor_res": {"angle": 90},
                    },
                    "markdown": {"text": "Gemeinde", "images": None},
                }
                result = {"layoutParsingResults": [page] * pages, "dataInfo": {}}
                self._send(200, {"logId": "x", "errorCode": 0, "errorMsg": "", "result": result})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()


def serving_db(path, page_counts, sections):
    """The two tables of the serving database that `layer.section_pages` reads."""
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("create table documents (sha256 text, page_count int)")
        conn.execute(
            "create table sections (sha256 text, type text, first_page int, last_page int)"
        )
        conn.executemany("insert into documents values (?, ?)", page_counts.items())
        conn.executemany("insert into sections values (?, ?, ?, ?)", sections)
        conn.commit()
    return path


def test_section_pages_widen_sections_within_the_document(tmp_path):
    sections = [
        ("a", "budget", 1, 3),
        ("a", "vote_result", 5, 5),
        ("a", "minutes", 8, 8),
        ("b", "accounts", 3, 3),
    ]
    db = serving_db(tmp_path / "serving.db", {"a": 10, "b": 3}, sections)
    pages = layer.section_pages(db, ["budget", "vote_result", "accounts"], 1)
    budget, vote = ["budget"], ["vote_result"]
    assert pages == {
        "a": {1: budget, 2: budget, 3: budget, 4: ["budget", "vote_result"], 5: vote, 6: vote},
        "b": {2: ["accounts"], 3: ["accounts"]},
    }


def test_measure_reads_the_text_layer_and_goes_on_where_it_stopped(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    sha = stored_pdf(raw, ["Budget 2027 Gemeinde", "", "12"])
    out = tmp_path / "pages.jsonl"
    pages = {sha: {1: ["budget"], 2: ["budget"], 3: ["budget"]}}
    assert layer.measure(pages, raw, out, 1) == {"documents": 1, "pages": 3, "unreadable": 0}
    records = {r["page"]: r for r in map(json.loads, out.read_text().splitlines())}
    assert [records[p]["chars"] for p in (1, 2, 3)] == [len("Budget2027Gemeinde"), 0, 2]
    assert records[1]["sections"] == ["budget"] and records[1]["text"].startswith("Budget 2027")
    assert layer.measure(pages, raw, out, 1)["documents"] == 0
    assert layer.measure({"f" * 64: {1: ["budget"]}}, raw, out, 1)["unreadable"] == 1


def test_ocr_reads_the_pages_in_scope_without_usable_text(tmp_path):
    a = scope_data.A
    serving = scope_data.serving_db(tmp_path / "serving.db")
    text = scope_data.text_db(tmp_path / "text.db")
    pages, counts = ocr.choose(serving, text, ["proposal"])
    assert pages == [(a, 7), (a, 3)]
    assert counts == {
        "pages_in_scope": 6,
        "pages_with_text": 4,
        "pages_read_before": 0,
        "pages_without_any_text": 1,
        "pages_first": 1,
        "documents": 1,
    }
    blank = [*scope_data.PAGES, (a, 3, "ocr:q", "")]  # OCR found nothing on page 3
    pages, counts = ocr.choose(serving, scope_data.text_db(tmp_path / "again.db", blank), [])
    assert pages == [(a, 7)] and counts["pages_read_before"] == 1


def test_claims_are_exclusive_and_a_stale_claim_is_taken_over(tmp_path):
    queue = make_queue(tmp_path, [("a", 1), ("a", 2), ("b", 1)])
    assert queue.chunks() == ["00000", "00001"]
    first, second = queue.claim("w1"), queue.claim("w2")
    assert (first.chunk, second.chunk) == ("00000", "00001")
    assert queue.claim("w3") is None
    old = time.time() - STALE_SECONDS - 1
    os.utime(first.path, (old, old))
    taken = queue.claim("w3")
    assert (taken.chunk, taken.path.name) == ("00000", "00000.2")
    assert not queue.touch(first) and queue.touch(taken)
    queue.release(second)
    assert queue.claim("w4").chunk == "00001"
    queue.finish(taken)
    assert queue.is_done("00000") and queue.claim("w5") is None
    assert queue.status()["chunks"] == {"total": 2, "done": 1, "held": 1}


def test_a_page_counts_as_answered_after_the_last_request_error(tmp_path):
    queue = make_queue(tmp_path, [("a", 1), ("a", 2)])
    out = queue.root / "out" / "00000.jsonl"
    records = [{"sha256": "a", "page": 1, "status": "request_error"}] * (MAX_TRIES - 1)
    records.append({"sha256": "a", "page": 2, "status": "rejected"})
    out.write_text("".join(json.dumps(r) + "\n" for r in records) + '{"cut off')
    assert queue.finished("00000") == {("a", 2)}
    with out.open("a") as file:
        file.write("\n" + json.dumps({"sha256": "a", "page": 1, "status": "request_error"}) + "\n")
    assert queue.finished("00000") == {("a", 1), ("a", 2)}


def test_a_worker_answers_every_page_and_finishes_every_chunk(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    a = stored_pdf(raw, ["", "x", ""])
    b = stored_pdf(raw, [""])
    (raw / f"{'e' * 64}.pdf").write_bytes((raw / f"{b}.pdf").read_bytes())  # wrong name
    pages = [(a, 1), (a, 2), (a, 3), (b, 1), ("e" * 64, 1), ("f" * 64, 1)]
    queue = make_queue(tmp_path, pages)
    with paddlex() as (url, seen):
        result = ocr.run(queue, raw, url, "one", 2, 64, 2, None)
        again = ocr.run(queue, raw, url, "two", 2, 64, 2, None)
    assert result["stopped"] == "no chunk left"
    assert (result["ok"], result["render_error"], result["chunks_finished"]) == (4, 2, 3)
    assert all(queue.is_done(chunk) for chunk in queue.chunks())
    assert again["stopped"] == "no chunk left" and "ok" not in again
    body = seen[0][0]
    assert {key: body[key] for key in ocr.OPTIONS} == ocr.OPTIONS and body["fileType"] == 0
    assert body["maxNewTokens"] == 8192 and body["markdownIgnoreLabels"] == []
    sent = [pymupdf.open(stream=pdf, filetype="pdf") for _, pdf in seen]
    assert sum(len(doc) for doc in sent) == 4
    sheet = sent[0][0]
    width, height = sheet.get_images()[0][2:4]
    assert (width, height) == (298, 421)  # A4 at 36 dpi
    assert (sheet.rect.width, sheet.rect.height) == (width / 2, height / 2)  # rendered at scale 2
    answer = next(r for r in queue.answers("00000") if r["status"] == "ok")
    assert (answer["text"], answer["angle"]) == ("Gemeinde", 90)
    assert answer["blocks"] == [["text", [1, 2, 3, 4]]]
    errors = {r["sha256"]: r["error"] for r in queue.answers("00002")}
    assert errors == {"e" * 64: "the file's SHA-256 is not its name", "f" * 64: "no such file"}
    assert queue.status()["pages"] == {"total": 6, "answered": 6, "ok": 4, "render_error": 2}
    assert json.loads((queue.root / "workers" / "one" / "worker.json").read_text())["ok"] == 4


def test_pages_are_rendered_at_most_at_max_pixels(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    sha = stored_pdf(raw, ["x"])
    page = ocr.render(str(raw / f"{sha}.pdf"), sha, 1, 144, 10_000)
    assert page["width"] * page["height"] < 11_000  # a pixel more on each edge at most
    assert page["png"].startswith(b"\x89PNG")


def test_a_refused_request_is_sent_again_page_by_page(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    sha = stored_pdf(raw, ["", "", ""])
    queue = make_queue(tmp_path, [(sha, 1), (sha, 2), (sha, 3)], chunk_pages=3)
    with paddlex("reject-batches") as (url, seen):
        result = ocr.run(queue, raw, url, "one", 1, 64, 1, None)
    assert (result["stopped"], result["ok"]) == ("no chunk left", 3)
    assert [len(re.findall(rb"/Type\s*/Page(?!s)", pdf)) for _, pdf in seen] == [3, 1, 1, 1]
    assert {r["request_pages"] for r in queue.answers("00000")} == {1}


def test_rejected_pages_are_answered_and_a_failing_server_stops_the_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "RETRY_SECONDS", 0)
    monkeypatch.setattr(ocr, "MAX_FAILURES_IN_A_ROW", 2)
    raw = tmp_path / "raw"
    raw.mkdir()
    sha = stored_pdf(raw, ["", "", ""])
    queue = make_queue(tmp_path, [(sha, 1), (sha, 2), (sha, 3)], chunk_pages=3)
    with paddlex("fail") as (url, seen):
        failed = ocr.run(queue, raw, url, "one", 1, 64, 1, None)
    assert failed["stopped"] == ocr.SERVER_FAILED and len(seen) >= 3
    assert not queue.is_done("00000") and not list((queue.root / "claims").iterdir())
    with paddlex("reject") as (url, _):
        rejected = ocr.run(queue, raw, url, "two", 1, 64, 1, None)
    assert (rejected["stopped"], rejected["rejected"]) == ("no chunk left", 3)
    assert queue.is_done("00000")


def test_a_worker_stops_at_its_time_and_releases_its_chunks(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    sha = stored_pdf(raw, ["", ""])
    queue = make_queue(tmp_path, [(sha, 1), (sha, 2)])
    with paddlex("slow") as (url, _):
        result = ocr.run(queue, raw, url, "one", 2, 64, 1, time.time() + 1)
    assert result["stopped"] == "time is up" and "ok" not in result
    assert not queue.is_done("00000") and not list((queue.root / "claims").iterdir())


def test_a_service_that_does_not_answer_stops_the_worker_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "REQUEST_TIMEOUT", 1)
    raw = tmp_path / "raw"
    raw.mkdir()
    sha = stored_pdf(raw, ["", ""])
    queue = make_queue(tmp_path, [(sha, 1), (sha, 2)])
    with paddlex("hang") as (url, seen):
        result = ocr.run(queue, raw, url, "one", 1, 64, 1, None)
    assert result["stopped"] == ocr.SERVER_FAILED and len(seen) == 1
    assert not queue.is_done("00000") and not list((queue.root / "claims").iterdir())


def test_a_worker_refuses_a_queue_read_with_another_model(tmp_path):
    queue = Queue.create(tmp_path / "queue", [], 1, {"model": "another"})
    with pytest.raises(RuntimeError, match="another"):
        ocr.run(queue, tmp_path, "http://127.0.0.1:9", "one", 1, 1, 1, None)


def test_the_text_commands_name_their_passes_and_queues(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    raw = tmp_path / "raw"
    raw.mkdir()
    sha = stored_pdf(raw, ["", "Budget 2027 der Gemeinde Muster", "3"])
    scope_data.serving_db(
        tmp_path / "serving.db",
        documents=[(sha, 3, "standalone", "commune")],
        sections=[(sha, 1, "budget", "money", 1, 3)],
    )
    runner = CliRunner()

    def run(*args):
        return runner.invoke(app, ["text", *args])

    made = run("layer", "d", "--sections", "budget", "--margin", "0", "--workers", "1")
    assert made.exit_code == 0, made.output
    assert json.loads(made.stdout.splitlines()[-1]) == {"documents": 1, "pages": 3, "unreadable": 0}
    assert run("layer", "d", "--sections", "accounts", "--margin", "0").exit_code != 0
    assert run("queue", "q").exit_code != 0  # no text.db yet
    assert run("import", "--layer", "d").exit_code == 0
    made = run("queue", "q")
    assert made.exit_code == 0, made.output
    queue = Queue(tmp_path / "text" / "queues" / "q")
    assert queue.pages("00000") == [(sha, 1), (sha, 3)]
    assert queue.settings["scope"] == list(scope.TYPES)
    assert (queue.settings["pages_in_scope"], queue.settings["pages"]) == (3, 2)
    assert (queue.settings["model"], queue.settings["dpi"]) == (ocr.MODEL, 144)
    assert run("queue", "q").exit_code != 0
    shown = run("status", "q")
    assert json.loads(shown.stdout)["pages"] == {"total": 2, "answered": 0}
    assert run("status", "../q").exit_code != 0
    imported = run("import", "--queue", "q")
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.stdout)["run"] == "ocr:q"
    assert run("import").exit_code != 0
    assert (tmp_path / "text.db").exists()


def test_text_db_keeps_every_run_and_prefers_an_ocr_answer(tmp_path):
    passed = tmp_path / "layer"
    passed.mkdir()
    settings_ = {"sections": ["budget"], "margin": 1, "started": "2026-11-14T15:00:00+00:00"}
    (passed / "settings.json").write_text(json.dumps(settings_))
    common = {"sections": ["budget"], "odd": 0, "image_cover": 0.0}
    records = [
        {"sha256": "a", "page": 1, "chars": 0, "alnum": 0, "text": "", **common},
        {"sha256": "a", "page": 2, "chars": 6, "alnum": 6, "text": "Budget", **common},
        {"sha256": "a", "page": 2, "chars": 6, "alnum": 6, "text": "Budget", **common},
        {"sha256": "b", "error": "FileDataError: broken"},
    ]
    lines = "".join(json.dumps(r) + "\n" for r in records)
    (passed / "pages.jsonl").write_text(lines + '{"cut off')
    queue = make_queue(tmp_path, [("a", 1), ("a", 2)])
    out = queue.root / "out" / "00000.jsonl"
    answers = [
        {"sha256": "a", "page": 1, "status": "ok", "finish_reason": "stop", "text": "Gemeinde X"},
        {"sha256": "a", "page": 2, "status": "request_error", "error": "HTTP 500"},
    ]
    out.write_text("".join(json.dumps(r) + "\n" for r in answers))
    db = store.connect(tmp_path / "text.db")
    assert store.import_layer(db, "d", passed) == {
        "run": "layer:d",
        "pages": {"ok": 2},
        "unreadable_documents": 1,
    }
    assert store.import_queue(db, "q", queue)["pages"] == {"ok": 1, "request_error": 1}

    def chosen():
        rows = db.execute("SELECT sha256, page, source, text FROM page_text ORDER BY page")
        return [tuple(row) for row in rows]

    assert chosen() == [("a", 1, "ocr", "Gemeinde X"), ("a", 2, "layer", "Budget")]
    ocr_row = db.execute(
        "SELECT chars, detail FROM pages WHERE run = 'ocr:q' AND page = 1"
    ).fetchone()
    assert ocr_row[0] == 9 and "error" not in json.loads(ocr_row[1])
    with out.open("a") as file:
        file.write(
            json.dumps({"sha256": "a", "page": 2, "status": "ok", "text": "Budget 2027"}) + "\n"
        )
    assert store.import_queue(db, "q", queue)["pages"] == {"ok": 2}
    assert chosen() == [("a", 1, "ocr", "Gemeinde X"), ("a", 2, "ocr", "Budget 2027")]
    assert db.execute("SELECT count(*) FROM pages").fetchone()[0] == 4
