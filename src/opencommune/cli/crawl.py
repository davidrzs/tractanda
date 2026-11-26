"""Discovery and download: `fetch`, the archive families (`iweb`, `wordpress`,
`linked-archive`) and the checks on the crawl (`storage-check`, `access-recheck`,
`coverage-report`)."""

from __future__ import annotations

from pathlib import Path

import typer

from ..config import settings
from .database import catalog_command

app = typer.Typer()


def _communes(bfs_ids: str | None, only_new: bool = False, source: str | None = None):
    """Select communes by a comma-separated list of BFS ids, or those without any fetched document.
    With `source`, "new" means no document fetched through that adapter yet, and communes without
    such a source are left out."""
    from ..communes import load_communes

    communes = load_communes()
    if bfs_ids:
        wanted = {int(x) for x in bfs_ids.split(",") if x.strip()}
        communes = [c for c in communes if c.bfs_id in wanted]
        missing = wanted - {c.bfs_id for c in communes}
        if missing:
            raise typer.BadParameter(
                f"no commune with bfs_id {sorted(missing)} in the configuration"
            )
    if source:
        communes = [c for c in communes if any(s.adapter == source for s in c.all_sources())]
    if only_new:
        from ..crawl.store import load_index

        primary = {c.bfs_id: c.adapter for c in communes}
        seen = {
            v.get("commune_bfs_id")
            for v in load_index().values()
            if source is None
            or (v.get("adapter") or primary.get(v.get("commune_bfs_id"))) == source
        }
        communes = [c for c in communes if c.bfs_id not in seen]
    return communes


@app.command()
def fetch(
    commune: str | None = typer.Option(None, help="BFS id, or several separated by commas"),
    limit: int | None = typer.Option(None, help="Max documents per commune and source"),
    dry_run: bool = typer.Option(False, help="List discovered documents without downloading"),
    max_follow: int | None = typer.Option(
        None, min=1, help="Cap pages followed per hop for a small initial fetch"
    ),
    only_new: bool = typer.Option(False, help="Only communes without any fetched document yet"),
    backfill: bool = typer.Option(
        False,
        help="Follow up to 1000 pages per hop instead of max_follow, to reach older documents",
    ),
    source: str | None = typer.Option(
        None, help="Only sources of this adapter, for example wp_media"
    ),
    rebuild_index: bool = typer.Option(
        False, help="Rewrite data/raw/urls.jsonl from the sidecars, then stop"
    ),
) -> None:
    """Discover and download public municipal documents into data/raw/."""
    from ..crawl import fetch as fetch_stage
    from ..crawl import store

    if rebuild_index:
        from ..communes import load_communes

        volatile = {
            c.bfs_id: [p for s in c.all_sources() for p in s.adapter_config.volatile_params]
            for c in load_communes()
        }
        typer.echo(f"{len(store.rebuild_index(volatile))} URLs in {store.index_path()}")
        return
    for res in fetch_stage.run(
        _communes(commune, only_new, source),
        limit=limit,
        dry_run=dry_run,
        source_adapter=source,
        max_follow=max_follow,
        backfill=backfill,
    ):
        typer.echo(
            f"{res.commune.name} ({res.commune.canton}): {len(res.discovered)} discovered, "
            f"{len(res.downloaded)} downloaded, {len(res.skipped)} known, {len(res.errors)} errors"
        )
        for run in res.runs:
            if run.listing != "ok":
                typer.echo(f"    listing {run.listing} ({run.source.adapter})")
        for d in res.discovered:
            typer.echo(f"    {d.date or '   ?    '}  {d.title[:60]:<60}  {d.url}")
        for e in res.errors:
            typer.echo(f"    ERROR {e}")


@app.command("iweb")
def iweb_command(
    mode: str = typer.Argument(..., help="discover, fetch, sync, or report"),
    commune: str | None = typer.Option(
        None, help="BFS ids; defaults to all i-web register entries"
    ),
    page_budget: int = typer.Option(
        100,
        min=1,
        help="Pages per commune this pass, four times as many with a parliament; unfinished queues "
        "are retained",
    ),
    limit: int | None = typer.Option(
        None, min=1, help="Download attempts per commune; known PDFs do not count"
    ),
    retry_failed: bool = typer.Option(False, help="Retry previously failed pages or documents"),
    refresh: bool = typer.Option(
        False,
        help="Revisit listing pages to find new publications; keep held PDFs and an unfinished "
        "queue",
    ),
    only_unconfigured: bool = typer.Option(False, help="Discover communes not yet configured"),
    max_minutes: int | None = typer.Option(
        None, min=1, help="Stop between requests after this time; retain progress"
    ),
) -> None:
    """Inventory the i-web sites and download their PDFs, resumably."""
    from ..communes import load_communes
    from ..crawl import iweb
    from ..register import load_register

    if mode not in {"discover", "fetch", "sync", "report"}:
        raise typer.BadParameter("mode must be discover, fetch, sync, or report")
    entries = [r for r in load_register() if r.vendor == "i-web" and r.website]
    if commune:
        try:
            wanted = [int(x.strip()) for x in commune.split(",") if x.strip()]
        except ValueError:
            raise typer.BadParameter("commune must contain comma-separated BFS ids") from None
        by_id = {r.bfs_id: r for r in entries}
        if missing := set(wanted) - by_id.keys():
            raise typer.BadParameter(
                f"not an i-web register entry with a website: {sorted(missing)}"
            )
        entries = [by_id[n] for n in dict.fromkeys(wanted)]
    if only_unconfigured:
        entries = [r for r in entries if r.status != "configured"]
    if mode == "report":
        import json

        for entry in entries:
            state = iweb.read(entry.bfs_id)
            typer.echo(
                json.dumps(
                    iweb.summary(state)
                    if state
                    else {"bfs_id": entry.bfs_id, "name": entry.name, "status": "not_started"},
                    ensure_ascii=False,
                )
            )
        return
    try:
        iweb.run(
            entries,
            mode=mode,
            communes=load_communes(),
            page_budget=page_budget,
            limit=limit,
            retry_failed=retry_failed,
            refresh=refresh,
            max_minutes=max_minutes,
            emit=typer.echo,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    if mode in {"fetch", "sync"}:
        catalog_command()


@app.command("wordpress")
def wordpress_command(
    mode: str = typer.Argument(..., help="configure, fetch or check"),
    commune: str | None = typer.Option(None, help="Optional comma-separated BFS ids"),
    limit: int | None = typer.Option(None, min=1, help="New PDF attempts per commune"),
    max_minutes: int | None = typer.Option(
        None, min=1, help="Stop between requests; retain progress"
    ),
    commune_max_minutes: float | None = typer.Option(
        None, min=0.01, help="Cooperative time slice per commune in minutes"
    ),
    workers: int = typer.Option(1, min=1, max=8, help="Concurrent commune downloads"),
    retry_failed: bool = typer.Option(False),
    refresh: bool = typer.Option(False),
):
    """Configure, fetch or check the WordPress archives (data/archive-sources.yaml)."""
    from ..communes import bfs_register
    from ..crawl import wordpress

    if mode not in {"configure", "fetch", "check"}:
        raise typer.BadParameter("mode must be configure, fetch or check")
    try:
        if mode == "configure":
            import json

            from ..crawl.archive_config import configure_wordpress

            typer.echo(json.dumps(configure_wordpress(), ensure_ascii=False))
            return
        sources = wordpress.load_sources()
        if commune:
            wanted = {int(n.strip()) for n in commune.split(",")}
            if wanted - {s.bfs_id for s in sources}:
                raise ValueError("BFS id missing from archive-sources.yaml")
            sources = [s for s in sources if s.bfs_id in wanted]
        # only the sources this run uses: an entry under review elsewhere does not block a list
        wordpress.validate_sources(sources, snapshot_optional=mode == "check")
        if mode == "check":
            where = "with" if bfs_register() else "without"
            typer.echo(
                f"{len(sources)} WordPress archive sources valid, checked {where} the BFS snapshot"
            )
            return
        wordpress.run_fetch(
            sources,
            limit=limit,
            max_minutes=max_minutes,
            commune_max_minutes=commune_max_minutes,
            retry_failed=retry_failed,
            refresh=refresh,
            workers=workers,
            emit=typer.echo,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    catalog_command()


@app.command("linked-archive")
def linked_archive_command(
    mode: str = typer.Argument(
        ..., help="fetch, check, or accept a reviewed configuration change (needs --commune)"
    ),
    commune: str | None = typer.Option(None, help="Optional comma-separated BFS ids"),
    limit: int = typer.Option(3, min=1, help="New PDF attempts per commune"),
    max_minutes: int = typer.Option(60, min=1),
    commune_max_minutes: float | None = typer.Option(
        None, min=0.01, help="Cooperative time slice per commune in minutes"
    ),
    refresh: bool = typer.Option(False, help="Read the configured listings again"),
    workers: int = typer.Option(1, min=1, max=8, help="Concurrent commune downloads"),
    retry_failed: bool = typer.Option(False),
):
    """Fetch or check the linked archives (data/linked-sources.yaml)."""
    import json

    from ..communes import bfs_register
    from ..crawl import linked_archive

    if mode not in {"fetch", "check", "accept"}:
        raise typer.BadParameter("mode must be fetch, check or accept")
    if mode == "accept" and not commune:
        raise typer.BadParameter("accept needs --commune: the sources whose change was reviewed")
    try:
        sources = linked_archive.load_sources()
        if commune:
            wanted = {int(n.strip()) for n in commune.split(",")}
            if wanted - {s.bfs_id for s in sources}:
                raise ValueError("BFS id missing from linked-sources.yaml")
            sources = [s for s in sources if s.bfs_id in wanted]
        # only the sources this run uses: an entry under review elsewhere does not block a list
        sources = linked_archive.validate_sources(sources, snapshot_optional=mode == "check")
        if mode == "check":
            where = "with" if bfs_register() else "without"
            typer.echo(
                f"{len(sources)} linked archive sources valid, checked {where} the BFS snapshot"
            )
            return
        if mode == "accept":
            for source in sources:
                result = linked_archive.accept(source)
                typer.echo(json.dumps(result or {"bfs_id": source.bfs_id, "unchanged": True}))
            return
        linked_archive.run_fetch(
            sources,
            limit=limit,
            max_minutes=max_minutes,
            commune_max_minutes=commune_max_minutes,
            refresh=refresh,
            retry_failed=retry_failed,
            workers=workers,
            emit=typer.echo,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    catalog_command()


@app.command("storage-check")
def storage_check() -> None:
    """Exit 1 when the raw archive's filesystem is below its free-space reserve."""
    from ..crawl.storage import StorageReserveError, require_space

    try:
        free = require_space()
    except (StorageReserveError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"{free:,} bytes free, reserve {settings.raw_min_free_bytes:,}")


@app.command("access-recheck")
def access_recheck(
    limit: int = typer.Option(100, min=0, help="Observations to recheck, oldest first"),
    seed_checkpoints: bool = typer.Option(
        False, help="Start from robots refusals recorded in the collectors' checkpoints"
    ),
) -> None:
    """Recheck the oldest access failures (robots.txt, rate limits, errors) without downloading."""
    from ..crawl import access

    access.recheck(limit, seed_checkpoints)


@app.command("coverage-report")
def coverage_report(
    output: Path = typer.Option(..., help="Directory for coverage.json and coverage.md"),
    shallow_limit: int = typer.Option(
        10, min=0, help="Communes with at most this many PDFs count as shallow"
    ),
) -> None:
    """Report how deep the archive goes per commune; reads the database and the crawl state."""
    import json

    from ..crawl import coverage_report as report

    try:
        summary = report.write(output, shallow_limit)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    typer.echo(json.dumps(summary, ensure_ascii=False))
