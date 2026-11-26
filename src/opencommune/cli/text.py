"""`opencommune text`: the text of each page, from the PDF's text layer or by OCR.

Passes and queues are named and live in DATA_DIR/text: `layers/<name>/` holds a text layer pass,
`queues/<name>/` an OCR queue with its answers. `import` copies either into DATA_DIR/text.db.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from ..config import settings
from .common import file_sha256, named, now, reported_errors

app = typer.Typer(
    no_args_is_help=True,
    help="The text of each page: the PDF's text layer, and OCR for the pages without one.",
)


def _named(kind: str, name: str) -> Path:
    return named(settings.text_dir / kind, name)


def _types(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


@app.command("layer")
def layer(
    name: str = typer.Argument(..., help="The pass; a rerun with the same name goes on"),
    sections: str = typer.Option(..., help="Comma-separated section types, e.g. budget,accounts"),
    margin: int = typer.Option(..., min=0, help="Pages on either side of a section to include"),
    workers: int = typer.Option(8, min=1, help="Documents read at once"),
) -> None:
    """Read the text layer of every page of the given section types with PyMuPDF.

    Writes pages.jsonl (one line per page, see opencommune.text.layer) and settings.json."""
    import pymupdf

    from ..text import layer as text_layer

    out = _named("layers", name)
    types = _types(sections)
    if not types:
        raise typer.BadParameter("name at least one section type")
    chosen = {"sections": types, "margin": margin}
    record = out / "settings.json"
    if record.exists():
        before = json.loads(record.read_text())
        if {key: before[key] for key in chosen} != chosen:
            raise typer.BadParameter(
                f"{name} holds pages of {before['sections']}, margin {before['margin']}"
            )
    else:
        out.mkdir(parents=True)
        facts = {
            **chosen,
            "serving_db": str(settings.serving_db),
            "serving_db_sha256": file_sha256(settings.serving_db),
            "raw": str(settings.raw_dir),
            "pymupdf": pymupdf.__version__,
            "started": now(),
        }
        record.write_text(json.dumps(facts, indent=2) + "\n")
    pages = text_layer.section_pages(settings.serving_db, types, margin)
    counts = text_layer.measure(
        pages, settings.raw_dir, out / "pages.jsonl", min(workers, os.cpu_count() or 1)
    )
    typer.echo(json.dumps(counts))


@app.command("queue")
def queue(
    name: str = typer.Argument(..., help="A new name; a queue is never changed"),
    first: str = typer.Option("", help="Comma-separated section types whose pages are read first"),
    chunk_pages: int = typer.Option(500, min=1, help="Pages per chunk, the unit a worker claims"),
) -> None:
    """Choose the pages for OCR and write them as a queue of chunks for `text ocr`.

    The pages are those in scope (opencommune.scope) whose text in DATA_DIR/text.db has fewer than
    scope.MIN_CHARS non-space characters, or that have none, and that no OCR run has answered."""
    from .. import scope
    from ..text import ocr as text_ocr
    from ..text.queue import Queue

    target = _named("queues", name)
    if target.exists():
        raise typer.BadParameter(f"queue {name} exists; a queue is never changed")
    if not settings.text_db.exists():
        raise typer.BadParameter("no text.db; import a text layer pass first")
    first_types = _types(first)
    pages, counts = text_ocr.choose(settings.serving_db, settings.text_db, first_types)
    chosen = {
        "created": now(),
        "rule": (
            f"pages in scope whose text has fewer than {scope.MIN_CHARS} non-space characters "
            "or none, and that no OCR run has answered"
        ),
        "scope": list(scope.TYPES),
        "first": first_types,
        "serving_db_sha256": file_sha256(settings.serving_db),
        "text_db_sha256": file_sha256(settings.text_db),
        **counts,
        **text_ocr.reading_settings(),
    }
    items = ({"sha256": sha, "page": page} for sha, page in pages)
    created = Queue.create(target, items, chunk_pages, chosen)
    typer.echo(
        json.dumps(
            {"queue": str(target), "pages": len(pages), **counts, "chunks": len(created.chunks())}
        )
    )


@app.command("ocr")
def ocr(
    name: str = typer.Argument(..., help="The queue"),
    url: str = typer.Option(..., help="The PaddleX service, e.g. http://127.0.0.1:8080"),
    worker: str | None = typer.Option(None, help="This worker's name, e.g. the Slurm job id"),
    concurrency: int = typer.Option(
        2, min=1, help="Requests in flight; the service reads one at a time, so two keep it busy"
    ),
    pages_per_request: int = typer.Option(
        64, min=1, help="Pages per request; changes speed, not output"
    ),
    render_workers: int = typer.Option(6, min=1, help="Renderer processes"),
    until: float | None = typer.Option(None, help="Unix time at which to stop"),
) -> None:
    """Work on a queue until no chunk is left for this worker, `until`, or SIGTERM.

    Reads the PDFs from DATA_DIR's raw directory. Exits with 2 when the service stopped answering
    or the worker failed."""
    import logging
    import socket
    import sys

    from ..text import ocr as text_ocr
    from ..text.queue import Queue

    root = _named("queues", name)
    if not root.is_dir():
        raise typer.BadParameter(f"no queue {name}")
    label = worker or f"{socket.gethostname()}-{os.getpid()}"
    with reported_errors():
        result = text_ocr.run(
            Queue(root),
            settings.raw_dir,
            url,
            label,
            concurrency,
            pages_per_request,
            render_workers,
            until,
        )
    typer.echo(json.dumps(result))
    sys.stdout.flush()
    logging.shutdown()
    # Requests still in flight and renderers stuck on a page would keep the process alive.
    os._exit(2 if result["stopped"] in text_ocr.FAILURES else 0)


@app.command("import")
def import_(
    layer: str | None = typer.Option(None, help="A text layer pass"),
    queue: str | None = typer.Option(None, help="An OCR queue, finished or not"),
) -> None:
    """Copy a text layer pass or an OCR queue into DATA_DIR/text.db.

    Importing a run again replaces its rows."""
    from ..text import store
    from ..text.queue import Queue

    if (layer is None) == (queue is None):
        raise typer.BadParameter("name either --layer or --queue")
    if layer is not None:
        root = _named("layers", layer)
        if not (root / "pages.jsonl").exists():
            raise typer.BadParameter(f"no text layer pass {layer}")
    else:
        root = _named("queues", queue)
        if not root.is_dir():
            raise typer.BadParameter(f"no queue {queue}")
    db = store.connect(settings.text_db)
    try:
        if layer is not None:
            result = store.import_layer(db, layer, root)
        else:
            result = store.import_queue(db, queue, Queue(root))
    finally:
        db.close()
    typer.echo(json.dumps(result))


@app.command("status")
def status(name: str = typer.Argument(..., help="The queue")) -> None:
    """Chunks by state and pages by answer."""
    from ..text.queue import Queue

    root = _named("queues", name)
    if not root.is_dir():
        raise typer.BadParameter(f"no queue {name}")
    typer.echo(json.dumps(Queue(root).status()))
