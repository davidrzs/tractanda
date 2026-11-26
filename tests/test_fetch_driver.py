"""The fetch driver against a fake site served by httpx.MockTransport: no network, no sleeping."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from urllib.parse import parse_qsl, urljoin

import httpx
import pytest

from opencommune.config import settings
from opencommune.crawl import fetch, http, runlog, store
from opencommune.crawl.adapters import Link
from opencommune.schemas import AdapterConfig, AdapterStep, CommuneConfig, FormSubmit, SourceConfig

HOST = "https://commune.test"
PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


def commune(
    bfs_id: int = 1, name: str = "Testwil", adapter: str = "html_links", **cfg
) -> CommuneConfig:
    steps = cfg.pop("steps", [AdapterStep(selector="a[href$='.pdf']")])
    return CommuneConfig(
        bfs_id=bfs_id,
        name=name,
        canton="ZH",
        language="de",
        website=HOST,
        body="assembly",
        listing_urls=[cfg.pop("listing_url", f"{HOST}/protokolle")],
        adapter=adapter,
        adapter_config=AdapterConfig(steps=steps, **cfg),
    )


def listing(*names: str) -> str:
    return (
        "<html><body>"
        + "".join(f'<a href="/docs/{n}.pdf">Protokoll {n}</a>' for n in names)
        + "</body></html>"
    )


def _key(url: str) -> str:
    u = httpx.URL(url)
    return f"{u.host}{u.path}"


class Site:
    """Routes keyed by host and path to a response factory or an exception; every request is kept."""

    def __init__(self) -> None:
        self.routes: dict[str, object] = {}
        self.calls: list[httpx.Request] = []

    def add(
        self, url: str, status: int = 200, body: bytes | str = b"", headers: dict | None = None
    ) -> None:
        self.routes[_key(url)] = lambda request: httpx.Response(
            status, content=body, headers=headers or {}
        )

    def answer(self, url: str, factory) -> None:
        self.routes[_key(url)] = factory

    def fail(self, url: str, exc: Exception) -> None:
        self.routes[_key(url)] = exc

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        route = self.routes.get(_key(str(request.url)))
        if route is None:
            return httpx.Response(404, content=b"not found")
        if isinstance(route, Exception):
            raise route
        return route(request)

    def calls_to(self, url: str) -> list[httpx.Request]:
        return [r for r in self.calls if _key(str(r.url)) == _key(url)]


@pytest.fixture
def site(tmp_path, monkeypatch) -> Site:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "request_delay_seconds", 0.0)
    settings.ensure_dirs()
    monkeypatch.setattr(http, "_pause", lambda seconds: None)
    monkeypatch.setattr(http.Politeness, "_network", lambda self, host: host)
    http.Politeness._network_last.clear()
    return Site()


def crawl(site: Site) -> tuple[httpx.Client, http.Politeness]:
    client = http.make_client(httpx.MockTransport(site.handler))
    return client, http.Politeness(client, 0.0, settings.ua)


def test_follow_cap_limits_initial_fetch_without_changing_archive_config(site):
    site.add(f"{HOST}/robots.txt", body="User-agent: *\nAllow: /\n")
    site.add(
        f"{HOST}/protokolle",
        body='<a href="/session/2026-01-01">1. Januar 2026</a>'
        '<a href="/session/2026-02-01">1. Februar 2026</a>',
    )
    for date in ("2026-01-01", "2026-02-01"):
        site.add(f"{HOST}/session/{date}", body=listing(date))
        site.add(f"{HOST}/docs/{date}.pdf", body=PDF)
    config = commune(
        max_follow=100,
        steps=[
            AdapterStep(selector="a[href*='/session/']", max_follow=20),
            AdapterStep(selector="a[href$='.pdf']"),
        ],
    )
    result = fetch.run([config], max_follow=1, transport=httpx.MockTransport(site.handler))
    assert len(result[0].downloaded) == 1
    assert not site.calls_to(f"{HOST}/session/2026-01-01")
    assert len(site.calls_to(f"{HOST}/session/2026-02-01")) == 1
    assert config.adapter_config.max_follow == 100
    assert config.adapter_config.steps[0].max_follow == 20


def test_future_dated_documents_are_kept(site):
    site.add(f"{HOST}/robots.txt", body="User-agent: *\nAllow: /\n")
    site.add(f"{HOST}/protokolle", body=listing("2099-01-01"))
    site.add(f"{HOST}/docs/2099-01-01.pdf", body=PDF)
    result = fetch.run([commune()], transport=httpx.MockTransport(site.handler))
    assert len(result[0].downloaded) == 1
    assert str(result[0].discovered[0].date) == "2099-01-01"


def test_backfill_follows_past_max_follow_without_changing_the_config(site):
    site.add(f"{HOST}/robots.txt", body="User-agent: *\nAllow: /\n")
    site.add(
        f"{HOST}/protokolle",
        body='<a href="/session/2026-01-01">1. Januar 2026</a>'
        '<a href="/session/2026-02-01">1. Februar 2026</a>',
    )
    for date in ("2026-01-01", "2026-02-01"):
        site.add(f"{HOST}/session/{date}", body=listing(date))
        site.add(f"{HOST}/docs/{date}.pdf", body=PDF + date.encode())
    config = commune(
        steps=[
            AdapterStep(selector="a[href*='/session/']", max_follow=1),
            AdapterStep(selector="a[href$='.pdf']"),
        ],
    )
    transport = httpx.MockTransport(site.handler)
    weekly = fetch.run([config], transport=transport)
    assert len(weekly[0].downloaded) == 1 and weekly[0].runs[0].unfollowed == 1
    assert not site.calls_to(f"{HOST}/session/2026-01-01")
    backfill = fetch.run([config], backfill=True, transport=transport)
    assert len(backfill[0].downloaded) == 1 and backfill[0].runs[0].unfollowed == 0
    assert len(site.calls_to(f"{HOST}/session/2026-01-01")) == 1
    assert config.adapter_config.steps[0].max_follow == 1
    assert next(iter(runlog.read_runs()))["unfollowed"] == 1


def fetch_one(site: Site, c: CommuneConfig | None = None, **kw) -> fetch.SourceRun:
    client, polite = crawl(site)
    (run,) = fetch.fetch_commune(c or commune(), client, polite, kw.pop("index", {}), **kw).runs
    return run


# --- listing failures are recorded, never silent ----------------------------------------------


def test_listing_http_error_is_recorded_with_its_kind(site):
    site.add(f"{HOST}/protokolle", 500, b"boom")
    run = fetch_one(site)
    assert run.listing == "http"
    assert [(e.kind, e.adapter) for e in run.errors] == [("http", "html_links")]
    assert "HTTP 500" in str(run.errors[0]) and run.discovered == []


def test_listing_timeout_is_its_own_kind(site):
    site.fail(f"{HOST}/protokolle", httpx.ReadTimeout("read timed out"))
    run = fetch_one(site)
    assert run.listing == "timeout" and run.errors[0].kind == "timeout"


def test_adapter_exception_on_a_malformed_page_is_a_parse_error(site):
    site.add(f"{HOST}/protokolle", 200, b"<this is not xml")
    run = fetch_one(site, commune(adapter="paris_api"))
    assert run.listing == "parse" and run.errors[0].adapter == "paris_api"


def test_listing_without_links_is_empty_not_ok(site):
    site.add(f"{HOST}/protokolle", 200, "<html><p>nothing here</p></html>")
    run = fetch_one(site)
    assert run.listing == "empty" and [e.kind for e in run.errors] == ["empty"]
    assert len(site.calls_to(f"{HOST}/protokolle")) == 2  # one retry with a fresh session


def test_robots_disallow_is_reported_and_the_page_never_requested(site):
    site.add(f"{HOST}/robots.txt", 200, "User-agent: *\nDisallow: /protokolle\n")
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01"))
    run = fetch_one(site)
    assert run.listing == "robots" and not site.calls_to(f"{HOST}/protokolle")


def test_run_survives_a_broken_source_and_continues_with_the_next_commune(site):
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01"))
    site.add(f"{HOST}/docs/2026-03-01.pdf", 200, PDF, {"content-type": "application/pdf"})
    broken = commune(bfs_id=2, name="Kaputt", adapter="no_such_adapter")
    results = fetch.run([broken, commune()], transport=httpx.MockTransport(site.handler))
    assert (
        results[0].errors[0].kind == "exception"
        and "no_such_adapter" in results[0].errors[0].message
    )
    assert results[0].runs[0].listing == "exception"
    assert len(results[1].downloaded) == 1 and not results[1].errors


# --- documents -----------------------------------------------------------------------------------


def test_download_writes_pdf_and_sidecar(site):
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01", "2025-12-05"))
    for name in ("2026-03-01", "2025-12-05"):
        site.add(
            f"{HOST}/docs/{name}.pdf", 200, PDF + name.encode(), {"content-type": "application/pdf"}
        )
    run = fetch_one(site)
    assert run.listing == "ok" and len(run.downloaded) == 2 and not run.errors
    assert [d.date.isoformat() for d in run.discovered] == ["2026-03-01", "2025-12-05"]
    for sha in run.downloaded:
        assert (settings.raw_dir / f"{sha}.pdf").exists() and (
            settings.raw_dir / f"{sha}.json"
        ).exists()


def test_document_errors_do_not_stop_the_other_documents(site):
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01", "2025-12-05"))
    site.add(f"{HOST}/docs/2026-03-01.pdf", 500, b"")
    site.add(f"{HOST}/docs/2025-12-05.pdf", 200, PDF, {"content-type": "application/pdf"})
    run = fetch_one(site)
    assert run.listing == "ok" and len(run.downloaded) == 1
    assert [(e.kind, e.url) for e in run.errors] == [("http", f"{HOST}/docs/2026-03-01.pdf")]


# --- adapter hooks: request() and next_url() ----------------------------------------------------


class PagedAdapter:
    name = "paged"

    def extract_links(self, html, base_url, step, date_regex, date_order, parent=None):
        return [
            Link(url=urljoin(base_url, h), text=h) for h in re.findall(r'href="([^"]+\.pdf)"', html)
        ]

    def request(self, step, url):
        return httpx.Request("POST", url, data={"q": "protokoll"}, headers={"X-Api": "1"})

    def next_url(self, content, url, step, headers=None):
        m = re.search(r'next="([^"]+)"', content)
        return urljoin(url, m[1]) if m else None


def test_request_and_next_url_hooks_drive_the_listing(site, monkeypatch):
    monkeypatch.setattr(fetch, "get_adapter", lambda name: PagedAdapter())
    site.add(f"{HOST}/list/1", 200, '<a href="/docs/a.pdf">a</a> next="/list/2"')
    site.add(f"{HOST}/list/2", 200, '<a href="/docs/b.pdf">b</a> next="/list/3"')
    site.add(f"{HOST}/list/3", 200, '<a href="/docs/c.pdf">c</a>')
    c = commune(listing_url=f"{HOST}/list/1", steps=[AdapterStep(selector="x", max_follow=2)])
    run = fetch_one(site, c, dry_run=True)
    assert [d.url for d in run.discovered] == [
        f"{HOST}/docs/a.pdf",
        f"{HOST}/docs/b.pdf",
    ]  # page 3 is beyond max_follow
    posts = [r for r in site.calls if r.method == "POST"]
    assert len(posts) == 2 and not site.calls_to(f"{HOST}/list/3")
    assert all(r.headers["x-api"] == "1" and r.headers["user-agent"] == settings.ua for r in posts)


# --- retries -------------------------------------------------------------------------------------


def in_turn(*responses):
    """A route that answers with each (status, body, headers) once, in order."""
    it = iter(responses)

    def factory(request):
        status, body, headers = next(it)
        return httpx.Response(status, content=body, headers=headers)

    return factory


def test_listing_5xx_is_retried_three_times_with_growing_pauses(site, monkeypatch):
    pauses: list[float] = []
    monkeypatch.setattr(http, "_pause", pauses.append)
    site.add(f"{HOST}/protokolle", 503, b"")
    run = fetch_one(site)
    assert run.listing == "http" and len(site.calls_to(f"{HOST}/protokolle")) == 3
    assert 2.0 <= pauses[0] < 3.0 and 4.0 <= pauses[1] < 5.0


@pytest.mark.parametrize(
    "exc, kind", [(httpx.ConnectError("refused"), "http"), (httpx.ReadTimeout("slow"), "timeout")]
)
def test_connect_errors_and_timeouts_are_retried(site, exc, kind):
    site.fail(f"{HOST}/protokolle", exc)
    run = fetch_one(site)
    assert run.errors[0].kind == kind and len(site.calls_to(f"{HOST}/protokolle")) == 3


def test_429_waits_for_retry_after_and_then_succeeds(site, monkeypatch):
    pauses: list[float] = []
    monkeypatch.setattr(http, "_pause", pauses.append)
    busy = (429, b"", {"retry-after": "0"})
    site.answer(f"{HOST}/protokolle", in_turn(busy, busy, (200, listing("2026-03-01"), {})))
    site.add(f"{HOST}/docs/2026-03-01.pdf", 200, PDF, {"content-type": "application/pdf"})
    run = fetch_one(site)
    assert run.listing == "ok" and len(run.downloaded) == 1 and not run.errors
    assert pauses == [0.0, 0.0]


def test_retry_after_as_http_date_is_understood():
    r = httpx.Response(
        503, headers={"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}
    )  # in the past: no wait
    assert http._retry_after(r) == 0.0
    assert http._retry_after(httpx.Response(503, headers={"retry-after": "7"})) == 7.0
    assert http._retry_after(httpx.Response(503, headers={"retry-after": "soon"})) is None


def test_other_4xx_is_never_retried(site):
    site.add(f"{HOST}/protokolle", 403, b"")
    run = fetch_one(site)
    assert run.listing == "http" and len(site.calls_to(f"{HOST}/protokolle")) == 1


def test_a_long_retry_after_is_not_waited_for(site, monkeypatch):
    pauses: list[float] = []
    monkeypatch.setattr(http, "_pause", pauses.append)
    site.add(f"{HOST}/protokolle", 503, b"", {"retry-after": "3600"})
    run = fetch_one(site)
    assert len(site.calls_to(f"{HOST}/protokolle")) == 1 and pauses == []
    assert run.errors[0].kind == "http" and "Retry-After 3600s" in run.errors[0].message


def test_download_5xx_is_retried_and_then_stored(site):
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01"))
    site.answer(
        f"{HOST}/docs/2026-03-01.pdf",
        in_turn((502, b"", {}), (200, PDF, {"content-type": "application/pdf"})),
    )
    run = fetch_one(site)
    assert len(run.downloaded) == 1 and not run.errors
    assert len(site.calls_to(f"{HOST}/docs/2026-03-01.pdf")) == 2


# --- downloads: streamed, checked, atomic --------------------------------------------------------

DOC = f"{HOST}/docs/2026-03-01.pdf"


def stored() -> list[str]:
    """Names of PDFs, part files and sidecars in data/raw."""
    return sorted(
        p.name
        for p in settings.raw_dir.iterdir()
        if p.suffix in (".pdf", ".part") or p.name.endswith(".pdf.part")
    )


def test_non_pdf_body_is_rejected_even_with_a_pdf_content_type(site):
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01"))
    site.add(
        DOC, 200, b"<html><body>Please log in</body></html>", {"content-type": "application/pdf"}
    )
    run = fetch_one(site)
    assert (
        [e.kind for e in run.errors] == ["not_pdf"] and run.downloaded == [] and run.skipped == []
    )
    assert stored() == []


def test_declared_size_over_the_cap_is_rejected_before_reading(site, monkeypatch):
    monkeypatch.setattr(store, "MAX_PDF_BYTES", 100)
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01"))
    site.add(DOC, 200, PDF, {"content-type": "application/pdf", "content-length": "101"})
    run = fetch_one(site)
    assert [e.kind for e in run.errors] == ["too_large"] and stored() == []


def test_streamed_body_over_the_cap_is_cut_off_and_removed(site, monkeypatch):
    monkeypatch.setattr(store, "MAX_PDF_BYTES", 100)
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01"))
    site.answer(DOC, lambda request: httpx.Response(200, stream=httpx.ByteStream(PDF + b"x" * 200)))
    run = fetch_one(site)
    assert [e.kind for e in run.errors] == ["too_large"] and stored() == []


class Breaking(httpx.SyncByteStream):
    def __iter__(self):
        yield PDF
        raise httpx.ReadError("connection reset")


def test_read_error_mid_stream_leaves_no_part_file(site):
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01"))
    site.answer(DOC, lambda request: httpx.Response(200, stream=Breaking()))
    run = fetch_one(site)
    assert [e.kind for e in run.errors] == ["http"] and stored() == []


def test_successful_download_is_hashed_sized_and_leaves_no_part(site):
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01"))
    site.add(DOC, 200, PDF, {"content-type": "application/pdf", "etag": '"abc"'})
    run = fetch_one(site)
    (sha,) = run.downloaded
    assert sha == hashlib.sha256(PDF).hexdigest()
    assert stored() == [f"{sha}.pdf"]
    sidecar = json.loads((settings.raw_dir / f"{sha}.json").read_text())
    assert (
        sidecar["size"] == len(PDF) and sidecar["etag"] == '"abc"' and sidecar["source_url"] == DOC
    )
    assert sidecar["listing_date"] == "2026-03-01" and sidecar["adapter"] == "html_links"


def test_same_pdf_under_a_second_url_is_remembered_but_stored_once(site):
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01", "2026-03-01-copy"))
    site.add(DOC, 200, PDF, {"content-type": "application/pdf"})
    site.add(f"{HOST}/docs/2026-03-01-copy.pdf", 200, PDF, {"content-type": "application/pdf"})
    index: dict = {}
    run = fetch_one(site, index=index)
    assert len(run.downloaded) == 1 and len(run.skipped) == 1 and not run.errors
    assert stored() == [f"{run.downloaded[0]}.pdf"]
    assert {v["sha256"] for v in index.values()} == {run.downloaded[0]} and len(index) == 2


def test_a_second_commune_linking_a_known_file_is_recorded_without_a_download(site):
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01"))
    site.add(DOC, 200, PDF, {"content-type": "application/pdf"})
    index: dict = {}
    fetch_one(site, index=index)
    neighbour = commune(bfs_id=2, name="Nachbarwil")
    second = fetch_one(site, neighbour, index=index)
    assert second.skipped == [DOC] and len(site.calls_to(DOC)) == 1
    assert store.load_index()[store.canonical_url(DOC)]["communes"] == [1, 2]
    fetch_one(site, neighbour, index=index)
    assert len(store.index_path().read_text().splitlines()) == 2


def test_known_documents_are_not_requested_again(site):
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01"))
    site.add(DOC, 200, PDF, {"content-type": "application/pdf"})
    index: dict = {}
    first = fetch_one(site, index=index)
    second = fetch_one(site, index=index)
    assert first.downloaded and second.downloaded == [] and second.skipped == [DOC]
    assert len(site.calls_to(DOC)) == 1


# --- URL index ------------------------------------------------------------------------------------


def test_volatile_query_parameters_do_not_cause_re_downloads(site):
    stamped = [f'<a href="/docs/2026-03-01.pdf?tmstv={n}">Protokoll 2026-03-01</a>' for n in (1, 2)]
    site.answer(f"{HOST}/protokolle", in_turn((200, stamped[0], {}), (200, stamped[1], {})))
    site.add(DOC, 200, PDF, {"content-type": "application/pdf"})
    index: dict = {}
    c = commune(steps=[AdapterStep(selector="a[href*='.pdf']")])
    first, second = fetch_one(site, c, index=index), fetch_one(site, c, index=index)
    assert first.downloaded and second.downloaded == [] and second.skipped == [f"{DOC}?tmstv=2"]
    assert len(site.calls_to(DOC)) == 1
    (line,) = [
        json.loads(link) for link in (settings.raw_dir / "urls.jsonl").read_text().splitlines()
    ]
    assert (
        line["url"] == f"{DOC}?tmstv=1"
        and line["canonical_url"] == DOC
        and line["sha256"] == first.downloaded[0]
    )
    assert line["commune_bfs_id"] == 1 and line["adapter"] == "html_links" and line["fetched_at"]
    assert store.load_index() == index


def test_adapter_volatile_params_extend_the_global_list(site):
    stamped = [f'<a href="/docs/2026-03-01.pdf?sid={n}">Protokoll 2026-03-01</a>' for n in (1, 2)]
    site.answer(f"{HOST}/protokolle", in_turn((200, stamped[0], {}), (200, stamped[1], {})))
    site.add(DOC, 200, PDF, {"content-type": "application/pdf"})
    index: dict = {}
    c = commune(volatile_params=["sid"], steps=[AdapterStep(selector="a[href*='.pdf']")])
    fetch_one(site, c, index=index)
    second = fetch_one(site, c, index=index)
    assert second.skipped == [f"{DOC}?sid=2"] and len(site.calls_to(DOC)) == 1


# --- robots.txt: server errors and redirect hops ------------------------------------------------

ROBOTS = f"{HOST}/robots.txt"


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("headers", [{}, {"retry-after": "3600"}])
def test_robots_busy_disallows_the_host_for_the_run_and_is_logged_once(
    site, caplog, status, headers
):
    site.add(ROBOTS, status, b"", headers)
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01"))
    with caplog.at_level(logging.WARNING, logger="opencommune.crawl.http"):
        results = fetch.run(
            [commune(), commune(bfs_id=2, name="Nebenwil")],
            transport=httpx.MockTransport(site.handler),
        )
    # An explicit long Retry-After is a resumable HTTP cooldown; a busy
    # robots.txt response without one remains a robots denial for this run.
    expected = "http" if headers else "robots"
    assert [r.runs[0].listing for r in results] == [expected, expected]
    assert not site.calls_to(f"{HOST}/protokolle")
    assert len(site.calls_to(ROBOTS)) == (
        1 if headers else 3
    )  # retried unless told to wait an hour
    assert sum("host disallowed for this run" in rec.message for rec in caplog.records) == 1


@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
def test_unreachable_robots_disallows_the_host(site, exc):
    """RFC 9309, 2.3.1.4: robots.txt unreachable through a network error means complete
    disallow, as for a 5xx answer; the listing page is never requested."""
    site.fail(ROBOTS, exc)
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01"))
    run = fetch_one(site)
    assert run.listing == "robots" and not site.calls_to(f"{HOST}/protokolle")


def test_redirect_to_a_disallowed_listing_page_is_not_followed(site):
    site.add(ROBOTS, 200, "User-agent: *\nDisallow: /private/\n")
    site.add(f"{HOST}/protokolle", 302, b"", {"location": "/private/protokolle"})
    site.add(f"{HOST}/private/protokolle", 200, listing("2026-03-01"))
    run = fetch_one(site)
    assert run.listing == "robots" and "/private/protokolle" in run.errors[0].message
    assert not site.calls_to(f"{HOST}/private/protokolle")


def test_redirect_to_a_disallowed_document_is_not_followed(site):
    site.add(ROBOTS, 200, "User-agent: *\nDisallow: /private/\n")
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01"))
    site.add(DOC, 302, b"", {"location": "/private/2026-03-01.pdf"})
    site.add(f"{HOST}/private/2026-03-01.pdf", 200, PDF, {"content-type": "application/pdf"})
    run = fetch_one(site)
    assert [e.kind for e in run.errors] == ["robots"] and stored() == []
    assert not site.calls_to(f"{HOST}/private/2026-03-01.pdf")


def test_allowed_redirects_are_followed_and_every_hop_counted(site):
    site.add(f"{HOST}/protokolle", 301, b"", {"location": f"{HOST}/protokolle-neu"})
    site.add(f"{HOST}/protokolle-neu", 200, listing("2026-03-01"))
    site.add(DOC, 200, PDF, {"content-type": "application/pdf"})
    run = fetch_one(site)
    assert run.listing == "ok" and len(run.downloaded) == 1
    assert run.requests == 4  # robots.txt, listing, its redirect target, the document


class Recorded(httpx.SyncByteStream):
    """A response body that records each time it is read."""

    def __init__(self, content: bytes, reads: list) -> None:
        self.content, self.reads = content, reads

    def __iter__(self):
        self.reads.append(self.content)
        yield self.content


def test_a_followed_page_that_redirects_to_a_pdf_is_the_document_under_its_link(site):
    # an i-web publication with a single attachment redirects through /_doc/<id> to the file
    reads: list[bytes] = []
    site.add(
        f"{HOST}/publikationen",
        200,
        '<a href="/_rte/publikation/5">Jahresrechnung 2025</a>'
        '<a href="/_rte/publikation/6">Budget 2026</a>',
    )
    site.add(f"{HOST}/_rte/publikation/5", 200, '<a href="/_doc/1">Jahresrechnung 2025</a>')
    site.add(f"{HOST}/_doc/1", 200, PDF + b"1", {"content-type": "application/pdf"})
    site.add(f"{HOST}/_rte/publikation/6", 302, b"", {"location": "/_doc/2"})
    site.add(f"{HOST}/_doc/2", 302, b"", {"location": "/_docn/2/Budget.pdf"})
    site.answer(
        f"{HOST}/_docn/2/Budget.pdf",
        lambda request: httpx.Response(
            200, headers={"content-type": "application/pdf"}, stream=Recorded(PDF + b"2", reads)
        ),
    )
    c = commune(
        listing_url=f"{HOST}/publikationen",
        steps=[
            AdapterStep(selector="a[href*='/_rte/publikation/']"),
            AdapterStep(selector="a[href*='/_doc']"),
        ],
    )
    run = fetch_one(site, c)
    assert {d.url: (d.title, d.listing_url) for d in run.discovered} == {
        f"{HOST}/_doc/1": ("Jahresrechnung 2025", f"{HOST}/_rte/publikation/5"),
        f"{HOST}/_doc/2": ("Budget 2026", f"{HOST}/_rte/publikation/6"),
    }
    assert len(run.downloaded) == 2 and not run.errors
    # the walk read the file's headers, the download its body
    assert len(site.calls_to(f"{HOST}/_docn/2/Budget.pdf")) == 2 and len(reads) == 1


def test_a_redirect_loop_ends_as_an_http_error(site):
    site.add(f"{HOST}/protokolle", 302, b"", {"location": "/protokolle"})
    run = fetch_one(site)
    assert run.listing == "http" and "redirects" in run.errors[0].message


# --- run log --------------------------------------------------------------------------------------


def test_every_source_run_writes_a_log_line_with_every_listed_link(site):
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01", "2026-01-15"))
    site.add(DOC, 200, PDF, {"content-type": "application/pdf"})
    site.add(f"{HOST}/docs/2026-01-15.pdf", 500, b"")
    run = fetch_one(site, commune())
    (line,) = list(runlog.read_runs())
    assert line == run.line()
    assert (line["commune_bfs_id"], line["commune"], line["adapter"], line["body"]) == (
        1,
        "Testwil",
        "html_links",
        "assembly",
    )
    assert (
        line["listing_url"] == f"{HOST}/protokolle"
        and line["listing_status"] == "ok"
        and line["dry_run"] is False
    )
    assert (line["listed"], line["discovered"], line["new"], line["known"]) == (2, 2, 1, 0)
    assert (line["date_min"], line["date_max"]) == ("2026-01-15", "2026-03-01")
    assert [link["date"] for link in line["links"]] == ["2026-03-01", "2026-01-15"]
    assert line["links"][0] == {"url": DOC, "date": "2026-03-01", "title": "Protokoll 2026-03-01"}
    assert line["errors"] == [
        {"kind": "http", "url": f"{HOST}/docs/2026-01-15.pdf", "message": "HTTP 500"}
    ]
    assert (
        line["requests"] == 6
    )  # robots.txt, listing, three attempts at the failing document, the good one
    assert line["started"].startswith("20") and line["duration_s"] >= 0
    assert runlog.last_runs() == {(1, "html_links", f"{HOST}/protokolle"): line}


def test_dry_runs_are_logged_but_marked(site):
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01"))
    fetch_one(site, dry_run=True)
    (line,) = list(runlog.read_runs())
    assert line["dry_run"] is True and line["discovered"] == 1 and line["new"] == 0
    assert runlog.last_runs() == {} and len(runlog.last_runs(include_dry=True)) == 1


def test_a_failed_listing_is_logged_with_its_kind_not_as_empty(site):
    site.add(f"{HOST}/protokolle", 503, b"")
    fetch_one(site)
    (line,) = list(runlog.read_runs())
    assert (
        line["listing_status"] == "http"
        and line["listed"] == 0
        and line["errors"][0]["kind"] == "http"
    )


def test_one_line_per_source_of_a_multi_source_commune(site):
    c = commune()
    c.sources = [
        SourceConfig(
            listing_urls=[f"{HOST}/archiv"],
            adapter_config=AdapterConfig(steps=[AdapterStep(selector="a")]),
        )
    ]
    site.add(f"{HOST}/protokolle", 200, listing("2026-03-01"))
    site.add(DOC, 200, PDF, {"content-type": "application/pdf"})
    site.add(f"{HOST}/archiv", 200, "<html>empty archive</html>")
    client, polite = crawl(site)
    res = fetch.fetch_commune(c, client, polite, {})
    assert [r.listing for r in res.runs] == ["ok", "empty"] and len(res.downloaded) == 1
    assert [link["listing_url"] for link in runlog.read_runs()] == [
        f"{HOST}/protokolle",
        f"{HOST}/archiv",
    ]


# --- form steps ----------------------------------------------------------------------------------

FORM_FIXTURES = Path(__file__).parent / "fixtures" / "fetch_form" / "bellinzona"


def test_form_step_posts_the_hidden_inputs_with_each_configured_value(site):
    """Bellinzona's older legislatures: the select inside form#fric_11761 only works as a POST
    carrying the page's hidden session fields (recorded 2026-10-18)."""
    page = (FORM_FIXTURES / "sedute.html").read_text()
    older = (FORM_FIXTURES / "legislatura-570.html").read_text()
    posted: list[dict[str, str]] = []

    def sedute(request):
        if request.method == "GET":
            return httpx.Response(200, text=page, headers={"set-cookie": "sid=abc; Path=/"})
        fields = dict(parse_qsl(request.content.decode()))
        posted.append(fields)
        return httpx.Response(200, text=older if fields["fric_11761_cat0"] == "570" else "<p></p>")

    site.answer(f"{HOST}/Sedute", sedute)
    step = AdapterStep(
        selector="a[href*='Seduta-']",
        form=FormSubmit(
            selector="form#fric_11761",
            values=[{"fric_11761_cat0": "569"}, {"fric_11761_cat0": "570"}],
        ),
    )
    run = fetch_one(site, commune(listing_url=f"{HOST}/Sedute", steps=[step]), dry_run=True)
    assert [d.url for d in run.discovered] == [
        f"{HOST}/Seduta-straordinaria-del-19022024-f8de8100",
        f"{HOST}/Seduta-straordinaria-del-29012024-13973e00",
        f"{HOST}/Seduta-ordinaria-del-18122023-146e7e00",
    ]  # the current legislature on the GET page is not read
    assert [p["fric_11761_cat0"] for p in posted] == ["569", "570"]
    assert all(p["id_para"] == "11761" and p["fricCF2"].startswith("destru#") for p in posted)
    posts = [r for r in site.calls_to(f"{HOST}/Sedute") if r.method == "POST"]
    assert all(r.headers["cookie"] == "sid=abc" for r in posts)
    assert all(r.headers["referer"] == f"{HOST}/Sedute" for r in posts)


def test_form_step_without_its_form_is_a_parse_failure(site):
    site.add(f"{HOST}/Sedute", 200, "<html><body><p>no form</p></body></html>")
    step = AdapterStep(
        selector="a",
        form=FormSubmit(selector="form#fric_11761", values=[{"fric_11761_cat0": "569"}]),
    )
    run = fetch_one(site, commune(listing_url=f"{HOST}/Sedute", steps=[step]), dry_run=True)
    assert run.listing == "parse" and not [r for r in site.calls if r.method == "POST"]


def test_form_submissions_resolve_the_action_without_its_fragment():
    page = (FORM_FIXTURES / "sedute.html").read_text()
    form = FormSubmit(selector="form#fric_11761", values=[{"fric_11761_cat0": "569"}])
    ((action, fields),) = fetch.form_submissions(page, "https://www.bellinzona.ch/Sedute", form)
    assert action == "https://www.bellinzona.ch/Sedute"
    assert fields["nomeform"] == "fric_11761" and fields["fric_11761_cat0"] == "569"
    assert "SID" in fields and fields["SID"] == ""
