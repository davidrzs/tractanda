"""Resumable untagged PDFs from configured municipal listings and selected files.

Tracked configuration is sufficient on a fresh server: survey snapshots are not required.
Configured sections can opt into bounded persistent navigation; shared file hosts stay scoped.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..communes import bfs_register
from ..config import settings
from ..register import load_register
from ..schemas import DocumentLink
from . import discovery, http, scheduling, storage, store
from .adapters.html_links import _base_href, is_pdf_candidate, unpack_embedded_tables, unwrap_viewer
from .checkpoints import Checkpoints
from .html_parse import parse_html
from .iweb import clean_url, same_site
from .sitemaps import sitemap_links
from .sites import arcantel, legislative, magnolia, sh_cms, wappler
from .sites.finsta import FinstaExports
from .sites.hoststar import extract_pdf_urls as hoststar_documents
from .sites.nextjs import link_tags as nextjs_links
from .sites.quicksite import document_urls as quicksite_documents
from .sites.quicksite import legacy_document_urls as quicksite_legacy_documents
from .sites.skr_file import document_urls as skr_file_documents
from .sites.wix import document_urls as wix_documents


def _safe_path(url):
    path = unquote(urlsplit(url).path)
    return "\\" not in path and not any(part in {".", ".."} for part in path.split("/"))


def _same_request_url(left, right):
    """Treat a configured Unicode seed and its HTTP percent-encoded form as one URL."""
    try:
        candidate, seed = httpx.URL(left), httpx.URL(right)
    except (ValueError, httpx.InvalidURL):
        return False
    # A municipal HTTP seed may redirect to HTTPS or drop www without changing
    # the document path. Keep the query and any escaped path separators exact.
    candidate_path, candidate_query = (
        candidate.raw_path.split(b"?", 1) if candidate.query else (candidate.raw_path, b"")
    )
    seed_path, seed_query = seed.raw_path.split(b"?", 1) if seed.query else (seed.raw_path, b"")
    return (
        same_site(str(candidate), str(seed))
        and candidate.port == seed.port
        and (
            candidate.scheme == seed.scheme
            or (seed.scheme == "http" and candidate.scheme == "https")
        )
        and candidate_path.rstrip(b"/") == seed_path.rstrip(b"/")
        and candidate_query == seed_query
    )


class LinkedSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bfs_id: int
    website: str
    vendor: str | None
    listing_urls: list[str] = Field(default_factory=list)
    document_urls: list[str] = Field(default_factory=list)
    attribution_note: str = ""
    document_url_prefixes: list[str] = Field(default_factory=list)
    crawl_prefixes: list[str] = Field(default_factory=list)
    external_listing_prefixes: list[str] = Field(default_factory=list)
    wix_file_owner: str | None = Field(default=None, pattern=r"^[0-9a-f]+$")
    finsta_exports: FinstaExports | None = None
    # Discovery limits for this archive where the defaults (discovery.MAX_PAGES, MAX_DEPTH) left
    # pages out (David, 2026-11-13).
    max_pages: int | None = Field(default=None, gt=0)
    max_depth: int | None = Field(default=None, gt=0)

    @property
    def selected_documents(self):
        return tuple(
            dict.fromkeys(
                [
                    *self.document_urls,
                    *(self.finsta_exports.urls(self.bfs_id) if self.finsta_exports else []),
                ]
            )
        )

    @property
    def traverses(self):
        return bool(self.crawl_prefixes or self.external_listing_prefixes)

    def external_listing(self, url):
        if clean_url(url) is None or not _safe_path(url):
            return False
        parts = urlsplit(url)
        return any(
            parts.scheme == urlsplit(prefix).scheme
            and parts.netloc == urlsplit(prefix).netloc
            and discovery.within(url, prefix)
            for prefix in self.external_listing_prefixes
        )

    @model_validator(mode="after")
    def check_urls(self):
        if self.finsta_exports and not self.attribution_note.strip():
            raise ValueError("FINSTA exports require a municipal attribution note")
        if self.wix_file_owner and not self.attribution_note.strip():
            raise ValueError("Wix file owner requires a municipal attribution note")
        if not self.listing_urls and not self.selected_documents:
            raise ValueError("at least one configured listing or document is required")
        for url in [self.website, *self.listing_urls, *self.document_urls]:
            if clean_url(url) is None:
                raise ValueError("expected a public HTTP(S) URL without credentials")
        for prefix in self.external_listing_prefixes:
            parts = urlsplit(prefix)
            if (
                clean_url(prefix) is None
                or parts.scheme != "https"
                or parts.query
                or parts.fragment
                or parts.path in {"", "/"}
                or not _safe_path(prefix)
                or not self.attribution_note.strip()
            ):
                raise ValueError("external listings require an HTTPS section and attribution note")
        if any(
            not same_site(u, self.website) and not self.external_listing(u)
            for u in self.listing_urls
        ):
            raise ValueError("listings must be on the registered municipal website")
        if (
            any(not same_site(u, self.website) for u in self.document_urls)
            and not self.attribution_note
        ):
            raise ValueError("external selected files require an attribution note")
        for prefix in self.document_url_prefixes:
            parts = urlsplit(prefix)
            if (
                clean_url(prefix) is None
                or parts.query
                or parts.fragment
                or not parts.path.endswith("/")
                or parts.path == "/"
                or not _safe_path(prefix)
                or not self.attribution_note
            ):
                raise ValueError("document prefixes require a directory path and attribution note")
        for prefix in self.crawl_prefixes:
            parts = urlsplit(prefix)
            if (
                clean_url(prefix) is None
                or not same_site(prefix, self.website)
                or parts.query
                or parts.fragment
                or parts.path in {"", "/"}
                or not discovery.within(prefix, prefix)
            ):
                raise ValueError("crawl prefixes require a municipal non-root section path")
        return self

    def allows_listing(self, url):
        if self.external_listing(url):
            return True
        return (
            clean_url(url) is not None
            and same_site(url, self.website)
            and (
                url in self.listing_urls
                or any(_same_request_url(url, seed) for seed in self.listing_urls)
                or any(discovery.within(url, prefix) for prefix in self.crawl_prefixes)
            )
        )

    def allows(self, url):
        if clean_url(url) is None or not _safe_path(url):
            return False
        parts = urlsplit(url)
        return (
            same_site(url, self.website)
            or url in self.selected_documents
            or (
                self.wix_file_owner
                and parts.scheme == "https"
                and parts.netloc == "static.wixstatic.com"
                and not parts.query
                and not parts.fragment
                and re.fullmatch(rf"/ugd/{self.wix_file_owner}_[0-9a-f]+\.pdf", parts.path)
            )
            or any(
                parts.scheme == urlsplit(prefix).scheme
                and parts.netloc == urlsplit(prefix).netloc
                and unquote(parts.path).startswith(unquote(urlsplit(prefix).path))
                for prefix in self.document_url_prefixes
            )
        )

    def accepts_sidecar(self, sidecar):
        return (
            sidecar.adapter == "linked_archive"
            and sidecar.body == "unknown"
            and (
                sidecar.listing_url in self.selected_documents
                or self.allows_listing(sidecar.listing_url)
            )
            and self.allows(sidecar.source_url)
        )


def load_sources(path: Path | None = None):
    path = path or settings.config_root / "linked-sources.yaml"
    return (
        [LinkedSource.model_validate(r) for r in (yaml.safe_load(path.read_text()) or [])]
        if path.exists()
        else []
    )


def validate_sources(sources, entries=None, *, snapshot_optional=False):
    """Reject sources whose register entry or BFS identity changed. A crawl needs the BFS snapshot;
    with `snapshot_optional`, a check without it (no data directory, as in CI) skips the identity
    comparison."""
    entries = {e.bfs_id: e for e in (load_register() if entries is None else entries)}
    identities = bfs_register()
    compare = bool(identities) or not snapshot_optional
    if len({c.bfs_id for c in sources}) != len(sources):
        raise ValueError("duplicate linked archive BFS ID")
    for c in sources:
        e = entries.get(c.bfs_id)
        identity = identities.get(c.bfs_id)
        if (
            e is None
            or (compare and (identity is None or identity[1] != e.canton))
            or e.website != c.website
            or e.vendor != c.vendor
        ):
            raise ValueError(f"{c.bfs_id}: registered identity or source changed; review required")
    return sources


CHECKPOINTS = Checkpoints("linked_archive", "linked archive")
directory, read, save, locked = (
    CHECKPOINTS.directory,
    CHECKPOINTS.read,
    CHECKPOINTS.save,
    CHECKPOINTS.locked,
)


def within_source(polite, source):
    """Requests limited to the source's documents and listings."""
    return http.Scoped(polite, source.allows, "URL outside configured source scope")


def listing_allowed(source, url):
    """A page the source may read as a listing: within its crawl sections when it traverses."""
    if source.traverses and not source.allows_listing(url):
        return False
    return source.external_listing(url) or source.allows(url)


def within_listings(polite, source):
    """Requests limited to the source's listings: within its crawl sections when it traverses."""
    return http.Scoped(
        polite,
        lambda url: listing_allowed(source, url),
        "listing outside configured crawl sections",
    )


def documents(html, listing, source):
    html = unpack_embedded_tables(html)
    if wappler.is_content_url(listing):
        html = wappler.html_document(html, listing, source.website)
    if source.vendor == "sh-cms" and urlsplit(listing).path == "/CMS/content":
        return {url: listing for url in sh_cms.document_urls(html, listing) if source.allows(url)}
    soup = parse_html(html)
    base = _base_href(soup, listing)
    found = {}
    embedded = hoststar_documents(html, listing) if "Server.components" in html else []
    widget = skr_file_documents(soup, listing) if "skrListFileManager(" in html else []
    legacy = quicksite_legacy_documents(html, listing)
    for url in (
        quicksite_documents(html, listing) + legacy + embedded + widget + wix_documents(soup)
    ):
        if source.allows(url):
            found[url] = listing
    for a in [*soup.select("a[href], iframe[src], embed[src], object[data]"), *nextjs_links(soup)]:
        try:
            raw = a.get("href") or a.get("src") or a.get("data")
            if not raw or not raw.strip():
                continue
            target = urljoin(base, raw.strip())
            parts = urlsplit(target)
            files = parse_qs(parts.query).get("file", [])
            if len(files) > 1:
                continue
            file_route = len(files) == 1 and urlsplit(files[0]).path.lower().endswith(".pdf")
            # Preserve download-handler parameters; only actual viewer paths are unwrapped.
            url = clean_url(
                target
                if file_route and "viewer" not in parts.path.lower()
                else unwrap_viewer(target)
            )
        except ValueError:
            continue
        parts = urlsplit(url or "")
        query = parse_qs(parts.query)
        files = query.get("file", [])
        download_ids = query.get("id", [])
        opaque_download = (
            "dropfiles_downloadlink" in a.get("class", [])
            or (
                source.vendor == "onegov"
                and url is not None
                and same_site(url, source.website)
                and bool(re.fullmatch(r"/storage/[0-9a-f]{64}", parts.path))
            )
            or bool(re.fullmatch(r"/Download/F/\d+", parts.path))
            or bool(re.fullmatch(r"/files/get-file/\d+/?", parts.path))
            or bool(re.fullmatch(r"/download_file/view/\d+(?:/\d+)?/?", parts.path))
            or (len(files) == 1 and urlsplit(files[0]).path.lower().endswith(".pdf"))
            or (
                parts.path.lower() == "/dynasite.cfm"
                and query.get("cmd") == ["cdownload_download_download_download"]
                and len(download_ids) == 1
                and bool(re.fullmatch(r"[0-9]+", download_ids[0]))
            )
        )
        if url and source.allows(url) and (is_pdf_candidate(a, url) or opaque_download):
            # No names, inferred meeting dates or body labels from broad document listings.
            found[url] = listing
    return found


class ConfigurationChanged(ValueError):
    """A source's configuration no longer matches its saved inventory; a person must review it."""


def _signature(source):
    """The configuration a checkpoint was built from, as saved in it."""
    signature = source.model_dump(mode="json")
    if source.finsta_exports is None:
        signature.pop("finsta_exports")
    if not source.document_url_prefixes:
        # Existing checkpoint signatures predate this optional scope setting.
        signature.pop("document_url_prefixes")
    if not source.crawl_prefixes:
        signature.pop("crawl_prefixes")
    if not source.external_listing_prefixes:
        signature.pop("external_listing_prefixes")
    for field in ("max_pages", "max_depth"):
        if getattr(source, field) is None:  # older signatures have no limits
            signature.pop(field)
    if source.wix_file_owner is None:
        signature.pop("wix_file_owner")
    return signature


def accept(source):
    """A person reviewed a changed configuration: its checkpoint takes it on. Listing pages it
    removed or put out of scope leave the queue, with their pages; documents it removed or found
    only on such pages leave the inventory unless they are held. Held files stay. Returns what was
    dropped, or None when the source has no checkpoint or nothing changed."""
    with locked():
        state = read(source.bfs_id)
        signature = _signature(source)
        if state is None or state["source"] == signature:
            return None
        previous = state["source"]
        removed_seeds = set(previous.get("listing_urls", [])) - set(source.listing_urls)
        removed_files = set(previous.get("document_urls", [])) - set(source.selected_documents)

        def listing_kept(url):
            return url not in removed_seeds and listing_allowed(source, url)

        frontier = state.get("frontier", {})
        state["frontier"] = {u: f for u, f in frontier.items() if listing_kept(u)}
        pages = state["pages"]
        state["pages"] = {u: p for u, p in pages.items() if u in state["frontier"]}
        dropped = []
        for url, listing in list(state["documents"].items()):
            if state["downloads"].get(url, {}).get("status") == "held":
                continue
            if url in source.selected_documents:
                continue
            if url in removed_files or not source.allows(url) or not listing_kept(listing):
                dropped.append(url)
                state["documents"].pop(url)
                state["downloads"].pop(url, None)
        state["source"] = signature
        state["status"] = "pending"
        save(state)
        return {
            "bfs_id": source.bfs_id,
            "listings_dropped": len(frontier) - len(state["frontier"]),
            "pages_dropped": len(pages) - len(state["pages"]),
            "documents_dropped": len(dropped),
        }


def fetch_one(
    source, entry, client, polite, index, *, limit, deadline=None, refresh=False, retry_failed=False
):
    signature = _signature(source)
    state = read(source.bfs_id) or {
        "bfs_id": source.bfs_id,
        "source": signature,
        "pages": {},
        "documents": {u: u for u in source.selected_documents},
        "downloads": {},
        "status": "pending",
    }
    if refresh:
        state["refresh_attempted_at"] = dt.datetime.now(dt.UTC).isoformat()
    if state["source"] != signature:
        previous = state["source"]
        url_fields = {
            "listing_urls",
            "document_urls",
            "document_url_prefixes",
            "crawl_prefixes",
            "external_listing_prefixes",
        }
        limits = ("max_pages", "max_depth")
        metadata = url_fields | {"attribution_note", "finsta_exports", *limits}
        same_scope = {k: v for k, v in previous.items() if k not in metadata} == {
            k: v for k, v in signature.items() if k not in metadata
        }
        additive = all(set(previous.get(k, [])) <= set(signature.get(k, [])) for k in url_fields)
        if previous.get("finsta_exports"):
            old_exports = FinstaExports.model_validate(previous["finsta_exports"])
            additive = additive and set(old_exports.urls(source.bfs_id)) <= set(
                source.selected_documents
            )
        if not same_scope or not additive:
            raise ConfigurationChanged(
                f"{source.bfs_id}: configuration changed; review saved inventory before resuming"
            )
        # Configured extra listings/files can extend an inventory without deleting progress.
        if set(signature.get("document_url_prefixes", [])) - set(
            previous.get("document_url_prefixes", [])
        ):
            # Previously successful listings may contain files on the newly configured host.
            # Keep held downloads, but re-read those listings under the expanded file scope.
            state["pages"] = {}
        if any(
            set(signature.get(field, [])) - set(previous.get(field, []))
            for field in ("crawl_prefixes", "external_listing_prefixes")
        ):
            state.pop("discovery_version", None)
        if any(previous.get(k) != signature.get(k) for k in limits):
            # Read the pages again whose links the old limits left out.
            for url, page in list(state["pages"].items()):
                if page.get("depth_limited", 0) or page.get("capacity_limited", 0):
                    state["pages"].pop(url)
        state["source"] = signature
        for url in source.selected_documents:
            state["documents"].setdefault(url, url)
    frontier = state.setdefault("frontier", {})
    for url in source.listing_urls:
        if frontier.get(url, {}).get("depth", 0) > 0:
            state["pages"].pop(url, None)
            frontier[url] = {"depth": 0, "parent": url}
        frontier.setdefault(url, {"depth": 0, "parent": url})
    if source.traverses and not state.get("discovery_version"):
        # Re-read old successful seeds once to discover their children.
        state["pages"] = {}
        state["discovery_version"] = 1
    if source.traverses and source.vendor == "typo3" and not state.get("typo3_pagination_version"):
        # Revisit only previously depth-limited pages after learning TYPO3's
        # pager keys. Keep held documents and every unrelated page checkpoint.
        for url, page in list(state["pages"].items()):
            if page.get("status") == "ok" and page.get("depth_limited", 0):
                state["pages"].pop(url)
        state["typo3_pagination_version"] = 1
    if refresh and not any(u not in state["pages"] for u in frontier):
        state["pages"] = {}
        state["refreshed_at"] = dt.datetime.now(dt.UTC).isoformat()
    scoped = within_source(polite, source)
    listing_scope = within_listings(polite, source)
    commune = SimpleNamespace(
        bfs_id=source.bfs_id, name=entry.name, canton=entry.canton, body="unknown"
    )
    attempted = 0
    attempted_urls = set()
    state["status"] = "running"

    def expired():
        return deadline is not None and time.monotonic() >= deadline

    def download_pending():
        nonlocal attempted
        # Explicitly configured files should not wait behind an older discovered
        # queue containing a restricted handler. All files still use the same
        # robots, redirect, request-budget and byte-validation checks.
        seeds = set(source.selected_documents)
        pending = sorted(state["documents"].items(), key=lambda item: item[0] not in seeds)
        for url, listing in pending:
            if expired() or attempted >= limit:
                return False
            previous = state["downloads"].get(url, {})
            if url in attempted_urls:
                continue
            known = index.get(store.canonical_url(url))
            if known and store.held(known["sha256"]):
                state["downloads"][url] = {"status": "held", "sha256": known["sha256"]}
                continue
            if (
                previous.get("status") == "failed"
                and not retry_failed
                and previous.get("retry_at") is None
            ):
                continue
            attempted += 1
            attempted_urls.add(url)
            try:
                store._download(
                    DocumentLink(
                        url=url,
                        listing_url=listing,
                        title="",
                        body="unknown",
                        adapter="linked_archive",
                    ),
                    commune,
                    client,
                    scoped,
                    index,
                )
            except http.FetchFailure as exc:
                state["downloads"][url] = {
                    "status": "failed",
                    "kind": exc.kind,
                    "message": exc.message,
                }
                if exc.retry_at is not None:
                    state["downloads"][url]["retry_at"] = exc.retry_at
                if exc.kind == "robots" or exc.status == 429 or exc.retry_at is not None:
                    state["status"] = "blocked"
                    save(state)
                    return False
            else:
                state["downloads"][url] = {
                    "status": "held",
                    "sha256": index[store.canonical_url(url)]["sha256"],
                }
            save(state)
        return True

    page_attempts = 0
    downloads_drained = download_pending()
    if downloads_drained or (source.traverses and state["status"] != "blocked" and not expired()):
        # The frontier is durable; a bounded run resumes without re-reading successful pages.
        queue = list(frontier)
        for listing in queue:
            if (
                expired()
                or (attempted >= limit and not source.traverses)
                or page_attempts >= discovery.PAGE_BATCH
            ):
                break
            previous = state["pages"].get(listing, {})
            if previous.get("status") == "ok":
                continue
            if (
                previous.get("status") == "failed"
                and not retry_failed
                and previous.get("retry_at") is None
            ):
                continue
            page_attempts += 1
            try:
                if not listing_scope.allowed(listing):
                    raise http.FetchFailure("robots", listing, "disallowed by robots.txt")
                request = client.build_request("GET", listing)
                response = http._send(client, listing_scope, request, stream=True)
                try:
                    http._ensure_ok(response, listing)
                    chunks = bytearray()
                    for chunk in response.iter_bytes(16384):
                        chunks.extend(chunk)
                        if chunks.startswith(b"%PDF"):
                            break
                        if len(chunks) > discovery.MAX_HTML_BYTES:
                            raise http.FetchFailure(
                                "html_size", listing, "HTML response exceeds discovery limit"
                            )
                    if chunks.startswith(b"%PDF"):
                        state["documents"].setdefault(listing, frontier[listing]["parent"])
                        found, children = {}, []
                    else:
                        content_type = (
                            response.headers.get("content-type", "").split(";")[0].lower()
                        )
                        sh_api = (
                            source.vendor == "sh-cms"
                            and urlsplit(str(response.url)).path == "/CMS/content"
                        )
                        wappler_api = wappler.is_content_url(str(response.url))
                        origin = urlunsplit((*urlsplit(str(response.url))[:2], "", "", ""))
                        legislative_api = legislative.is_api_url(str(response.url), origin)
                        bulletin_api = magnolia.offset(str(response.url)) is not None
                        allowed_types = (
                            {"application/json", "application/vnd.api+json"}
                            if sh_api or wappler_api or legislative_api or bulletin_api
                            else {
                                "text/html",
                                "application/xhtml+xml",
                                "text/plain",
                                "application/xml",
                                "text/xml",
                            }
                        )
                        # Manno's public Magnolia endpoint serves JSON with this
                        # legacy MIME type. It still goes through strict JSON/schema parsing.
                        if bulletin_api:
                            allowed_types.add("text/x-json")
                        if content_type and content_type not in allowed_types:
                            raise http.FetchFailure(
                                "not_html", listing, "Unexpected content type for archive listing"
                            )
                        html = bytes(chunks).decode(response.encoding or "utf-8", errors="replace")
                        if legislative_api:
                            try:
                                pdfs, next_pages = legislative.links(
                                    html, str(response.url), origin
                                )
                            except ValueError as exc:
                                raise http.FetchFailure("api_schema", listing, str(exc)) from exc
                            found = {url: listing for url in pdfs if source.allows(url)}
                            children = [url for url in next_pages if source.allows_listing(url)]
                        elif bulletin_api:
                            try:
                                children = [
                                    u
                                    for u in magnolia.links(html, str(response.url))
                                    if source.allows_listing(u)
                                ]
                            except ValueError as exc:
                                raise http.FetchFailure("api_schema", listing, str(exc)) from exc
                            found = {}
                        elif sh_api or wappler_api:
                            try:
                                if sh_api:
                                    sh_cms.validate_node(html, str(response.url))
                                found = documents(html, str(response.url), source)
                                children = (
                                    discovery.page_links(html, str(response.url), source, found)
                                    if source.traverses
                                    else []
                                )
                            except ValueError as exc:
                                raise http.FetchFailure("api_schema", listing, str(exc)) from exc
                        elif content_type in {"application/xml", "text/xml"} or urlsplit(
                            str(response.url)
                        ).path.lower().endswith(".xml"):
                            try:
                                children = sitemap_links(html, str(response.url), source)
                            except ValueError as exc:
                                raise http.FetchFailure(
                                    "sitemap_schema", listing, str(exc)
                                ) from exc
                            found = {}
                        else:
                            if source.vendor == "arcantel":
                                html = arcantel.render(
                                    client,
                                    within_source(polite, source),
                                    source,
                                    str(response.url),
                                )
                            found = documents(html, str(response.url), source)
                            children = (
                                discovery.page_links(html, str(response.url), source, found)
                                if source.traverses
                                else []
                            )
                finally:
                    response.close()
                document_listing = frontier[listing].get(
                    "provenance", str(response.url) if source.traverses else listing
                )
                state["documents"].update({u: document_listing for u in found})
                depth = frontier[listing]["depth"]
                depth_limited = capacity_limited = 0
                for child in children:
                    child_depth = discovery.child_depth(listing, child, depth)
                    if child in frontier:
                        if child_depth < frontier[child]["depth"]:
                            frontier[child] = {"depth": child_depth, "parent": str(response.url)}
                            state["pages"].pop(child, None)
                            queue.append(child)
                        continue
                    if child_depth > (source.max_depth or discovery.MAX_DEPTH):
                        depth_limited += 1
                    elif len(frontier) >= (source.max_pages or discovery.MAX_PAGES):
                        capacity_limited += 1
                    else:
                        frontier[child] = {"depth": child_depth, "parent": str(response.url)}
                        queue.append(child)
                state["pages"][listing] = {
                    "status": "ok",
                    "documents": len(found),
                    "depth_limited": depth_limited,
                    "capacity_limited": capacity_limited,
                }
            except http.FetchFailure as exc:
                state["pages"][listing] = {
                    "status": "failed",
                    "kind": exc.kind,
                    "message": exc.message,
                }
                if exc.retry_at is not None:
                    state["pages"][listing]["retry_at"] = exc.retry_at
                if exc.kind == "robots" or exc.status == 429 or exc.retry_at is not None:
                    state["status"] = "blocked"
                    break
            save(state)
            drained = download_pending()
            if state["status"] == "blocked" or (not drained and not source.traverses):
                break
    state["discovery"] = {
        "queued": len(frontier),
        "unvisited": sum(u not in state["pages"] for u in frontier),
        "failed": sum(p.get("status") == "failed" for p in state["pages"].values()),
        "depth_limited": sum(p.get("depth_limited", 0) for p in state["pages"].values()),
        "capacity_limited": sum(p.get("capacity_limited", 0) for p in state["pages"].values()),
        "pages_this_run": page_attempts,
    }
    if state["status"] == "running":
        remaining = (
            any(d.get("status") != "held" for d in state["downloads"].values())
            or set(state["documents"]) - state["downloads"].keys()
        )
        state["status"] = (
            "pending"
            if remaining
            or any(
                state["discovery"][k] for k in ("unvisited", "depth_limited", "capacity_limited")
            )
            or any(p["status"] != "ok" for p in state["pages"].values())
            else "complete"
        )
    save(state)
    return state


def run_fetch(
    sources=None,
    *,
    limit=3,
    max_minutes=60,
    commune_max_minutes=None,
    refresh=False,
    retry_failed=False,
    workers=1,
    emit=print,
):
    if not 1 <= workers <= 8:
        raise ValueError("workers must be between 1 and 8")
    if limit < 1 or max_minutes <= 0:
        raise ValueError("positive limit and time budget required")
    if commune_max_minutes is not None and commune_max_minutes <= 0:
        raise ValueError("commune_max_minutes must be positive")
    sources = validate_sources(load_sources() if sources is None else sources)
    entries = {e.bfs_id: e for e in load_register()}

    def priority(source):
        # Retain only the sort key, not every commune's full document inventory.
        return scheduling.source_priority(read(source.bfs_id), refresh=refresh)

    sources.sort(key=priority)
    settings.ensure_dirs()
    with locked():
        deadline = time.monotonic() + max_minutes * 60
        with http.make_client() as client:
            polite = http.Politeness(client, max(2.0, settings.request_delay_seconds), settings.ua)
            index = store.load_index()

            def work(source):
                if time.monotonic() >= deadline:
                    return None
                storage.require_space(store.MAX_PDF_BYTES)
                try:
                    state = fetch_one(
                        source,
                        entries[source.bfs_id],
                        client,
                        polite,
                        index,
                        limit=limit,
                        deadline=scheduling.commune_deadline(deadline, commune_max_minutes),
                        refresh=refresh,
                        retry_failed=retry_failed,
                    )
                except ConfigurationChanged:
                    # One source awaiting review must not stop the pass for all the others.
                    return json.dumps({"bfs_id": source.bfs_id, "status": "configuration_changed"})
                return json.dumps(
                    {
                        "bfs_id": source.bfs_id,
                        "name": entries[source.bfs_id].name,
                        "status": state["status"],
                        "held_urls": sum(
                            d["status"] == "held" for d in state["downloads"].values()
                        ),
                        "candidates": len(state["documents"]),
                        "discovery": state.get("discovery", {}),
                    },
                    ensure_ascii=False,
                )

            with ThreadPoolExecutor(max_workers=workers) as pool:
                for result in pool.map(work, sources):
                    if result is not None:
                        emit(result)
