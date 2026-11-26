"""The databases: the main one (`init-db`), the internal catalogue of files in it (`catalog`), the
serving database the web server reads (`build`), the full-text index of page text
(`build-fulltext`) and the release of those two as a version of the data (`release`)."""

from __future__ import annotations

import json

import typer

from ..config import settings
from .common import database_path

app = typer.Typer()


@app.command("init-db")
def init_db_cmd() -> None:
    """Create the SQLite database, tables, and the FTS5 index."""
    from ..db import init_db

    settings.ensure_dirs()
    init_db()
    typer.echo(f"database ready at {settings.database_url}")


@app.command()
def build() -> None:
    """Write the serving database the web server reads, from scratch, into DATA_DIR/serving.db."""
    from .. import build as stage

    for table, rows in stage.run(settings.serving_db, database_path(), settings.process_db).items():
        typer.echo(f"{table}: {rows} rows")


@app.command("build-fulltext")
def build_fulltext() -> None:
    """Write the full-text index of page text, redacted, from scratch, into DATA_DIR/fulltext.db.

    Reads the serving database, DATA_DIR/text.db and DATA_DIR/privacy.db; changes none of them.
    Run it again after a new privacy check is imported, or after the redaction rules change."""
    from .. import fulltext

    for path, made_by in (
        (settings.text_db, "text import"),
        (settings.privacy_db, "privacy import"),
    ):
        if not path.exists():
            raise typer.BadParameter(f"no {path.name}; run `opencommune {made_by}` first")
    counts = fulltext.run(
        settings.fulltext_db, settings.serving_db, settings.text_db, settings.privacy_db
    )
    typer.echo(json.dumps(counts))


@app.command()
def release(
    note: str = typer.Option(..., help="What changed, for the ledger; the site does not show it"),
) -> None:
    """Release DATA_DIR/serving.db and DATA_DIR/fulltext.db as the next version of the data.

    Run it after build and build-fulltext, before the deploy: it appends the version's row to
    releases.csv in the configuration (commit that file) and writes the row into the full-text
    index, from which the site shows it."""
    from .. import release as stage

    try:
        row = stage.run(settings.releases_file, settings.serving_db, settings.fulltext_db, note)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"released {row.version}: add {settings.releases_file} to the next commit")


@app.command("catalog")
def catalog_command():
    """Refresh the website's document catalogue from the crawl records."""
    import fcntl

    from .. import catalog
    from ..communes import load_communes
    from ..db import init_db, session_scope

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with (settings.data_dir / "catalog.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            typer.echo("catalogue refresh already running")
            return
        init_db()
        from ..db import engine

        if catalog.rebuild_if_outdated(engine):
            typer.echo("pdf_catalog had an older layout and is rebuilt")
        communes = load_communes()
        with session_scope() as session:
            catalog.upsert_communes(session, communes)
        with session_scope() as session:
            count = catalog.refresh(session, communes)
        typer.echo(f"{count} PDFs in the catalogue")
        pages = catalog.html_documents()
        if pages:
            typer.echo(f"{pages} HTML decision pages (not in the catalogue)")
