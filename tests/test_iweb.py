"""Inventory traversal and restart behaviour, with recorded markup and mock HTTP only."""

import json
from pathlib import Path

import httpx
import pytest

from opencommune.config import settings
from opencommune.crawl import http, iweb, store
from opencommune.register import RegisterEntry
from opencommune.schemas import AdapterConfig, AdapterStep, CommuneConfig, DocumentLink

HOST = "https://example.test"
PDF = b"%PDF-1.4\narchive test\n%%EOF"


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.ensure_dirs()
    monkeypatch.setattr(http.Politeness, "wait", lambda self, url: None)
    monkeypatch.setattr(http, "_pause", lambda seconds: None)
    routes = {"/robots.txt": (200, "User-agent: *\nAllow: /")}
    calls = []

    def answer(request):
        calls.append(str(request.url))
        route = routes.get(request.url.raw_path.decode(), (404, "absent"))
        if callable(route):
            return route(request)
        status, body, *headers = route
        return httpx.Response(status, content=body, headers=headers[0] if headers else {})

    with http.make_client(httpx.MockTransport(answer)) as client:
        yield routes, calls, client, http.Politeness(client, 0, settings.ua)


def entry():
    return RegisterEntry(bfs_id=1, name="Example", canton="ZH", website=HOST, vendor="i-web")


def config():
    return CommuneConfig(
        bfs_id=1,
        name="Example",
        canton="ZH",
        website=HOST,
        language="de",
        body="assembly",
        vendor="i-web",
        listing_urls=[HOST],
        adapter_config=AdapterConfig(steps=[AdapterStep(selector="a")]),
    )


def test_recorded_embedded_session_table_is_not_a_three_meeting_sample():
    html = Path("tests/fixtures/html_links/zollikon_iweb_embedded_table/input.html").read_text()
    _, pages, _ = iweb.page_links(
        html, "https://www.zollikon.ch/sitzung", "https://www.zollikon.ch"
    )
    assert len([u for u in pages if "/_rte/anlass/" in u]) == 68


def test_all_titles_future_dates_pagination_and_explicit_non_pdf():
    html = """<a href="/_doc/1">Formular</a><a href="/budget.pdf">Budget 2099-01-01</a>
    <a href="/_doc/1">Download</a>
    <tr><td><a href="/_doc/2">Planunterlagen</a>
    <span class="icms-document-type-and-size">(ZIP, 29.42 MB)</span></td></tr>
    <a href="/_rte/anlass/99">Session</a><a rel="next" href="/list?page=2">Weiter</a>
    <a href="https://other.test/sitzung">Other municipality</a><a href="/logout">Logout</a>"""
    docs, pages, excluded = iweb.page_links(html, HOST, HOST)
    assert [d["url"] for d in docs] == [HOST + "/_doc/1", HOST + "/budget.pdf"]
    assert docs[1]["date"] == "2099-01-01"
    assert list(pages) == [HOST + "/_rte/anlass/99", HOST + "/list?page=2"]
    assert pages[HOST + "/_rte/anlass/99"]["title"] == "Session"
    assert pages[HOST + "/_rte/anlass/99"]["listing_url"] == HOST
    assert excluded[0]["url"] == HOST + "/_doc/2"


def test_budget_resume_and_listings_are_retained_without_refetch(site):
    routes, calls, client, polite = site
    routes["/"] = (200, '<a href="/_doc/1">Formular</a><a href="/archiv">Archiv</a>')
    routes["/archiv"] = (200, '<a href="/_doc/1">Formular</a><a href="/archiv">Self</a>')
    first = iweb.discover_one(entry(), client, polite, page_budget=1)
    assert iweb.summary(first)["pending_pages"] == 6
    assert not iweb.summary(first)["listing_queue_exhausted"]
    second = iweb.discover_one(entry(), client, polite, page_budget=20)
    assert iweb.summary(second)["listing_queue_exhausted"]
    assert calls.count(HOST + "/") == 1
    assert second["documents"][HOST + "/_doc/1"]["listings"] == [HOST + "/", HOST + "/archiv"]
    before = calls.copy()
    iweb.discover_one(entry(), client, polite)
    assert calls == before


@pytest.mark.parametrize(
    "label",
    [
        "Jahresbericht",
        "Geschäftsbericht",
        "Finanzplan",
        "Steuerfuss",
        "Finances",
        "Comptes annuels",
        "Rapport de gestion",
        "Budget",
        "Bilancio",
        "Rendiconto",
        "Piano finanziario",
        "Preventivo",
        "Consuntivo",
    ],
)
def test_financial_navigation_discovers_documents_without_topic_filter(site, label):
    routes, calls, client, polite = site
    routes["/"] = (200, f'<a href="/section/42">{label}</a>')
    routes["/section/42"] = (200, '<a href="/_doc/123">2025</a>')
    state = iweb.discover_one(entry(), client, polite, page_budget=20)
    assert HOST + "/section/42" in calls
    assert HOST + "/_doc/123" in state["documents"]


def test_linked_404_is_failure_and_retry_is_explicit(site):
    routes, calls, client, polite = site
    routes["/"] = (200, '<a href="/archiv/2020">Archiv</a>')
    state = iweb.discover_one(entry(), client, polite)
    assert iweb.summary(state)["failed_pages"] == 1
    assert not iweb.summary(state)["listing_queue_exhausted"]
    routes["/archiv/2020"] = (200, '<a href="/doc.pdf">Budget</a>')
    iweb.discover_one(entry(), client, polite)
    assert calls.count(HOST + "/archiv/2020") == 1
    state = iweb.discover_one(entry(), client, polite, retry_failed=True)
    assert iweb.summary(state)["listing_queue_exhausted"]
    assert iweb.summary(state)["document_candidates"] == 1


def test_short_pass_reaches_linked_publication_before_event_backlog(site):
    routes, calls, client, polite = site
    events = [f"/_rte/anlass/{n}" for n in range(30)]
    routes["/"] = (
        200,
        "".join(f'<a href="{u}">Session</a>' for u in events)
        + '<a href="/_rte/publikation/12">Reglement</a>',
    )
    routes["/_rte/publikation/12"] = (200, '<a href="/reglement.pdf">Reglement</a>')
    state = iweb.discover_one(entry(), client, polite, page_budget=2)
    assert list(state["documents"]) == [HOST + "/reglement.pdf"]
    assert calls == [HOST + "/robots.txt", HOST + "/", HOST + "/_rte/publikation/12"]
    assert state["pending"] == [HOST + p for p in (*iweb.SEEDS[1:], *events)]


def test_saved_queue_prioritises_publications_stably_and_resumes_every_page(site):
    routes, calls, client, polite = site
    paths = [
        "/_rte/anlass/1",
        "/archiv",
        "/_rte/publikation/20?view=full",
        "/_rte/anlass/2",
        "/_rte/publikation/10",
    ]
    state = iweb.initial(entry())
    state["pending"] = [HOST + p for p in paths]
    iweb.save(state)
    for path in paths:
        routes[path] = (200, "<p>Public page</p>")
    state = iweb.discover_one(entry(), client, polite, page_budget=1)
    assert list(state["pages"]) == [HOST + paths[2]]
    assert state["pending"] == [HOST + paths[n] for n in (4, 0, 1, 3)]
    state = iweb.discover_one(entry(), client, polite, page_budget=4)
    assert not state["pending"]
    assert calls[1:] == [HOST + paths[n] for n in (2, 4, 0, 1, 3)]
    assert set(state["pages"]) == {HOST + p for p in paths}


def test_robots_denial_remains_visible_and_stops_host(site):
    routes, calls, client, polite = site
    routes["/robots.txt"] = (200, "User-agent: *\nDisallow: /\n")
    state = iweb.discover_one(entry(), client, polite)
    assert calls == [HOST + "/robots.txt"]
    assert iweb.summary(state)["failed_pages"] == 1
    assert iweb.summary(state)["pending_pages"] == 5


def test_download_resume_content_dedup_and_failures(site):
    routes, calls, client, polite = site
    routes["/"] = (200, "".join(f'<a href="/_doc/{n}">Document {n}</a>' for n in range(4)))
    routes["/_doc/0"] = (200, PDF)
    routes["/_doc/1"] = (200, PDF)  # duplicate bytes under another URL
    routes["/_doc/2"] = (200, "not a PDF")
    state = iweb.discover_one(entry(), client, polite)
    index = {}
    iweb.download_one(state, config(), client, polite, index, limit=1)
    state = iweb.read(1)
    iweb.download_one(state, config(), client, polite, store.load_index())
    assert iweb.summary(state)["held_urls"] == 2
    assert iweb.summary(state)["failed_downloads"] == 1
    assert iweb.summary(state)["non_pdf"] == 1
    assert len(list(settings.raw_dir.glob("*.pdf"))) == 1
    before = calls.copy()
    iweb.download_one(iweb.read(1), config(), client, polite, store.load_index())
    assert calls == before
    sidecar = json.loads(next(settings.raw_dir.glob("*.json")).read_text())
    assert sidecar["listing_title"] == "Document 0"
    assert sidecar["listing_url"] == HOST + "/"


def test_queue_lock_prevents_a_second_crawler(site):
    with iweb.locked(), pytest.raises(ValueError, match="already running"), iweb.locked():
        pass


def test_refresh_finds_new_documents_without_discarding_previous_inventory(site):
    routes, _, client, polite = site
    routes["/"] = (200, '<a href="/old.pdf">Budget</a>')
    iweb.discover_one(entry(), client, polite)
    routes["/"] = (200, '<a href="/new.pdf">Reglement</a>')
    assert len(iweb.discover_one(entry(), client, polite)["documents"]) == 1
    state = iweb.discover_one(entry(), client, polite, refresh=True)
    assert set(state["documents"]) == {HOST + "/old.pdf", HOST + "/new.pdf"}


def test_refresh_keeps_an_unfinished_queue_and_reads_its_listings_first(site):
    routes, calls, client, polite = site
    events = [f"/_rte/anlass/{n}" for n in range(4)]
    home = '<a href="/archiv">Archiv</a>' + "".join(f'<a href="{u}">Sitzung</a>' for u in events)
    routes["/"] = (200, home)
    routes["/archiv"] = (200, '<a href="/alt.pdf">Budget</a>')
    for path in events:
        routes[path] = (200, "<p>Sitzung</p>")
    seeds = [HOST + p for p in iweb.SEEDS]
    state = iweb.discover_one(entry(), client, polite, page_budget=len(seeds) + 2)
    assert state["pending"] == [HOST + p for p in events[1:]]
    routes["/"] = (200, home + '<a href="/neu.pdf">Reglement</a>')
    state = iweb.discover_one(entry(), client, polite, refresh=True, page_budget=1)
    assert HOST + "/neu.pdf" in state["documents"]
    assert state["pending"] == [*seeds[1:], HOST + "/archiv", *(HOST + p for p in events[1:])]
    iweb.discover_one(entry(), client, polite)
    assert calls.count(HOST + events[0]) == 1
    assert calls.count(HOST + "/archiv") == 2
    # a finished walk starts over
    state = iweb.discover_one(entry(), client, polite, refresh=True, page_budget=0)
    assert state["pending"] == seeds and not state["pages"]


def test_a_commune_with_a_parliament_reads_more_pages_and_comes_up_sooner(site, monkeypatch):
    from contextlib import nullcontext

    from opencommune import communes
    from opencommune.register import Publication

    routes, calls, client, _polite = site
    monkeypatch.setattr(communes, "bfs_register", lambda: {})
    monkeypatch.setattr(http, "make_client", lambda **kw: nullcontext(client))
    monkeypatch.setattr(http.Politeness, "_network", lambda *args: "network")
    town = RegisterEntry(
        bfs_id=2,
        name="Town",
        canton="ZH",
        website=HOST,
        vendor="i-web",
        publications=[Publication(body="parliament", kind="minutes", status="candidate")],
    )
    queued = {}
    for commune, read_before in ((entry(), 4), (town, 12)):
        state = iweb.initial(commune)
        state["pages"] = {
            f"{HOST}/{commune.bfs_id}/{n}": {"status": "absent"} for n in range(read_before)
        }
        queued[commune.bfs_id] = [f"/_rte/anlass/{commune.bfs_id}{n}" for n in range(6)]
        state["pending"] = [HOST + p for p in queued[commune.bfs_id]]
        iweb.save(state)
        for path in queued[commune.bfs_id]:
            routes[path] = (200, "<p>Sitzung</p>")
    # 12 pages read count as 3 for the town, fewer than the 4 of the other commune
    iweb.run([entry(), town], mode="sync", page_budget=1, emit=lambda _: None)
    read = [url.removeprefix(HOST) for url in calls if "/_rte/" in url]
    assert read == [*queued[2][: iweb.PARLIAMENT_SHARE], queued[1][0]]


def test_deadline_leaves_pages_and_downloads_queued(site):
    routes, calls, client, polite = site
    routes["/"] = (200, '<a href="/doc.pdf">Budget</a>')
    state = iweb.discover_one(entry(), client, polite, deadline=0)
    assert len(state["pending"]) == len(iweb.SEEDS)
    assert calls == []
    state = iweb.discover_one(entry(), client, polite)
    before = calls.copy()
    iweb.download_one(state, config(), client, polite, {}, deadline=0)
    assert calls == before
    assert not state["downloads"]


def test_redirect_target_is_not_crawled_again_when_linked_by_its_own_page(site):
    routes, calls, client, polite = site
    routes["/"] = (200, '<a href="/_rte/publikation/1">Publikation</a>')
    routes["/_rte/publikation/1"] = (302, "", {"location": "/publikationen/1"})
    routes["/publikationen/1"] = (
        200,
        '<a href="/publikationen/1">Permalink</a><a href="/a.pdf">PDF</a>',
    )
    state = iweb.discover_one(entry(), client, polite)
    assert iweb.summary(state)["listing_queue_exhausted"]
    assert calls.count(HOST + "/publikationen/1") == 1
    assert state["documents"][HOST + "/a.pdf"]["listing_url"] == HOST + "/publikationen/1"


class Body(httpx.SyncByteStream):
    """A response body that records each time it is read."""

    def __init__(self, content: bytes, reads: list):
        self.content, self.reads = content, reads

    def __iter__(self):
        self.reads.append(self.content)
        yield self.content


def single_attachment(routes, reads, publication=5, doc=7, name="ZA_Vereinb.pdf"):
    """An i-web publication with one attachment: it redirects through the /_doc/ route to the file."""
    routes[f"/_rte/publikation/{publication}"] = (
        302,
        "",
        {"location": f"/publikationen/{publication}"},
    )
    routes[f"/publikationen/{publication}"] = (302, "", {"location": f"/_doc/{doc}"})
    routes[f"/_doc/{doc}"] = (302, "", {"location": f"/_docn/{doc}/{name}"})
    routes[f"/_docn/{doc}/{name}"] = lambda request: httpx.Response(
        200, headers={"content-type": "application/pdf"}, stream=Body(PDF, reads)
    )


def test_a_publication_that_redirects_to_a_pdf_is_a_document_with_its_link(site):
    routes, calls, client, polite = site
    reads = []
    routes["/"] = (200, '<a href="/publikationen">Publikationen</a>')
    routes["/publikationen"] = (
        200,
        (
            "<table><tr><td>Zusammenarbeitsvereinbarung</td><td>01.02.2026</td>"
            '<td><a href="/_rte/publikation/5">Zusammenarbeitsvereinbarung</a></td></tr></table>'
        ),
    )
    single_attachment(routes, reads)
    state = iweb.discover_one(entry(), client, polite)
    assert state["pages"][HOST + "/_rte/publikation/5"] == {
        "status": "file",
        "url": HOST + "/_doc/7",
        "content_type": "application/pdf",
    }
    assert state["documents"] == {
        HOST + "/_doc/7": {
            "url": HOST + "/_doc/7",
            "title": "Zusammenarbeitsvereinbarung",
            "date": "2026-02-01",
            "date_precision": "day",
            "listing_url": HOST + "/publikationen",
            "via": HOST + "/_rte/publikation/5",
            "listings": [HOST + "/publikationen"],
        }
    }
    assert iweb.summary(state)["failed_pages"] == 0
    assert iweb.summary(state)["listing_queue_exhausted"]
    assert not state["linked_from"]
    assert reads == []  # discovery read the headers only
    iweb.download_one(state, config(), client, polite, store.load_index())
    assert state["downloads"][HOST + "/_doc/7"]["status"] == "held"
    assert reads == [PDF] and calls.count(HOST + "/_docn/7/ZA_Vereinb.pdf") == 2
    sidecar = json.loads(next(settings.raw_dir.glob("*.json")).read_text())
    assert sidecar["source_url"] == HOST + "/_doc/7"
    assert sidecar["listing_url"] == HOST + "/publikationen"
    assert sidecar["listing_title"] == "Zusammenarbeitsvereinbarung"
    assert sidecar["listing_date"] == "2026-02-01"


def test_a_file_linked_directly_and_through_a_publication_is_one_document(site):
    routes, _calls, client, polite = site
    reads = []
    routes["/"] = (
        200,
        '<a href="/_doc/7">Reglement</a><a href="/_rte/publikation/5">Reglement (Publikation)</a>',
    )
    single_attachment(routes, reads)
    state = iweb.discover_one(entry(), client, polite)
    assert list(state["documents"]) == [HOST + "/_doc/7"]
    assert state["documents"][HOST + "/_doc/7"]["title"] == "Reglement"  # the first link wins
    iweb.download_one(state, config(), client, polite, store.load_index())
    assert reads == [PDF] and len(list(settings.raw_dir.glob("*.pdf"))) == 1
    # a file held under the publication's own URL (a curated source lists those) is not fetched again
    routes["/"] = (200, '<a href="/_rte/publikation/6">Budget</a>')
    single_attachment(routes, reads, publication=6, doc=8)
    store._download(
        DocumentLink(
            url=HOST + "/_rte/publikation/6", title="Budget", body="assembly", listing_url=HOST
        ),
        config(),
        client,
        polite,
        store.load_index(),
    )
    assert len(reads) == 2
    state = iweb.discover_one(entry(), client, polite, refresh=True)
    assert state["documents"][HOST + "/_doc/8"]["via"] == HOST + "/_rte/publikation/6"
    iweb.download_one(state, config(), client, polite, store.load_index())
    assert state["downloads"][HOST + "/_doc/8"]["status"] == "held" and len(reads) == 2


def test_a_page_that_serves_a_file_itself_keeps_its_url_and_other_files_are_excluded(site):
    routes, _calls, client, polite = site
    routes["/"] = (
        200,
        (
            '<a href="/online-schalter/1/download">Gesuch Reglement</a>'
            '<a href="/online-schalter/2/download">Formular Download</a>'
            '<a href="/_rte/information/3">Information Dokument</a>'
            '<a href="/_rte/information/4">Information Dokument</a>'
        ),
    )
    pdf = {"content-type": "application/pdf", "content-disposition": 'inline; filename="R.pdf"'}
    routes["/online-schalter/1/download"] = (200, PDF, pdf)
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    routes["/online-schalter/2/download"] = (302, "", {"location": "/_doc/9"})
    routes["/_doc/9"] = (200, b"PK", {"content-type": docx})
    routes["/_rte/information/3"] = (
        200,
        b"PK",
        {
            "content-type": "application/octet-stream",
            "content-disposition": "attachment; filename*=UTF-8''Pl%C3%A4ne.zip",
        },
    )
    routes["/_rte/information/4"] = (
        200,
        PDF,
        {"content-type": "application/octet-stream", "content-disposition": "filename=Bericht.pdf"},
    )
    state = iweb.discover_one(entry(), client, polite)
    one, four = HOST + "/online-schalter/1/download", HOST + "/_rte/information/4"
    assert list(state["documents"]) == [one, four]
    assert state["documents"][one]["title"] == "Gesuch Reglement"
    assert state["documents"][one]["listing_url"] == HOST + "/"
    assert "via" not in state["documents"][one]
    assert state["excluded"] == {
        HOST + "/_doc/9": {"url": HOST + "/_doc/9", "reason": docx},
        HOST + "/_rte/information/3": {
            "url": HOST + "/_rte/information/3",
            "reason": "application/octet-stream",
        },
    }
    assert iweb.summary(state)["failed_pages"] == 0
    assert iweb.summary(state)["non_pdf"] == 2
    # a refresh of an unfinished queue reads the listings again, never the files
    state["pending"].append(HOST + "/_rte/anlass/1")
    iweb.save(state)
    state = iweb.discover_one(entry(), client, polite, refresh=True, page_budget=0)
    assert one not in state["pending"] and state["pages"][one]["status"] == "file"


def test_pages_saved_as_failed_files_are_queued_again_first_and_recovered(site):
    from opencommune.crawl import coverage_report

    routes, calls, client, polite = site
    reads = []
    single_attachment(routes, reads)
    routes["/archiv"] = (200, '<a href="/_doc/1">Budget</a>')
    old = iweb.initial(entry())
    old["pending"] = [HOST + "/archiv"]
    old["pages"] = {
        HOST + "/": {"status": "ok", "resolved_url": HOST + "/", "documents": 0},
        HOST + "/_rte/publikation/5": {
            "status": "failed",
            "kind": "content_type",
            "message": "listing is not HTML",
        },
        HOST + "/_rte/anlass/2": {"status": "failed", "kind": "http", "message": "HTTP 500"},
    }
    iweb.save(old)
    assert coverage_report.checkpoint("iweb", iweb.read(1))["failed_pages"] == 2
    state = iweb.discover_one(entry(), client, polite, page_budget=1)
    hops = ["/_rte/publikation/5", "/publikationen/5", "/_doc/7", "/_docn/7/ZA_Vereinb.pdf"]
    assert calls == [HOST + "/robots.txt", *(HOST + p for p in hops)]
    assert state["pages"][HOST + "/_rte/publikation/5"]["status"] == "file"
    assert state["documents"][HOST + "/_doc/7"]["title"] == "ZA_Vereinb.pdf"
    assert state["documents"][HOST + "/_doc/7"]["listing_url"] == HOST + "/_rte/publikation/5"
    assert state["pending"] == [HOST + "/archiv"]
    report = coverage_report.checkpoint("iweb", state)
    assert report["failed_pages"] == 1 and report["failure_kinds"] == {"http": 1}
    assert report["candidate_urls"] == 1 and report["unattempted_urls"] == 1
    iweb.discover_one(entry(), client, polite)
    assert calls.count(HOST + "/_rte/publikation/5") == 1  # recovered once, not again


def test_automatic_archive_identity_and_review_queue(site, monkeypatch):
    from opencommune import communes

    monkeypatch.setattr(communes, "bfs_register", lambda: {1: ("Example (ZH)", "ZH")})
    state = iweb.initial(entry())
    state["documents"][HOST + "/test.pdf"] = {"url": HOST + "/test.pdf"}
    source = iweb.archive_source(entry(), state)
    assert source.body == "unknown"
    assert state["onboarding"]["status"] == "eligible"
    state["website"] = "https://different.test"
    assert iweb.archive_source(entry(), state) is None
    assert state["onboarding"]["status"] == "review"


def test_unconfigured_commune_sync_downloads_and_resumes(site, monkeypatch):
    from contextlib import nullcontext

    from opencommune import communes
    from opencommune.schemas import RawSidecar

    routes, calls, client, _polite = site
    routes["/"] = (
        200,
        '<a href="/test.pdf">Formular</a><a href="https://other.test/a.pdf">External</a>',
    )
    routes["/test.pdf"] = (200, PDF)
    monkeypatch.setattr(communes, "bfs_register", lambda: {1: ("Example (ZH)", "ZH")})
    monkeypatch.setattr(http, "make_client", lambda **kw: nullcontext(client))
    monkeypatch.setattr(http.Politeness, "_network", lambda *args: "network")
    iweb.run([entry()], mode="sync", page_budget=1, emit=lambda _: None)
    state = iweb.read(1)
    assert state["downloads"][HOST + "/test.pdf"]["status"] == "held"
    assert state["review_documents"] == {"https://other.test/a.pdf": "external_document_or_listing"}
    assert not any("other.test" in url for url in calls)
    sidecar = RawSidecar.model_validate_json(next(settings.raw_dir.glob("*.json")).read_text())
    assert sidecar.body == "unknown" and sidecar.commune_bfs_id == 1
    iweb.run([entry()], mode="fetch", emit=lambda _: None)
    assert calls.count(HOST + "/test.pdf") == 1


def test_scheme_change_keeps_the_inventory(site):
    _routes, _calls, client, polite = site
    old = iweb.initial(entry())
    old["website"] = "http://www.example.test"
    iweb.save(old)
    state = iweb.discover_one(entry(), client, polite, page_budget=0)
    assert state["website"] == HOST


def test_changed_website_skips_only_that_commune(site, monkeypatch):
    from contextlib import nullcontext

    routes, _calls, client, _polite = site
    routes["/"] = (200, "<p>nothing</p>")
    monkeypatch.setattr(http, "make_client", lambda **kw: nullcontext(client))
    monkeypatch.setattr(http.Politeness, "_network", lambda *args: "network")
    moved = RegisterEntry(bfs_id=2, name="Moved", canton="ZH", website=HOST, vendor="i-web")
    old = iweb.initial(moved)
    old["website"] = "https://elsewhere.test"
    iweb.save(old)
    lines = []
    iweb.run([moved, entry()], mode="discover", page_budget=1, emit=lines.append)
    assert json.loads(lines[0]) == {"bfs_id": 2, "status": "website_changed"}
    assert json.loads(lines[1])["bfs_id"] == 1
    assert iweb.read(2)["website"] == "https://elsewhere.test"


def test_a_saved_checkpoint_records_when_it_was_updated():
    # the coverage report reads `updated_at`, as for the other collectors
    iweb.save({"bfs_id": 99999})
    assert iweb.read(99999)["updated_at"]
    iweb.directory().joinpath("99999.json").unlink()
