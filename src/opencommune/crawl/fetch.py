"""Fetch stage: walk each commune's listing pages via its adapter and store PDFs by sha256, and
decision pages published only as HTML where the last step says `capture: html`.

Etiquette: robots.txt honoured for every URL requested, redirect targets included, and a host whose
robots.txt answers 5xx is left alone for the run; descriptive User-Agent; one request per second per
host and per hosting network; Crawl-delay honoured; known URLs never re-downloaded (a known decision
page is asked for again, conditionally, and stored only when its decisions changed).

Failures never stop the run: every failed request, page or download ends as a FetchError with a
kind (robots, http, timeout, parse, empty, not_pdf, not_html, no_content, too_large, exception) on
the source's run, and every run of a source is one line in data/logs/fetch_runs.jsonl (see runlog.py).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from dataclasses import dataclass, field
from html import escape
from urllib.parse import urljoin

import httpx

from ..config import settings
from ..schemas import (
    AdapterStep,
    CommuneConfig,
    DocumentLink,
    FormSubmit,
    SourceConfig,
)
from . import http, runlog, store
from .adapters import Link, get_adapter
from .html_parse import parse_html

log = logging.getLogger(__name__)

# A backfill pass follows up to this many pages per hop instead of each step's max_follow, to
# reach documents older than the weekly pass looks at (David, 2026-11-13). More is logged.
BACKFILL_FOLLOW = 1000
# A page of a hop can be the file itself: an i-web publication with a single attachment redirects
# to it.
PDF_TYPES = frozenset({"application/pdf", "application/x-pdf"})


@dataclass(frozen=True)
class FetchError:
    kind: str
    url: str
    message: str = ""
    adapter: str = ""

    @classmethod
    def of(cls, failure: http.FetchFailure, adapter: str) -> FetchError:
        return cls(failure.kind, failure.url, failure.message, adapter)

    def __str__(self) -> str:
        return f"{self.kind} [{self.adapter}] {self.url}: {self.message}"


@dataclass
class Discovery:
    """What one source's listing walk produced."""

    links: list[DocumentLink] = field(default_factory=list)  # every document, newest first
    listed: list[DocumentLink] = field(
        default_factory=list
    )  # every document link seen on the last hop
    errors: list[FetchError] = field(default_factory=list)
    listing: str = "ok"  # ok, empty, or the error kind of the first failed entry page
    unfollowed: int = 0  # pages of intermediate hops left out by max_follow


@dataclass
class SourceRun:
    """One source's crawl within a commune's fetch."""

    commune: CommuneConfig
    source: SourceConfig
    started: dt.datetime
    dry_run: bool = False
    listing: str | None = None
    listed: list[DocumentLink] = field(default_factory=list)
    discovered: list[DocumentLink] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[FetchError] = field(default_factory=list)
    requests: int = 0
    duration: float = 0.0
    unfollowed: int = 0

    def line(self) -> dict:
        """The run-log record, see runlog.py."""
        dates = sorted(d.date for d in self.listed if d.date)
        return {
            "started": self.started.isoformat(timespec="seconds"),
            "duration_s": round(self.duration, 2),
            "commune_bfs_id": self.commune.bfs_id,
            "commune": self.commune.name,
            "adapter": self.source.adapter,
            "body": self.source.body or self.commune.body,
            "listing_url": self.source.listing_urls[0] if self.source.listing_urls else "",
            "dry_run": self.dry_run,
            "requests": self.requests,
            "listing_status": self.listing,
            "listed": len(self.listed),
            "unfollowed": self.unfollowed,
            "discovered": len(self.discovered),
            "date_min": dates[0].isoformat() if dates else None,
            "date_max": dates[-1].isoformat() if dates else None,
            "new": len(self.downloaded),
            "known": len(self.skipped),
            "errors": [{"kind": e.kind, "url": e.url, "message": e.message} for e in self.errors],
            "links": [
                {"url": d.url, "date": d.date.isoformat() if d.date else None, "title": d.title}
                for d in self.listed
            ],
        }


@dataclass
class FetchResult:
    """One commune's fetch, aggregated over its sources."""

    commune: CommuneConfig
    runs: list[SourceRun] = field(default_factory=list)

    @property
    def discovered(self) -> list[DocumentLink]:
        return [link for run in self.runs for link in run.discovered]

    @property
    def downloaded(self) -> list[str]:
        return [sha for run in self.runs for sha in run.downloaded]

    @property
    def skipped(self) -> list[str]:
        return [url for run in self.runs for url in run.skipped]

    @property
    def errors(self) -> list[FetchError]:
        return [err for run in self.runs for err in run.errors]


# --- adapter hooks (each optional; these helpers are the only place that looks for them) ----------


def _entry_urls(adapter, source: SourceConfig) -> list[str]:
    """The adapter's listing_urls() hook, else the configured URLs; `{1..23}` ranges expanded."""
    hook = getattr(adapter, "listing_urls", None)
    return _expand_ranges(hook(source) if hook else source.listing_urls)


def _build_request(
    adapter, step: AdapterStep | None, url: str, client: httpx.Client
) -> httpx.Request:
    """The adapter's request() hook (POST bodies, extra headers), else a plain GET. The client's
    default headers (User-Agent, Accept-Language) apply either way."""
    hook = getattr(adapter, "request", None)
    if hook is None:
        return client.build_request("GET", url)
    request = hook(step, url)
    for name, value in client.headers.items():
        request.headers.setdefault(name, value)
    return request


def _next_url(adapter, content: str, url: str, step: AdapterStep, headers=None) -> str | None:
    """The adapter's next_url() hook: the next page of the same hop, or None. The response headers
    let an API adapter read a page count (WordPress: X-WP-TotalPages)."""
    hook = getattr(adapter, "next_url", None)
    return hook(content, url, step, headers=headers) if hook else None


# --- discovery ----------------------------------------------------------------


def discover(
    commune: CommuneConfig,
    client: httpx.Client,
    polite: http.Politeness,
    source: SourceConfig | None = None,
) -> Discovery:
    """Walk one source's configured hops. Without `source`, the commune's primary source is used.
    A failed page is recorded and the walk goes on with the other pages of that hop."""
    source = source or commune.all_sources()[0]
    adapter = get_adapter(source.adapter)
    cfg = source.adapter_config
    out = Discovery()
    frontier: list[tuple[str, Link | None]] = [(u, None) for u in _entry_urls(adapter, source)]
    found: list[tuple[Link, str]] = []
    also: list[
        tuple[Link, str]
    ] = []  # documents linked from intermediate pages (step.also_documents)
    for depth, step in enumerate(cfg.steps):
        found = []
        last = depth == len(cfg.steps) - 1
        for page_url, parent in frontier:
            extra: list[Link] = []
            try:
                links = _links_for_page(
                    page_url,
                    adapter,
                    step,
                    cfg,
                    client,
                    polite,
                    retry=depth == 0,
                    parent=parent,
                    extra_step=cfg.steps[-1] if step.also_documents and not last else None,
                    extra_out=extra,
                )
            except http.FetchFailure as exc:
                log.warning("hop %d %s", depth + 1, exc)
                out.errors.append(FetchError.of(exc, source.adapter))
                continue
            found.extend((link, page_url) for link in links)
            also.extend((link, page_url) for link in extra)
        if depth == 0:
            if not found and not out.errors:
                out.errors.append(
                    FetchError(
                        "empty",
                        frontier[0][0] if frontier else "",
                        "no links on the listing page",
                        source.adapter,
                    )
                )
            if out.errors:
                out.listing = out.errors[0].kind
        if not found:
            break
        if depth < len(cfg.steps) - 1:
            ranked = _rank(found, len(found), step)
            cap = step.max_follow or cfg.max_follow
            frontier = [(link.url, link) for link, _ in ranked[:cap]]
            if len(ranked) > cap:
                out.unfollowed += len(ranked) - cap
                log.info(
                    "hop %d: following the newest %d of %d pages (max_follow)",
                    depth + 1,
                    cap,
                    len(ranked),
                )
            found = []
    found = found + also
    out.listed = [
        _document_link(link, src, source, commune) for link, src in _rank(found, len(found))
    ]
    out.links = out.listed  # no cap on documents (David, 2026-11-13)
    if out.listing == "ok" and not out.listed:
        out.listing = "empty"
    return out


def _document_link(
    link: Link, listing_url: str, source: SourceConfig, commune: CommuneConfig
) -> DocumentLink:
    return DocumentLink(
        url=link.url,
        title=link.text or link.url.rsplit("/", 1)[-1],
        date=link.date,
        body=source.body or commune.body,
        listing_url=listing_url,
        adapter=source.adapter,
        upstream_id=link.upstream_id,
        date_precision=link.date_precision,
    )


def _links_for_page(
    page_url,
    adapter,
    step,
    cfg,
    client,
    polite,
    retry: bool = True,
    parent: Link | None = None,
    extra_step: AdapterStep | None = None,
    extra_out: list[Link] | None = None,
) -> list[Link]:
    """Links of one listing page and its continuation pages (adapter next_url hook, at most
    step.max_follow or cfg.max_follow pages). Raises FetchFailure: robots, http, timeout, parse.

    Some CMSs (GOViS) render a collapsed tree intermittently; one retry with a fresh session helps.
    Only listing pages (first hop) are retried: a session page without a protocol yet is empty for real,
    and retrying every one of them doubled the requests on multi-hop sites."""
    for attempt in range(2 if retry else 1):
        links = _links_for_hop(
            page_url, adapter, step, cfg, client, polite, parent, extra_step, extra_out
        )
        if links or attempt == 1 or not retry:
            return links
        log.info("no links on %s, retrying once with a fresh session", page_url)
        client.cookies.clear()
        http._pause(2)
    return []


def _links_for_hop(
    page_url,
    adapter,
    step,
    cfg,
    client,
    polite,
    parent: Link | None = None,
    extra_step: AdapterStep | None = None,
    extra_out: list[Link] | None = None,
) -> list[Link]:
    """`extra_step` reads the same page content a second time with the final step's selection
    (documents linked directly from a listing page, `also_documents`) into `extra_out`."""
    if step.form is not None:
        return _links_for_form(page_url, adapter, step, cfg, client, polite, parent)
    links: list[Link] = []
    url = page_url
    seen = {url}
    for _ in range(step.max_follow or cfg.max_follow):
        try:
            text, headers = _get_page(url, client, polite, adapter, step)
        except _FileAnswer as answer:
            links.extend(_file_links(answer.chain, url, adapter, step, cfg, parent))
            if extra_step is not None and extra_out is not None:
                extra_out.extend(_file_links(answer.chain, url, adapter, extra_step, cfg, parent))
            break
        try:
            links.extend(
                adapter.extract_links(
                    text, url, step, cfg.date_regex, cfg.date_order, parent=parent
                )
            )
            if extra_step is not None and extra_out is not None:
                extra_out.extend(
                    adapter.extract_links(
                        text, url, extra_step, cfg.date_regex, cfg.date_order, parent=parent
                    )
                )
            following = _next_url(adapter, text, url, step, headers)
        except (
            Exception
        ) as exc:  # adapter code on a malformed page: any exception is a parse failure
            raise http.FetchFailure("parse", url, http._describe(exc)) from exc
        if not following or following in seen:
            break
        url = following
        seen.add(url)
    else:
        log.info(
            "%s: pagination stopped after %d pages (max_follow)",
            page_url,
            step.max_follow or cfg.max_follow,
        )
    return links


class _FileAnswer(http.FetchFailure):
    """A page that answered with a PDF. Its body is left unread, for the download; where a page is
    not read for its links (a form), it is a failure of kind not_html."""

    def __init__(self, url: str, response: httpx.Response) -> None:
        super().__init__("not_html", url, "the page is a PDF")
        self.chain = [*(str(r.url) for r in response.history), str(response.url)]


def _file_links(chain, url, adapter, step, cfg, parent: Link | None) -> list[Link]:
    """A page that answered with a PDF, read as a page that links the file under the text of the
    link that led to it, so that the step's own selection decides. The file goes by the first URL of
    the redirect chain that the step selects (on i-web the /_doc/<id> hop, not the publication page
    nor the named file it ends at), the URL a page linking the file directly would give."""
    text = escape(parent.text if parent else "")
    for hop in chain:
        anchor = f'<a href="{escape(hop)}">{text}</a>'
        try:
            found = adapter.extract_links(
                anchor, url, step, cfg.date_regex, cfg.date_order, parent=parent
            )
        except Exception as exc:  # as for a malformed page: any exception is a parse failure
            raise http.FetchFailure("parse", url, http._describe(exc)) from exc
        if found:
            return found[:1]
    log.info("%s is a PDF that the step does not select", url)
    return []


def form_submissions(
    html: str, page_url: str, form: FormSubmit
) -> list[tuple[str, dict[str, str]]]:
    """(action URL, fields) for each of the form's configured value sets: the form's hidden inputs
    with the configured fields set on top. Raises ValueError when no POST form matches."""
    el = parse_html(html).select_one(form.selector)
    if el is None or el.name != "form":
        raise ValueError(f"no form matches {form.selector!r}")
    if (el.get("method") or "get").lower() != "post":
        raise ValueError(f"form {form.selector!r} is not posted")
    action = urljoin(page_url, el.get("action") or page_url).split("#", 1)[0]
    hidden = {
        i["name"]: i.get("value") or ""
        for i in el.find_all("input")
        if i.get("name") and (i.get("type") or "").lower() == "hidden"
    }
    return [(action, {**hidden, **values}) for values in form.values]


def _links_for_form(page_url, adapter, step, cfg, client, polite, parent) -> list[Link]:
    """The step's links from each posted form response (step.form); no continuation pages. The
    client keeps the cookies of the first GET, which the form's session tokens belong to."""
    text = _get_page(page_url, client, polite, adapter, step)[0]
    try:
        submissions = form_submissions(text, page_url, step.form)
    except ValueError as exc:
        raise http.FetchFailure("parse", page_url, str(exc)) from exc
    links: list[Link] = []
    for action, fields in submissions:
        if not polite.allowed(action):
            raise http.FetchFailure("robots", action, "disallowed by robots.txt")
        request = client.build_request("POST", action, data=fields, headers={"Referer": page_url})
        r = http._send(client, polite, request)
        http._ensure_ok(r, action)
        try:
            links.extend(
                adapter.extract_links(
                    r.text,
                    action,
                    step,
                    cfg.date_regex,
                    cfg.date_order,
                    parent=parent,
                )
            )
        except (
            Exception
        ) as exc:  # adapter code on a malformed page: any exception is a parse failure
            raise http.FetchFailure("parse", action, http._describe(exc)) from exc
    return links


_RANGE = re.compile(r"\{(\d+)\.\.(\d+)\}")


def _expand_ranges(urls: list[str]) -> list[str]:
    """`...?page={1..23}` becomes 23 URLs; used for paginated listings."""
    out: list[str] = []
    for url in urls:
        m = _RANGE.search(url)
        if not m:
            out.append(url)
            continue
        for i in range(int(m[1]), int(m[2]) + 1):
            out.append(url[: m.start()] + str(i) + url[m.end() :])
    return out


def _rank(
    found: list[tuple[Link, str]],
    limit: int,
    step: AdapterStep | None = None,
) -> list[tuple[Link, str]]:
    """Newest first. Dated links sort by date; undated keep listing order after dated ones. Links
    dated in the future are kept: invitations to coming meetings (David, 2026-11-13)."""
    seen: set[str] = set()
    uniq: list[tuple[Link, str]] = []
    for link, src in found:
        if link.url not in seen:
            seen.add(link.url)
            uniq.append((link, src))
    dated = sorted(
        [x for x in uniq if x[0].date],
        key=lambda x: x[0].date,
        reverse=True,
    )
    undated = [x for x in uniq if not x[0].date]
    if not dated and undated and step is not None and step.sort == "text_desc":
        undated = sorted(undated, key=lambda x: x[0].text, reverse=True)
    return (dated + undated)[:limit]


def _get_page(
    url: str,
    client: httpx.Client,
    polite: http.Politeness,
    adapter=None,
    step: AdapterStep | None = None,
) -> tuple[str, httpx.Headers]:
    """One listing page as text, with the response headers. Raises FetchFailure: robots, http or
    timeout, and _FileAnswer for a PDF, whose body is not read."""
    if not polite.allowed(url):
        raise http.FetchFailure("robots", url, "disallowed by robots.txt")
    r = http._send(client, polite, _build_request(adapter, step, url, client), stream=True)
    http._ensure_ok(r, url)
    try:
        if r.headers.get("content-type", "").split(";")[0].strip().lower() in PDF_TYPES:
            raise _FileAnswer(url, r)
        r.read()
    except httpx.TimeoutException as exc:
        raise http.FetchFailure("timeout", url, http._describe(exc)) from exc
    except httpx.HTTPError as exc:
        raise http.FetchFailure("http", url, http._describe(exc)) from exc
    finally:
        r.close()
    return r.text, r.headers


def fetch_text(url: str) -> str:
    """One-off text download used for the BFS register."""
    with http.make_client() as client:
        polite = http.Politeness(client, settings.request_delay_seconds, settings.ua)
        r = http._send(client, polite, client.build_request("GET", url))
        r.raise_for_status()
        return r.text


# --- fetch --------------------------------------------------------------------


def fetch_commune(
    commune: CommuneConfig,
    client: httpx.Client,
    polite: http.Politeness,
    index: dict[str, dict],
    limit: int | None = None,
    dry_run: bool = False,
    source_adapter: str | None = None,
) -> FetchResult:
    """Run every source of the commune (or only those of one adapter); `limit` applies per source.
    A source that fails ends up as errors on its run; the next source still runs."""
    result = FetchResult(commune=commune)
    for source in commune.all_sources():
        if source_adapter is None or source.adapter == source_adapter:
            result.runs.append(
                _fetch_source(commune, source, client, polite, index, limit, dry_run)
            )
    return result


def _fetch_source(commune, source, client, polite, index, limit, dry_run) -> SourceRun:
    run = SourceRun(
        commune=commune, source=source, started=dt.datetime.now(dt.UTC), dry_run=dry_run
    )
    requests_before = polite.requests
    t0 = time.monotonic()
    try:
        found = discover(commune, client, polite, source)
        run.listing, run.listed, run.errors = found.listing, found.listed, list(found.errors)
        run.unfollowed = found.unfollowed
        run.discovered = found.links[:limit] if limit is not None else found.links
        if not dry_run:
            for link in run.discovered:
                _fetch_document(link, run, client, polite, index)
    except Exception as exc:  # an adapter bug or anything unforeseen must not stop the weekly run
        log.exception("%s (%s): source failed", commune.name, source.adapter)
        url = source.listing_urls[0] if source.listing_urls else ""
        run.errors.append(FetchError("exception", url, http._describe(exc), source.adapter))
    run.listing = run.listing or "exception"
    run.requests = polite.requests - requests_before
    run.duration = time.monotonic() - t0
    runlog.append(run.line())
    return run


def _fetch_document(link: DocumentLink, run: SourceRun, client, polite, index) -> None:
    cfg = run.source.adapter_config
    try:
        sha = store._download(
            link, run.commune, client, polite, index, cfg.volatile_params, cfg.steps[-1]
        )
    except http.FetchFailure as exc:
        log.warning("%s", exc)
        run.errors.append(FetchError.of(exc, run.source.adapter))
        return
    if sha is None:
        run.skipped.append(link.url)
    else:
        run.downloaded.append(sha)


def run(
    communes: list[CommuneConfig],
    limit: int | None = None,
    dry_run: bool = False,
    source_adapter: str | None = None,
    transport: httpx.BaseTransport | None = None,
    max_follow: int | None = None,
    backfill: bool = False,
) -> list[FetchResult]:
    """`backfill` follows up to BACKFILL_FOLLOW pages per hop instead of each step's max_follow."""
    if max_follow is not None and max_follow < 1:
        raise ValueError("max_follow must be positive")
    if backfill and max_follow is not None:
        raise ValueError("backfill and max_follow exclude each other")
    settings.ensure_dirs()
    index = store.load_index()
    results: list[FetchResult] = []
    with http.make_client(transport) as client:
        delay = settings.request_delay_seconds
        polite = http.Politeness(client, delay, settings.ua)
        for commune in communes:
            if backfill:
                commune = commune.model_copy(deep=True)
                for source in commune.all_sources():
                    source.adapter_config.max_follow = BACKFILL_FOLLOW
                    for step in source.adapter_config.steps:
                        step.max_follow = None
            if max_follow is not None:
                commune = commune.model_copy(deep=True)
                for source in commune.all_sources():
                    cfg = source.adapter_config
                    cfg.max_follow = min(cfg.max_follow, max_follow)
                    for step in cfg.steps:
                        if step.max_follow is not None:
                            step.max_follow = min(step.max_follow, max_follow)
            results.append(
                fetch_commune(
                    commune,
                    client,
                    polite,
                    index,
                    limit=limit,
                    dry_run=dry_run,
                    source_adapter=source_adapter,
                )
            )
    return results
