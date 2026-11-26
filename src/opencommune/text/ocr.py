"""OCR: which pages are read (`choose`), and one worker that claims chunks of a queue, renders their
pages and sends them to PaddleOCR-VL.

`choose` takes the pages in scope (opencommune.scope) without usable text that no OCR run has
answered yet.

The pages are read by PaddleX's PaddleOCR-VL-1.6 pipeline, run as PaddleX's local service with the
model behind a vLLM server: a layout model (PP-DocLayoutV3) finds the blocks of a page, PaddleOCR-VL
reads each block, and the service returns the page as Markdown (tables as HTML). The queue records
the options sent with every request (`OPTIONS`): the page turned upright first (orientation
classification; no unwarping), text in image blocks read too, charts and seals not read, nothing
left out of the Markdown (headers, footers, footnotes and page numbers stay), at most 8,192 tokens
per block, temperature 0.

Pages are rendered with PyMuPDF at 144 dpi, the scale at which PaddleX renders PDF pages (2.0), and
at most MAX_PIXELS, then sent in batches as one PDF of images per request, each page sized so that
the service's rendering gives back the same pixels. The service reads one request at a time and
batches its pages internally, so a few requests in flight keep it busy.

Renderer processes keep pages ready. The next chunk is claimed as soon as the current one is handed
to the renderers, so the service does not wait for the last pages of a chunk. Each answer is
appended to its chunk's result file at once.

Each PDF is checked against its name (SHA-256) before its pages are rendered; a missing or changed
file is a render error. A page that crashes or hangs its renderer is rendered again on its own, and
its error is recorded if it fails again. A request the service answers with an error is sent again
page by page, so that only the page that causes it fails; a page the service refuses (HTTP 4xx) is
final ("rejected"). Other request errors are tried three times here, then left for a later holder
of the chunk (queue.MAX_TRIES).

The worker stops when no chunk is left for it, at `until`, on SIGTERM, or when the service stops
answering: after MAX_FAILURES_IN_A_ROW failed requests, or at once when a request gets no answer for
REQUEST_TIMEOUT. It releases its unfinished chunks on the way out.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import multiprocessing
import queue as queues
import signal
import socket
import threading
import time
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import httpx
import pymupdf

from .. import scope
from .queue import Claim, Queue

log = logging.getLogger(__name__)

MODEL = "PaddleOCR-VL-1.6-0.9B"
PIPELINE = "PaddleOCR-VL-1.6"  # PaddleX's pipeline of that name, layout model PP-DocLayoutV3
DPI = 144  # PaddleX renders PDF pages at scale 2.0
MAX_PIXELS = 25_000_000  # larger pages (beyond A1 at 144 dpi) are rendered smaller
SERVICE_SCALE = 2.0  # the scale at which the service renders the PDFs we send
OPTIONS = {
    "useDocOrientationClassify": True,
    "useDocUnwarping": False,
    "useLayoutDetection": True,
    "useChartRecognition": False,
    "useSealRecognition": False,
    "useOcrForImageBlock": True,
    "mergeLayoutBlocks": True,
    "markdownIgnoreLabels": [],
    "maxNewTokens": 8192,
    "temperature": 0.0,
    "prettifyMarkdown": True,
    "restructurePages": False,
    "returnMarkdownImages": False,
    "visualize": False,
}
BATCH_WAIT = 5  # seconds a request waits for more rendered pages before it is sent smaller
REQUEST_TIMEOUT = 1200  # seconds without an answer: the service hangs (a request takes 1-2 min)
RENDER_TIMEOUT = 300  # seconds; a few vector plans never finish rendering
MAX_FAILURES_IN_A_ROW = 25  # requests; the service is gone: stop rather than fail every page
RETRY_SECONDS = 5  # wait before the second try, twice that before the third
HEARTBEAT_SECONDS = 60
REPORT_SECONDS = 300
SERVER_FAILED = "the server stopped answering"
WORKER_FAILED = "the worker failed; see the log"
FAILURES = (SERVER_FAILED, WORKER_FAILED)


def reading_settings() -> dict:
    """How the pages of a queue are read; the queue records it and its workers follow it."""
    return {
        "model": MODEL,
        "pipeline": PIPELINE,
        "dpi": DPI,
        "max_pixels": MAX_PIXELS,
        "options": OPTIONS,
    }


def choose(
    serving_db: Path, text_db: Path, first: Sequence[str]
) -> tuple[list[tuple[str, int]], dict]:
    """The pages to read, in reading order: pages of a `first` section type, then the rest, each
    by document and page; and the counts of the pages in scope."""
    chosen: dict[tuple[str, int], bool] = {}
    counts = dict.fromkeys(("in_scope", "with_text", "read_before", "without_any_text"), 0)
    for page in scope.pages(serving_db, text_db):
        counts["in_scope"] += 1
        if page.usable:
            counts["with_text"] += 1
        elif page.source == "ocr":  # an OCR answer with too little text: a blank page
            counts["read_before"] += 1
        else:
            chosen[(page.sha256, page.page)] = bool(set(page.types) & set(first))
            counts["without_any_text"] += page.raw is None
    pages = sorted(chosen, key=lambda key: (not chosen[key], key))
    return pages, {
        **{f"pages_{key}": value for key, value in counts.items()},
        "pages_first": sum(chosen.values()),
        "documents": len({sha for sha, _ in pages}),
    }


def _quiet() -> None:
    pymupdf.TOOLS.mupdf_display_errors(False)


def render(path: str, sha: str, page: int, dpi: int, max_pixels: int) -> dict:
    """One page as PNG at `dpi`, or at about `max_pixels` where that would be more."""
    started = time.perf_counter()
    try:
        with pymupdf.open(path) as doc:
            pdf_page = doc[page - 1]
            zoom = dpi / 72
            pixels = pdf_page.rect.width * pdf_page.rect.height * zoom * zoom
            if pixels > max_pixels:
                zoom *= (max_pixels / pixels) ** 0.5
            pixmap = pdf_page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            png, width, height = pixmap.tobytes("png"), pixmap.width, pixmap.height
    except Exception as error:  # noqa: BLE001 - recorded per page
        return {"sha256": sha, "page": page, "error": f"{type(error).__name__}: {error}"}
    return {
        "sha256": sha,
        "page": page,
        "png": png,
        "width": width,
        "height": height,
        "render_s": round(time.perf_counter() - started, 3),
    }


def document(pages: list[dict]) -> bytes:
    """The rendered pages as one PDF of images, each page sized so that rendering it at
    SERVICE_SCALE gives back its pixels."""
    with pymupdf.open() as doc:
        for page in pages:
            sheet = doc.new_page(
                width=page["width"] / SERVICE_SCALE, height=page["height"] / SERVICE_SCALE
            )
            sheet.insert_image(sheet.rect, stream=page["png"])
        return doc.tobytes()


def _pool(workers: int) -> ProcessPoolExecutor:
    # spawn, not fork: the process runs hundreds of threads when a pool is restarted
    context = multiprocessing.get_context("spawn")
    return ProcessPoolExecutor(workers, mp_context=context, initializer=_quiet)


def _kill(pool: ProcessPoolExecutor) -> None:
    for process in list((getattr(pool, "_processes", None) or {}).values()):
        process.kill()
    pool.shutdown(wait=False, cancel_futures=True)


def _render_alone(path: str, sha: str, page: int, dpi: int, max_pixels: int) -> dict:
    """One page in a fresh process, so a crash or hang is pinned on that page alone."""
    pool = _pool(1)
    try:
        return pool.submit(render, path, sha, page, dpi, max_pixels).result(timeout=RENDER_TIMEOUT)
    except BrokenProcessPool:
        return {"sha256": sha, "page": page, "error": "renderer crashed"}
    except TimeoutError:
        return {"sha256": sha, "page": page, "error": "render timeout"}
    finally:
        _kill(pool)


class _Refused(Exception):
    """The service answered a request with an error."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class _Held:
    """A chunk this worker holds, and how far it has got with it."""

    claim: Claim
    out: TextIO
    handed: int = 0  # pages given to the renderers or answered without them
    answered: int = 0  # of those, answered (or dropped, once the chunk was lost)
    all_handed: bool = False
    lost: bool = False
    closed: bool = False


class Worker:
    def __init__(
        self,
        queue: Queue,
        raw: Path,
        client: httpx.Client,
        owner: str,
        concurrency: int,
        pages_per_request: int,
        render_workers: int,
        until: float | None,
    ):
        self.queue, self.raw, self.client, self.owner = queue, raw, client, owner
        self.dpi = queue.settings["dpi"]
        self.max_pixels = queue.settings["max_pixels"]
        self.options = queue.settings["options"]
        self.concurrency, self.pages_per_request = concurrency, pages_per_request
        self.render_workers, self.until = render_workers, until
        self.pages: queues.Queue = queues.Queue(maxsize=concurrency * pages_per_request)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.pdf_lock = threading.Lock()  # PyMuPDF is not thread-safe
        self.held: list[_Held] = []
        self.counts: Counter = Counter()
        self.failures_in_a_row = 0
        self.checked: dict[str, str | None] = {}
        self.reason = "no chunk left"

    # --- chunks -----------------------------------------------------------------------------

    def _check(self, sha: str) -> str | None:
        """Why the PDF cannot be read, or None. Once per document and worker."""
        if sha not in self.checked:
            try:
                with (self.raw / f"{sha}.pdf").open("rb") as file:
                    digest = hashlib.file_digest(file, "sha256").hexdigest()
            except FileNotFoundError:
                self.checked[sha] = "no such file"
            else:
                self.checked[sha] = None if digest == sha else "the file's SHA-256 is not its name"
        return self.checked[sha]

    def _take(self, claim: Claim) -> tuple[_Held, list[tuple[str, int]]]:
        finished = self.queue.finished(claim.chunk)
        todo = [key for key in self.queue.pages(claim.chunk) if key not in finished]
        held = _Held(claim, (self.queue.root / "out" / f"{claim.chunk}.jsonl").open("a"))
        with self.lock:
            self.held.append(held)
        ready = []
        for sha, page in todo:
            problem = self._check(sha)
            if problem is None:
                ready.append((sha, page))
                continue
            with self.lock:
                held.handed += 1
            self._write(
                held, {"sha256": sha, "page": page, "status": "render_error", "error": problem}
            )
        log.info(
            "chunk %s: %d pages to read, %d answered before", claim.chunk, len(ready), len(finished)
        )
        return held, ready

    def _jobs(self):
        """(chunk, sha256, page) in order; the next chunk is claimed once one is handed out."""
        while not self.stop.is_set():
            claim = self.queue.claim(self.owner)
            if claim is None:
                return
            held, ready = self._take(claim)
            for sha, page in ready:
                if self.stop.is_set() or held.lost:
                    break
                with self.lock:
                    held.handed += 1
                yield held, sha, page
            with self.lock:
                held.all_handed = True
                self._close_if_complete(held)

    def _close_if_complete(self, held: _Held) -> None:
        """With the lock held: once every page handed out is answered, finish or release."""
        if held.closed or not held.all_handed or held.answered < held.handed:
            return
        held.closed = True
        held.out.close()
        self.held.remove(held)
        if held.lost:
            return
        if set(self.queue.pages(held.claim.chunk)) <= self.queue.finished(held.claim.chunk):
            self.queue.finish(held.claim)
            self.counts["chunks_finished"] += 1
        else:  # request errors to try again later
            self.queue.release(held.claim)
            self.counts["chunks_released"] += 1

    def _write(self, held: _Held, record: dict) -> None:
        with self.lock:
            if held.closed:  # released at a stop
                return
            if not held.lost:
                record = {**record, "worker": self.owner, "at": round(time.time(), 1)}
                held.out.write(json.dumps(record, ensure_ascii=False) + "\n")
                held.out.flush()
                self.counts[record["status"]] += 1
            held.answered += 1
            self._close_if_complete(held)

    def _beat(self) -> None:
        with self.lock:
            held = list(self.held)
        for item in held:
            if not item.lost and not self.queue.touch(item.claim):
                log.warning("chunk %s was taken over by another worker", item.claim.chunk)
                with self.lock:
                    item.lost = True
                    self._close_if_complete(item)

    def _release_all(self) -> None:
        with self.lock:
            for held in self.held:
                held.closed = True
                held.out.close()
                if not held.lost:
                    self.queue.release(held.claim)
                    self.counts["chunks_released"] += 1
            self.held.clear()

    def _halt(self, reason: str) -> None:
        with self.lock:
            if self.stop.is_set():
                return
            self.reason = reason
            self.stop.set()
        log.warning("stopping: %s", reason)

    # --- rendering and requests ---------------------------------------------------------------

    def _produce(self) -> None:
        """Render pages in worker processes into the page queue as they finish; a sentinel per
        consumer at the end. A page that crashes or hangs its renderer takes the pool down with
        it: the pool is then restarted and every page it held is rendered on its own, so only the
        culprit becomes an error."""
        pool = _pool(self.render_workers)
        pending: set = set()
        jobs = self._jobs()
        exhausted = False
        size = (self.dpi, self.max_pixels)
        try:
            while (not exhausted or pending) and not self.stop.is_set():
                suspects = []
                while not exhausted and len(pending) < self.render_workers * 4:
                    try:
                        held, sha, page = next(jobs)
                    except StopIteration:
                        exhausted = True
                        break
                    path = str(self.raw / f"{sha}.pdf")
                    try:
                        future = pool.submit(render, path, sha, page, *size)
                    except BrokenProcessPool:
                        suspects.append((held, sha, page))
                        break
                    future.job, future.submitted = (held, sha, page), time.monotonic()
                    pending.add(future)
                finished, pending = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                for future in finished:
                    try:
                        self.pages.put((future.job[0], future.result()))
                    except BrokenProcessPool:
                        suspects.append(future.job)
                overdue = any(time.monotonic() - f.submitted > RENDER_TIMEOUT for f in pending)
                if suspects or overdue:
                    suspects += [f.job for f in pending]
                    _kill(pool)
                    pool, pending = _pool(self.render_workers), set()
                    for held, sha, page in suspects:
                        result = _render_alone(str(self.raw / f"{sha}.pdf"), sha, page, *size)
                        self.pages.put((held, result))
        except Exception:
            log.exception("renderer loop failed")
            self._halt(WORKER_FAILED)
        finally:
            _kill(pool)
            for _ in range(self.concurrency):
                self.pages.put(None)

    def _batches(self):
        """Lists of (chunk, rendered page) of at most `pages_per_request`; a batch waits up to
        BATCH_WAIT for more pages. Ends at this consumer's sentinel."""
        while (item := self.pages.get()) is not None:
            batch, deadline = [item], time.monotonic() + BATCH_WAIT
            while len(batch) < self.pages_per_request:
                try:
                    item = self.pages.get(timeout=max(0.0, deadline - time.monotonic()))
                except queues.Empty:
                    break
                if item is None:
                    yield batch
                    return
                batch.append(item)
            yield batch

    def _consume(self) -> None:
        for batch in self._batches():
            if self.stop.is_set():
                continue
            try:
                rendered = []
                for held, page in batch:
                    key = {"sha256": page["sha256"], "page": page["page"]}
                    if "error" in page:
                        self._write(held, {**key, "status": "render_error", "error": page["error"]})
                    else:
                        rendered.append((held, page))
                if rendered:
                    answers = self._ask([page for _, page in rendered])
                    for (held, page), answer in zip(rendered, answers, strict=True):
                        self._write(
                            held, {"sha256": page["sha256"], "page": page["page"], **answer}
                        )
            except Exception:
                log.exception("writing the answers to %d pages failed", len(batch))
                self._halt(WORKER_FAILED)

    def _answered(self, ok: bool) -> None:
        with self.lock:
            self.failures_in_a_row = 0 if ok else self.failures_in_a_row + 1
            failed = self.failures_in_a_row >= MAX_FAILURES_IN_A_ROW
        if failed:
            self._halt(SERVER_FAILED)

    def _ask(self, pages: list[dict]) -> list[dict]:
        """One answer per page. A request the service answers with an error is sent again page by
        page; a single page it refuses (HTTP 4xx) is final."""
        with self.pdf_lock:
            pdf = document(pages)
        body = {"file": base64.b64encode(pdf).decode(), "fileType": 0, **self.options}
        sent = time.monotonic()
        refused: _Refused | None = None
        message = ""
        for attempt in range(3):
            if attempt and self.stop.wait(RETRY_SECONDS * attempt):
                break
            try:
                response = self.client.post(
                    "/layout-parsing", json=body, timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=30)
                )
                if response.is_error:
                    raise _Refused(
                        response.status_code, f"HTTP {response.status_code}: {response.text[:500]}"
                    )
                results = _results(response.json(), len(pages))
            except httpx.TimeoutException as error:
                message, refused = f"no answer within {REQUEST_TIMEOUT} s: {error}", None
                self._halt(SERVER_FAILED)  # a hung service would hold the GPU until the job ends
                break
            except httpx.HTTPError as error:
                message, refused = f"{type(error).__name__}: {error}", None
                continue
            except _Refused as error:
                message, refused = str(error), error
                if error.status < 500:
                    break
                continue
            except (ValueError, KeyError, IndexError, TypeError) as error:
                message, refused = f"unexpected answer: {type(error).__name__}: {error}", None
                continue
            self._answered(True)
            seconds = round(time.monotonic() - sent, 3)
            return [
                {
                    "status": "ok",
                    "width": page["width"],
                    "height": page["height"],
                    "render_s": page["render_s"],
                    "request_s": seconds,
                    "request_pages": len(pages),
                    **result,
                }
                for page, result in zip(pages, results, strict=True)
            ]
        if refused is not None and len(pages) > 1 and not self.stop.is_set():
            log.warning("%d pages together: %s; sending them one by one", len(pages), message)
            return [answer for page in pages for answer in self._ask([page])]
        if refused is not None and refused.status < 500:
            self._answered(True)
            return [{"status": "rejected", "error": message}]
        self._answered(False)
        return [{"status": "request_error", "error": message}] * len(pages)

    # --- the run ------------------------------------------------------------------------------

    def run(self) -> dict:
        previous = signal.signal(signal.SIGTERM, lambda *_: self._halt("SIGTERM"))
        producer = threading.Thread(target=self._produce, daemon=True)
        consumers = [
            threading.Thread(target=self._consume, daemon=True) for _ in range(self.concurrency)
        ]
        producer.start()
        for thread in consumers:
            thread.start()
        started = last_beat = last_report = time.monotonic()
        try:
            while not self.stop.is_set() and any(thread.is_alive() for thread in consumers):
                time.sleep(1)
                if self.until is not None and time.time() >= self.until:
                    self._halt("time is up")
                now = time.monotonic()
                if now - last_beat >= HEARTBEAT_SECONDS:
                    last_beat = now
                    self._beat()
                if now - last_report >= REPORT_SECONDS:
                    last_report = now
                    log.info(
                        "%.0f s: %s, %.2f pages/s",
                        now - started,
                        dict(self.counts),
                        self.counts["ok"] / (now - started),
                    )
        finally:
            signal.signal(signal.SIGTERM, previous)
            self._release_all()
        return {"stopped": self.reason, "seconds": round(time.monotonic() - started), **self.counts}


def _results(answer: dict, pages: int) -> list[dict]:
    """Text, blocks and orientation of each page from the service's answer."""
    if answer.get("errorCode", 0) != 0:
        raise ValueError(f"error {answer['errorCode']}: {answer.get('errorMsg')}")
    items = answer["result"]["layoutParsingResults"]
    if len(items) != pages:
        raise ValueError(f"{len(items)} results for {pages} pages")
    results = []
    for item in items:
        pruned = item.get("prunedResult") or {}
        blocks = pruned.get("parsing_res_list") or []
        results.append(
            {
                "angle": (pruned.get("doc_preprocessor_res") or {}).get("angle"),
                "blocks": [[block.get("block_label"), block.get("block_bbox")] for block in blocks],
                "text": item["markdown"]["text"],
            }
        )
    return results


def run(
    queue: Queue,
    raw: Path,
    url: str,
    name: str,
    concurrency: int,
    pages_per_request: int,
    render_workers: int,
    until: float | None,
) -> dict:
    """Work on the queue with the PaddleX service at `url`."""
    if queue.settings.get("model") != MODEL:
        raise RuntimeError(
            f"this code reads with {MODEL}; the queue wants {queue.settings['model']}"
        )
    owner = f"{name}@{socket.gethostname()}"
    record = queue.root / "workers" / name / "worker.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    source = hashlib.sha256()
    for module in ("ocr.py", "queue.py"):
        source.update((Path(__file__).parent / module).read_bytes())
    facts = {
        "owner": owner,
        "url": url,
        "raw": str(raw),
        "concurrency": concurrency,
        "pages_per_request": pages_per_request,
        "render_workers": render_workers,
        "until": until,
        "pymupdf": pymupdf.__version__,
        "httpx": httpx.__version__,
        "source_sha256": source.hexdigest(),
        "started": time.time(),
    }
    record.write_text(json.dumps(facts, indent=2) + "\n")
    with httpx.Client(
        base_url=url,
        timeout=httpx.Timeout(None, connect=30),  # a request of many pages may run long
        limits=httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency),
        trust_env=False,
    ) as client:
        client.get("/health", timeout=60).raise_for_status()
        worker = Worker(
            queue, raw, client, owner, concurrency, pages_per_request, render_workers, until
        )
        result = worker.run()
    record.write_text(json.dumps({**facts, "finished": time.time(), **result}, indent=2) + "\n")
    return result
