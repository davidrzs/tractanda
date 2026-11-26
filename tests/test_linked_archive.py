"""Reviewed PDF sources: resumability, attribution boundaries and untagged catalogue entries."""

import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.orm import Session

from opencommune import catalog, register
from opencommune.config import settings
from opencommune.crawl import http, store
from opencommune.crawl import linked_archive as archive
from opencommune.crawl.sites.finsta import FinstaExports, ReportChoice
from opencommune.db import _make_engine, init_db
from opencommune.models import CatalogPDF
from opencommune.register import RegisterEntry

HOST = "https://commune.test"
SHARED = "https://shared.test"


def source(**kwargs):
    return archive.LinkedSource(bfs_id=1, website=HOST, vendor="drupal", **kwargs)


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.ensure_dirs()
    monkeypatch.setattr(http.Politeness, "wait", lambda self, url: None)
    monkeypatch.setattr(http, "ATTEMPTS", 1)
    monkeypatch.setattr(archive, "bfs_register", lambda: {1: ("Commune", "ZH")})
    entry = RegisterEntry(bfs_id=1, name="Commune", canton="ZH", website=HOST, vendor="drupal")
    monkeypatch.setattr(register, "load_register", lambda: [entry])
    routes, calls = {}, []

    def respond(request):
        url = str(request.url)
        calls.append(url)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return routes.get(url, httpx.Response(404))

    with http.make_client(httpx.MockTransport(respond)) as client:
        yield entry, routes, calls, client, http.Politeness(client, 0, settings.ua)


def pdf(text):
    return httpx.Response(200, content=b"%PDF-1.4\n" + text.encode() + b"\n%%EOF")


def run(site, config, **kwargs):
    entry, _, _, client, polite = site
    return archive.fetch_one(
        config, entry, client, polite, store.load_index(), limit=kwargs.pop("limit", 1), **kwargs
    )


def test_seed_first_resume_listing_refresh_and_no_inferred_metadata(site):
    _, routes, calls, _, _ = site
    config = source(document_urls=[HOST + "/seed.pdf"], listing_urls=[HOST + "/docs"])
    routes[HOST + "/seed.pdf"] = pdf("seed")
    routes[HOST + "/other.pdf"] = pdf("other")
    routes[HOST + "/docs"] = httpx.Response(
        200, text='<base href="/"> <a href="other.pdf">Private Name 2026-09-01</a>'
    )
    state = run(site, config)
    assert len(state["downloads"]) == 1 and HOST + "/docs" not in calls
    state = run(site, config)
    assert state["downloads"][HOST + "/other.pdf"]["status"] == "held"
    before = list(calls)
    assert run(site, config)["status"] == "complete"
    assert calls == before
    routes[HOST + "/docs"] = httpx.Response(200, text='<a href="new.pdf">New</a>')
    routes[HOST + "/new.pdf"] = pdf("new")
    assert run(site, config, refresh=True)["downloads"][HOST + "/new.pdf"]["status"] == "held"
    assert calls.count(HOST + "/seed.pdf") == 1
    for path in settings.raw_dir.glob("*.json"):
        sidecar = json.loads(path.read_text())
        assert sidecar["body"] == "unknown" and sidecar["listing_date"] is None
        assert sidecar["listing_title"] == ""


def test_opaque_iweb_links_require_pdf_bytes_and_preserve_unknown_metadata(site):
    _, routes, calls, _, _ = site
    config = source(listing_urls=[HOST + "/accounts"])
    routes[HOST + "/accounts"] = httpx.Response(
        200,
        text='<a href="/_doc/123">Jahresrechnung</a>'
        '<a href="/_doc/124">Spreadsheet</a><a href="/_doc/not-a-number">Page</a>',
    )
    routes[HOST + "/_doc/123"] = pdf("accounts")
    routes[HOST + "/_doc/124"] = httpx.Response(200, content=b"PK not a PDF")
    state = run(site, config, limit=10)
    assert state["downloads"][HOST + "/_doc/123"]["status"] == "held"
    assert state["downloads"][HOST + "/_doc/124"]["status"] == "failed"
    assert HOST + "/_doc/not-a-number" not in calls
    sidecars = list(settings.raw_dir.glob("*.json"))
    assert len(sidecars) == 1
    record = json.loads(sidecars[0].read_text())
    assert record["body"] == "unknown" and record["listing_title"] == ""


def test_shared_host_exact_selection_rejects_redirect_to_other_commune(site):
    _, routes, calls, _, _ = site
    selected = SHARED + "/selected.pdf"
    config = source(document_urls=[selected], attribution_note="First-page identity checked")
    routes[selected] = httpx.Response(302, headers={"location": SHARED + "/other-commune.pdf"})
    state = run(site, config)
    assert state["downloads"][selected]["kind"] == "external"
    assert SHARED + "/other-commune.pdf" not in calls
    assert config.allows(selected) and not config.allows(SHARED + "/other.pdf")
    with pytest.raises(ValueError, match="attribution"):
        source(document_urls=[selected])
    with pytest.raises(ValueError, match="listings"):
        source(listing_urls=[SHARED + "/all-documents"], attribution_note="Shared")


def test_financial_publisher_preserves_unknown_municipal_vendor_and_bfs_scope(site):
    entry, routes, calls, client, polite = site
    entry = entry.model_copy(update={"vendor": None})
    selected = SHARED + "/report?BFS_Nr=1&rs:Format=PDF"
    config = archive.LinkedSource(
        bfs_id=1,
        website=HOST,
        vendor=None,
        document_urls=[selected],
        attribution_note="Official financial report; BFS identity checked in HTML output.",
    )
    assert archive.validate_sources([config], [entry]) == [config]
    other = selected.replace("BFS_Nr=1", "BFS_Nr=2")
    routes[selected] = httpx.Response(302, headers={"location": other})
    state = archive.fetch_one(config, entry, client, polite, store.load_index(), limit=1)
    assert state["downloads"][selected]["kind"] == "external"
    assert other not in calls
    assert HOST + "/robots.txt" not in calls
    routes[selected] = pdf("financial expenditure report")
    state = archive.fetch_one(
        config, entry, client, polite, store.load_index(), limit=1, retry_failed=True
    )
    assert state["downloads"][selected]["status"] == "held"


def test_financial_year_expansion_preserves_checkpoint_and_catalogue_provenance(site):
    _, routes, calls, _, _ = site

    def financial(years):
        return source(
            finsta_exports=FinstaExports(
                endpoint=SHARED + "/native-report",
                folder="/Reports_BE/",
                years=years,
                reports=[ReportChoice(name="Bil_Aktiven_DE")],
            ),
            attribution_note="Reviewed publisher options and municipal BFS identity",
        )

    old = financial([2016])
    held = old.selected_documents[0]
    routes[held] = pdf("2016 accounts")
    assert run(site, old)["downloads"][held]["status"] == "held"
    expanded = financial([2016, 2024])
    newer = expanded.selected_documents[1]
    routes[newer] = pdf("2024 accounts")
    state = run(site, expanded)
    assert state["downloads"][held]["status"] == "held"
    assert state["downloads"][newer]["status"] == "held"
    assert calls.count(held) == 1
    assert run(site, expanded)["status"] == "complete"
    with pytest.raises(ValueError, match="configuration changed"):
        run(site, financial([2024]))
    for path in settings.raw_dir.glob("*.json"):
        sidecar = store.RawSidecar.model_validate_json(path.read_text())
        assert expanded.accepts_sidecar(sidecar)
        assert sidecar.body == "unknown" and sidecar.listing_date is None


def test_listing_redirect_and_external_links_do_not_escape_scope(site):
    _, routes, calls, _, _ = site
    config = source(listing_urls=[HOST + "/docs"])
    routes[HOST + "/docs"] = httpx.Response(302, headers={"location": SHARED + "/all"})
    state = run(site, config)
    assert state["pages"][HOST + "/docs"]["kind"] == "external"
    assert SHARED + "/all" not in calls
    found = archive.documents(
        '<a href="https://shared.test/x.pdf">x</a><a href="/viewer?file=%2Fa.pdf">a</a>',
        HOST + "/docs",
        config,
    )
    assert found == {HOST + "/a.pdf": HOST + "/docs"}


def test_robots_and_throttling_stop_further_documents(site):
    _, routes, calls, _client, polite = site
    config = source(document_urls=[HOST + "/a.pdf", HOST + "/b.pdf"])
    routes[HOST + "/a.pdf"] = httpx.Response(429)
    state = run(site, config, limit=3)
    assert state["status"] == "blocked" and HOST + "/b.pdf" not in calls
    polite._robots["commune.test"] = http.RobotsRules("User-agent: *\nDisallow: /", settings.ua)
    before = list(calls)
    state = run(site, config, limit=3, retry_failed=True)
    assert state["status"] == "blocked" and calls == before


def test_configuration_identity_and_saved_inventory_must_match(site):
    entry, routes, calls, _, _ = site
    config = source(document_urls=[HOST + "/a.pdf"])
    assert archive.validate_sources([config], [entry]) == [config]
    with pytest.raises(ValueError, match="identity"):
        archive.validate_sources([config], [entry.model_copy(update={"canton": "BE"})])
    with pytest.raises(ValueError, match="duplicate"):
        archive.validate_sources([config, config], [entry])
    routes[HOST + "/a.pdf"] = pdf("a")
    run(site, config)
    before = list(calls)
    with pytest.raises(ValueError, match="configuration changed"):
        run(site, source(document_urls=[HOST + "/b.pdf"]))
    assert calls == before


def test_accepting_a_narrowed_configuration_resumes_without_the_removed_pages(site):
    _, routes, calls, _, _ = site
    wide = source(
        listing_urls=[HOST + "/docs", HOST + "/notices"],
        document_urls=[HOST + "/a.pdf", HOST + "/named.pdf"],
    )
    routes[HOST + "/a.pdf"] = pdf("a")
    routes[HOST + "/docs"] = httpx.Response(200, text='<a href="/x.pdf">x</a>')
    routes[HOST + "/x.pdf"] = pdf("x")
    routes[HOST + "/notices"] = httpx.Response(
        200, text='<a href="/held.pdf">held</a><a href="/gone.pdf">gone</a>'
    )
    routes[HOST + "/held.pdf"] = pdf("held")
    state = run(site, wide, limit=10)
    assert state["downloads"][HOST + "/named.pdf"]["status"] == "failed"
    assert state["downloads"][HOST + "/gone.pdf"]["status"] == "failed"
    narrow = source(listing_urls=[HOST + "/docs"], document_urls=[HOST + "/a.pdf"])
    with pytest.raises(ValueError, match="configuration changed"):
        run(site, narrow)
    assert archive.accept(narrow) == {
        "bfs_id": 1,
        "listings_dropped": 1,
        "pages_dropped": 1,
        "documents_dropped": 2,
    }
    assert archive.accept(narrow) is None
    before = list(calls)
    state = run(site, narrow, limit=10)
    assert calls == before
    assert state["status"] == "complete"
    assert set(state["downloads"]) == {HOST + "/a.pdf", HOST + "/x.pdf", HOST + "/held.pdf"}
    assert HOST + "/notices" not in state["frontier"]


def test_check_without_the_bfs_snapshot_skips_only_the_identity(site, monkeypatch):
    entry = site[0]
    config = source(document_urls=[HOST + "/a.pdf"])
    monkeypatch.setattr(archive, "bfs_register", lambda: {})
    with pytest.raises(ValueError, match="identity"):
        archive.validate_sources([config], [entry])
    assert archive.validate_sources([config], [entry], snapshot_optional=True) == [config]
    with pytest.raises(ValueError, match="identity"):
        archive.validate_sources(
            [config], [entry.model_copy(update={"vendor": "wordpress"})], snapshot_optional=True
        )


@pytest.mark.parametrize("shared", [False, True])
def test_new_commune_is_visible_only_after_pdf_download(site, monkeypatch, tmp_path, shared):
    _, routes, _, _, _ = site
    url = (SHARED if shared else HOST) + "/selected.pdf"
    config = source(
        document_urls=[url], attribution_note="Municipal identity checked" if shared else ""
    )
    monkeypatch.setattr(archive, "load_sources", lambda: [config])
    engine = _make_engine(f"sqlite:///{tmp_path}/catalog.db")
    init_db(engine)
    with Session(engine) as session:
        assert catalog.refresh(session, []) == 0
        routes[url] = pdf("municipal PDF")
        state = run(site, config)
        sha = state["downloads"][url]["sha256"]
        assert catalog.refresh(session, []) == 1
        row = session.get(CatalogPDF, sha)
        assert row.commune_bfs_id == 1 and row.title == "Gemeindedokument"
        assert row.body == "unknown" and row.source_date is None
    engine.dispose()
    fake = SimpleNamespace(
        adapter="linked_archive", body="unknown", listing_url=url, source_url=SHARED + "/other.pdf"
    )
    assert not config.accepts_sidecar(fake)


def test_opaque_assolo_links_are_downloaded_without_inferred_metadata(site):
    _, routes, _, _, _ = site
    listing = HOST + "/documents"
    opaque = HOST + "/Decisioni-1234abcd?i=1"
    no_query = HOST + "/Regolamento-2345bcde"
    routes[listing] = httpx.Response(
        200,
        text=f"""
      <a class="Link-Nero" href="{opaque}">Decisioni</a>
      <a class="ObjListDocLink" href="{no_query}">Regolamento</a>
      <a href="/Comune-abcdef12">Comune</a>
      <a href="/Pagina-abcdef12?i=2">Page</a>
    """,
    )
    routes[opaque] = pdf("decision")
    routes[no_query] = pdf("regulation")
    state = run(site, source(listing_urls=[listing]), limit=3)
    assert set(state["downloads"]) == {opaque, no_query}
    assert all(d["status"] == "held" for d in state["downloads"].values())


def test_reviewed_file_directory_allows_redirects_but_not_other_tenants(site):
    _, routes, _, _, _ = site
    original = HOST + "/download/file.pdf"
    target = SHARED + "/tenant-a/download/file.pdf"
    routes[original] = httpx.Response(302, headers={"location": target})
    routes[target] = pdf("municipal file on its reviewed asset host")
    config = source(
        document_urls=[original],
        document_url_prefixes=[SHARED + "/tenant-a/download/"],
        attribution_note="Official download redirects to this reviewed tenant directory.",
    )
    result = run(site, config)
    assert result["downloads"][original]["status"] == "held"
    assert not config.allows(SHARED + "/tenant-b/download/file.pdf")
    assert not config.allows(SHARED + "/tenant-a/download-other/file.pdf")
    assert not config.allows("https://shared.test.evil.test/tenant-a/download/file.pdf")


def test_document_prefix_needs_narrow_path_and_review_note():
    with pytest.raises(ValueError, match="directory path"):
        source(document_urls=[HOST + "/a.pdf"], document_url_prefixes=[SHARED + "/files/"])
    with pytest.raises(ValueError, match="directory path"):
        source(
            document_urls=[HOST + "/a.pdf"],
            document_url_prefixes=[SHARED + "/"],
            attribution_note="Reviewed",
        )


def test_old_checkpoint_signature_resumes_without_new_optional_field(site):
    _, routes, _, _, _ = site
    config = source(document_urls=[HOST + "/a.pdf"])
    routes[HOST + "/a.pdf"] = pdf("a")
    first = run(site, config)
    assert "document_url_prefixes" not in first["source"]
    assert run(site, config)["status"] == "complete"


def test_added_document_prefix_rechecks_successful_listing_and_keeps_downloads(site):
    _, routes, calls, _, _ = site
    listing = HOST + "/docs"
    local = HOST + "/local.pdf"
    external = SHARED + "/tenant-a/download/new.pdf"
    routes[listing] = httpx.Response(
        200, text=f'<a href="{local}">Local</a><a href="{external}">New</a>'
    )
    routes[local] = pdf("local")
    routes[external] = pdf("new")
    old = source(listing_urls=[listing])
    first = run(site, old, limit=3)
    assert first["status"] == "complete"
    assert set(first["downloads"]) == {local}

    expanded = source(
        listing_urls=[listing],
        document_url_prefixes=[SHARED + "/tenant-a/download/"],
        attribution_note="Reviewed municipal link to this tenant directory.",
    )
    resumed = run(site, expanded, limit=3)
    assert resumed["status"] == "complete"
    assert set(resumed["downloads"]) == {local, external}
    assert all(row["status"] == "held" for row in resumed["downloads"].values())
    assert calls.count(listing) == 2
    assert calls.count(local) == 1


def test_unicode_listing_seed_accepts_wire_url_but_rejects_different_path(site):
    _, routes, calls, _, _ = site
    seed = HOST + "/infoblätter/"
    wire = HOST + "/infobl%C3%A4tter/"
    other = HOST + "/infoblaetter/"
    doc = HOST + "/document.pdf"
    config = source(listing_urls=[seed], crawl_prefixes=[HOST + "/reviewed"])
    assert config.allows_listing(seed)
    assert config.allows_listing(wire)
    assert not config.allows_listing(other)
    assert not config.allows_listing(wire + "?page=2")

    routes[wire] = httpx.Response(200, text=f'<a href="{doc}">Document</a>')
    routes[doc] = pdf("document")
    state = run(site, config, limit=2)
    assert state["downloads"][doc]["status"] == "held"
    assert state["pages"][seed]["status"] == "ok"
    assert wire in calls

    routes[wire] = httpx.Response(302, headers={"location": other})
    routes[other] = httpx.Response(200, text="Unexpected path")
    state = run(site, config, limit=2, refresh=True)
    assert state["pages"][seed]["kind"] == "external"
    assert other not in calls


def test_opaque_download_listings_refresh_with_scope_checks():
    config = source(listing_urls=[HOST + "/docs"])
    html = """<a class="dropfiles_downloadlink" href="/files/68/2025/68/29092025">Minutes</a>
      <a href="/Download/F/105">Minutes</a>
      <a href="/Download/F/not-a-file">Navigation</a>
      <a class="dropfiles_downloadlink" href="https://other.test/file">Other</a>"""
    assert archive.documents(html, HOST + "/docs", config) == {
        HOST + "/files/68/2025/68/29092025": HOST + "/docs",
        HOST + "/Download/F/105": HOST + "/docs",
    }


@pytest.mark.parametrize("handler", ["sdm_process_download", "smd_process_download"])
def test_wordpress_download_handler_is_discovered_and_validated(site, handler):
    _, routes, _, _, _ = site
    url = HOST + "/?" + handler + "=1&download_id=9199"
    config = source(listing_urls=[HOST + "/budgets"])
    routes[HOST + "/budgets"] = httpx.Response(200, text=f'<a href="{url}">Budget</a>')
    routes[url] = httpx.Response(302, headers={"location": HOST + "/uploads/budget.pdf"})
    routes[HOST + "/uploads/budget.pdf"] = pdf("budget")
    state = run(site, config)
    assert state["downloads"][url]["status"] == "held"
    assert (
        archive.documents(
            f'<a href="/?{handler}=0&download_id=9199">Not a download</a>'
            f'<a href="/?{handler}=1&download_id=bad">Bad ID</a>',
            HOST + "/budgets",
            config,
        )
        == {}
    )


def test_opaque_php_downloads_are_candidates_but_html_is_not_retained(site):
    _, routes, _, _, _ = site
    good = HOST + "/downdoc.php?id_doc=4503&lng=1&i=1"
    wrong = HOST + "/downdoc.php?id_doc=4504&lng=1&i=1"
    config = source(listing_urls=[HOST + "/documents"])
    routes[HOST + "/documents"] = httpx.Response(
        200, text=f'<a href="{good}">Document</a><a href="{wrong}">Expired document</a>'
    )
    routes[good] = pdf("document")
    routes[wrong] = httpx.Response(200, text="<html>File no longer available</html>")
    state = run(site, config, limit=5)
    assert state["downloads"][good]["status"] == "held"
    assert state["downloads"][wrong]["status"] == "failed"
    assert state["downloads"][wrong]["kind"] == "not_pdf"


def test_phoca_category_download_links_are_discovered():
    listing = HOST + "/index.php/albo-comunale/category/5-legislazione-comunale"
    config = source(listing_urls=[listing])
    url = listing + "?download=68:regolamento-acap-2024"
    assert archive.documents(f'<a href="{url}">Download</a>', listing, config) == {
        url.replace("68:", "68%3A"): listing
    }
    assert archive.documents('<a href="?download=all">Navigation</a>', listing, config) == {}


def test_indual_hexadecimal_file_handler_is_discovered_and_downloaded(site):
    _, routes, _, _, _ = site
    listing = HOST + "/reglemente"
    url = HOST + "/?action=get_file&id=33&resource_link_id=6a"
    config = source(listing_urls=[listing])
    routes[listing] = httpx.Response(200, text=f'<a href="{url}">Reglement</a>')
    routes[url] = pdf("regulation")
    state = run(site, config)
    assert state["downloads"][url]["status"] == "held"
    assert (
        archive.documents(
            '<a href="/?action=get_file&id=33">Missing resource</a>'
            '<a href="/?action=delete_file&id=33&resource_link_id=6a">Other action</a>',
            listing,
            config,
        )
        == {}
    )


def test_new_reviewed_seed_precedes_older_discovered_restricted_handler(site):
    _, routes, calls, _, polite = site
    listing = HOST + "/docs"
    config = source(listing_urls=[listing])
    routes[listing] = httpx.Response(
        200,
        text='<a href="/old.pdf">old</a><a href="/restricted/file.pdf">restricted</a><a href="/reviewed.pdf">reviewed</a>',
    )
    routes[HOST + "/old.pdf"] = pdf("old")
    routes[HOST + "/reviewed.pdf"] = pdf("reviewed")
    state = run(site, config, limit=1)
    assert state["downloads"][HOST + "/old.pdf"]["status"] == "held"
    polite._robots["commune.test"] = http.RobotsRules(
        "User-agent: *\nDisallow: /restricted/", settings.ua
    )
    config = config.model_copy(update={"document_urls": [HOST + "/reviewed.pdf"]})
    state = run(site, config, limit=2)
    assert state["downloads"][HOST + "/reviewed.pdf"]["status"] == "held"
    assert state["documents"][HOST + "/reviewed.pdf"] == listing
    assert state["downloads"][HOST + "/restricted/file.pdf"]["kind"] == "robots"
    assert HOST + "/restricted/file.pdf" not in calls
    assert calls.count(HOST + "/old.pdf") == 1


def test_arcantel_fetch_stores_pdfs_with_human_page_provenance(site):
    _, routes, _, _, _ = site
    page = HOST + "/council/sessions"
    config = archive.LinkedSource(
        bfs_id=1,
        website=HOST,
        vendor="arcantel",
        listing_urls=[page],
        crawl_prefixes=[HOST + "/council"],
    )
    routes[page] = httpx.Response(200, text="<html>Loading</html>")
    routes[HOST + "/Api/Page.svc/detail"] = httpx.Response(
        200,
        json={
            "DetailResult": {
                "Url": "/council/sessions",
                "Content": '<a href="/council/old.pdf">Historical report</a>',
            }
        },
    )
    routes[HOST + "/Api/Menu.svc/children"] = httpx.Response(200, json={"ChildrenResult": []})
    routes[HOST + "/council/old.pdf"] = pdf("historical")
    state = run(site, config)
    assert state["downloads"][HOST + "/council/old.pdf"]["status"] == "held"
    sidecar = json.loads(next(settings.raw_dir.glob("*.json")).read_text())
    assert sidecar["listing_url"] == page


def test_embedded_pdf_viewers_keep_municipal_scope():
    listing = HOST + "/regulations"
    html = f"""<iframe src="https://drive.google.com/viewerng/viewer?url={HOST}/files/rules.pdf&amp;embedded=true"></iframe>
      <object data="/files/budget.pdf"></object>
      <embed src="/files/accounts.pdf">
      <iframe src="https://drive.google.com/viewerng/viewer?url={SHARED}/other.pdf"></iframe>
      <iframe src="/interactive-map"></iframe>
      <iframe src=""></iframe><object data=" "></object><a href=""></a>"""
    found = archive.documents(html, listing, source(listing_urls=[listing]))
    assert found == {
        HOST + "/files/" + name + ".pdf": listing for name in ["rules", "budget", "accounts"]
    }
