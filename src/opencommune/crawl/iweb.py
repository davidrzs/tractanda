"""Resumable, sequential inventory of public i-web document listings.

Machine observations live under data/discovery, never in the human coverage register.
Only linked public URLs and a small set of listing routes are visited: no document-ID scans.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from types import SimpleNamespace
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from ..config import settings
from ..schemas import AdapterStep, DocumentLink
from . import http, store
from .adapters.html_links import HtmlLinksAdapter, unpack_embedded_tables
from .checkpoints import Checkpoints

SEEDS = ("/", "/sitemap", "/sitzung", "/publikationen", "/reglemente", "/downloads")
PAGE_HINT = re.compile(
    r"sitzung|versammlung|politbusiness|polprotokoll|protokoll|beschluss|beschlüsse|"
    r"publikation|dokument|download|reglement|verordnung|finanz|budget|rechnung|archiv|"
    r"jahresbericht|gesch.ftsbericht|steuer|financ|comptes?|rapport.{0,20}gestion|fiscal|"
    r"seances?|séances?|assembl|conseil|proc.s.verba|d.cision|r.glement|publication|"
    r"sedut|consiglio|verbal|risoluzion|document|regolament|preventiv|consuntiv|"
    r"bilanc|rendicont|piano.{0,20}finanziario|/_rte/anlass/",
    re.I,
)
FILE = re.compile(r"\.(?:pdf|zip|docx?|xlsx?|pptx?|jpe?g|png|gif|mp[34])$", re.I)
DOCUMENT = re.compile(r"/_doc(?:/|$)|\.pdf(?:\?|$)", re.I)
PUBLICATION_DETAIL = re.compile(r"/_rte/publikation/\d+/?$", re.I)
# The page of one session, publication, business item or vote. A refresh does not read it again
# while the queue is unfinished: new ones are found through the listing pages that link to them.
RECORD_PAGE = re.compile(r"/_rte/[^/]+/\d+/?$", re.I)
# A commune with a parliament publishes the most (business items, proposals, minutes): it reads this
# many page budgets per turn and takes its turn as often as a commune with a quarter of its pages.
PARLIAMENT_SHARE = 4
# A queued page can answer with a file: an i-web publication with a single attachment redirects to
# it. The file is a PDF by these content types, or by a generic one when its name does not say
# otherwise; the download checks the bytes.
PDF_TYPES = frozenset({"application/pdf", "application/x-pdf"})
BINARY_TYPES = frozenset(
    {
        "application/octet-stream",
        "binary/octet-stream",
        "application/download",
        "application/force-download",
        "application/x-download",
    }
)
PAGER = "a[rel~='next'], .pagination a[href], .pager a[href], a.page-link[href]"
ADAPTER = HtmlLinksAdapter()


CHECKPOINTS = Checkpoints("iweb", "i-web")
directory, read, save, locked = (
    CHECKPOINTS.directory,
    CHECKPOINTS.read,
    CHECKPOINTS.save,
    CHECKPOINTS.locked,
)


def clean_url(url: str) -> str | None:
    p = urlsplit(url)
    if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
        return None
    return store.canonical_url(urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, "")))


def same_site(url: str, website: str) -> bool:
    return (urlsplit(url).hostname or "").removeprefix("www.") == (
        urlsplit(website).hostname or ""
    ).removeprefix("www.")


def page_links(html: str, base: str, website: str) -> tuple[list[dict], dict, list[dict]]:
    """All attachments, recognised archive/session pages and explicitly non-PDF attachments.

    No title-based document filter and no date cut-off. Embedded i-web tables go through the
    existing adapter; dates are read from documents, never inherited from a generic page. Pages map
    to the link that names them (title, date, this page as `listing_url`), or None: a page can turn
    out to be a file, which then takes its title and date from that link.
    """
    soup = BeautifulSoup(unpack_embedded_tables(html), "html.parser")
    excluded = {}
    for row in soup.select("tr"):
        kind = row.select_one(".icms-document-type-and-size")
        if kind and re.search(r"\b(?:ZIP|DOCX?|XLSX?|PPTX?)\b", kind.get_text(), re.I):
            for a in row.select("a[href]"):
                url = clean_url(urljoin(base, a["href"]))
                if url and DOCUMENT.search(url):
                    excluded[url] = {"url": url, "reason": kind.get_text(" ", strip=True)}
            row.decompose()
    named = {}
    for link in ADAPTER.extract_links(
        str(soup), base, AdapterStep(selector="a[href]", context="row"), None, "dmy"
    ):
        if url := clean_url(link.url):
            named.setdefault(
                url,
                {
                    "title": link.text,
                    "date": str(link.date) if link.date else None,
                    "date_precision": link.date_precision,
                    "listing_url": base,
                },
            )
    documents = [
        {"url": url, **link}
        for url, link in named.items()
        if DOCUMENT.search(url) and url not in excluded
    ]
    pages = {}
    pagers = {id(a) for a in soup.select(PAGER)}
    for a in soup.select("a[href], link[rel~='next'][href]"):
        url = clean_url(urljoin(base, a["href"]))
        if not url or not same_site(url, website) or DOCUMENT.search(url):
            continue
        p = urlsplit(url)
        if FILE.search(p.path) or re.search(r"login|logout|signout|/suche|/search", p.path, re.I):
            continue
        if (
            PAGE_HINT.search(a.get_text(" ", strip=True) + " " + p.path)
            or id(a) in pagers
            or a.name == "link"
        ):
            pages[url] = named.get(url)
    return documents, pages, list(excluded.values())


def content_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";")[0].strip().lower()


def file_name(response: httpx.Response) -> str:
    """The name of a file: from Content-Disposition, else the last segment of the URL that answered."""
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*\s*=\s*(?:[\w-]+'[^']*')?([^;]+)", disposition, re.I)
    match = match or re.search(r"filename\s*=\s*\"?([^\";]+)", disposition, re.I)
    name = match[1] if match else urlsplit(str(response.url)).path.rsplit("/", 1)[-1]
    return unquote(name).strip().strip('"')


def file_answer(url: str, response: httpx.Response, website: str) -> dict:
    """What a queued page that answered with a file stands for. The file goes by the first URL of
    the redirect chain that a listing linking it directly would have given (on i-web the
    /_doc/<id> hop, not the publication page nor the named file it ends at), so that it is held and
    downloaded once however it is linked; without such a hop, by the page's own URL."""
    chain = [*(str(r.url) for r in response.history), str(response.url)]
    target = next(
        (
            u
            for hop in chain
            if (u := clean_url(hop)) and DOCUMENT.search(u) and same_site(u, website)
        ),
        url,
    )
    kind, name = content_type(response), file_name(response)
    pdf = kind in PDF_TYPES or (
        name.lower().endswith(".pdf") if FILE.search(name) else kind in BINARY_TYPES
    )
    return {"url": target, "name": name, "content_type": kind, "pdf": pdf}


def page_text(response: httpx.Response, url: str) -> str:
    """The body of a streamed page as text; a broken transfer is a failed page."""
    try:
        response.read()
    except httpx.TimeoutException as exc:
        raise http.FetchFailure("timeout", url, http._describe(exc)) from exc
    except httpx.HTTPError as exc:
        raise http.FetchFailure("http", url, http._describe(exc)) from exc
    return response.text


def initial(entry) -> dict:
    seeds = [urljoin(entry.website, path) for path in SEEDS[:2]]
    seeds += [
        p.listing_url
        for p in entry.publications
        if p.listing_url and same_site(p.listing_url, entry.website)
    ]
    seeds += [urljoin(entry.website, path) for path in SEEDS[2:]]
    seeds = list(dict.fromkeys(u for s in seeds if (u := clean_url(s))))
    return {
        "version": 1,
        "bfs_id": entry.bfs_id,
        "name": entry.name,
        "canton": entry.canton,
        "website": entry.website,
        "seeds": seeds,
        "optional_routes": [urljoin(entry.website, path) for path in SEEDS[1:]],
        "pending": seeds.copy(),
        "pages": {},
        "documents": {},
        "excluded": {},
        "downloads": {},
    }


def share(entry) -> int:
    """The page budgets a commune reads per turn."""
    return PARLIAMENT_SHARE if any(p.body == "parliament" for p in entry.publications) else 1


def restart(state: dict) -> None:
    """Start a refresh. A finished walk starts over from the seeds. An unfinished one keeps its
    queue and the record pages it has read, and reads its seeds and listing pages again first, so
    that a long queue is not started over at every refresh. A page that was a file has no links."""
    if state["pending"]:
        again = [*state["seeds"]]
        again += [
            u
            for u, page in state["pages"].items()
            if page["status"] != "file" and not RECORD_PAGE.search(urlsplit(u).path)
        ]
        for url in again:
            state["pages"].pop(url, None)
        state["pending"] = list(dict.fromkeys([*again, *state["pending"]]))
        state["resolved"] = {
            p["resolved_url"]: u for u, p in state["pages"].items() if p["status"] == "ok"
        }
    else:
        state["pending"], state["pages"], state["resolved"] = state["seeds"].copy(), {}, {}
    state["refreshed_at"] = dt.datetime.now(dt.UTC).isoformat()


def requeue_files(state: dict) -> None:
    """A checkpoint written before a page could be a file holds such a page as failed, with kind
    `content_type`. It is queued again, first, and read once more: the checkpoint kept neither the
    content type nor the address the page redirected to."""
    files = [
        url
        for url, page in state["pages"].items()
        if page["status"] == "failed" and page.get("kind") == "content_type"
    ]
    for url in files:
        del state["pages"][url]
    state["pending"] = list(dict.fromkeys([*files, *state["pending"]]))


def add_file(state: dict, url: str, file: dict) -> None:
    """Record a queued page that answered with a file: a PDF becomes a document with the title, date
    and listing of the link that queued the page, any other file is excluded."""
    state["pages"][url] = {
        "status": "file",
        "url": file["url"],
        "content_type": file["content_type"],
    }
    if not file["pdf"]:
        state["excluded"][file["url"]] = {
            "url": file["url"],
            "reason": file["content_type"] or file["name"],
        }
        return
    link = state["linked_from"].get(url) or {}
    document = {
        "url": file["url"],
        "title": link.get("title") or file["name"],
        "date": link.get("date"),
        "date_precision": link.get("date_precision", "day"),
        "listing_url": link.get("listing_url", url),
    }
    if file["url"] != url:
        document["via"] = url
    held = state["documents"].setdefault(file["url"], {**document, "listings": []})
    if link.get("listing_url") and link["listing_url"] not in held["listings"]:
        held["listings"].append(link["listing_url"])


class WebsiteChanged(ValueError):
    """The register names another site than the saved inventory; a person must review it."""


def discover_one(
    entry,
    client,
    polite,
    *,
    page_budget: int = 100,
    retry_failed: bool = False,
    refresh: bool = False,
    deadline: float | None = None,
) -> dict:
    state = read(entry.bfs_id) or initial(entry)
    if state["website"] != entry.website:
        # The same host under another scheme or with/without www is the same inventory.
        if not same_site(state["website"], entry.website):
            raise WebsiteChanged(
                f"{entry.bfs_id}: website changed; move its old inventory before rescanning"
            )
        state["website"] = entry.website
    requeue_files(state)
    if refresh:
        restart(state)
    state.setdefault(
        "resolved", {p["resolved_url"]: u for u, p in state["pages"].items() if p["status"] == "ok"}
    )
    # The link that queued a page (title, date, listing), kept until the page is read.
    state.setdefault("linked_from", {})
    if retry_failed:
        for url, page in state["pages"].items():
            if page["status"] == "failed" and url not in state["pending"]:
                state["pending"].append(url)
    for _ in range(page_budget):
        if not state["pending"] or (deadline is not None and time.monotonic() >= deadline):
            break
        # Publication detail pages usually expose the attachment directly. Avoid spending a
        # short pass on hundreds of event pages before reaching already-linked publications.
        # Stable sorting also upgrades saved queues without dropping or inventing any URLs.
        state["pending"].sort(
            key=lambda u: not bool(PUBLICATION_DETAIL.fullmatch(urlsplit(u).path))
        )
        url = state["pending"][0]  # leave queued until the page and its discoveries are persisted
        if url in state["resolved"]:
            state["pending"].pop(0)
            state["linked_from"].pop(url, None)
            save(state)
            continue
        file = None
        try:
            if not polite.allowed(url):
                raise http.FetchFailure("robots", url, "disallowed by robots.txt")
            # Streamed: a page that is a file is known by its headers, and its body is left to the
            # download.
            response = http._send(client, polite, client.build_request("GET", url), stream=True)
            try:
                http._ensure_ok(response, url)
                final_url = str(response.url)
                if not same_site(final_url, entry.website):
                    raise http.FetchFailure("redirect", url, "listing redirects to another site")
                kind = content_type(response)
                if kind and "html" not in kind:
                    file = file_answer(url, response, entry.website)
                else:
                    text = page_text(response, url)
                    documents, pages, excluded = page_links(text, final_url, entry.website)
            finally:
                response.close()
        except http.FetchFailure as exc:
            # Missing speculative routes are normal; a missing linked page is an incomplete crawl.
            status = (
                "absent"
                if exc.status in {404, 410} and url in state["optional_routes"]
                else "failed"
            )
            state["pages"][url] = {"status": status, "kind": exc.kind, "message": exc.message}
        except (ValueError, TypeError, KeyError) as exc:
            state["pages"][url] = {"status": "failed", "kind": "parse", "message": str(exc)}
        else:
            if file is not None:
                add_file(state, url, file)
            else:
                state["resolved"][final_url] = url
                state["pages"][url] = {
                    "status": "ok",
                    "resolved_url": final_url,
                    "documents": len(documents),
                }
                for document in documents:
                    held = state["documents"].setdefault(
                        document["url"], {**document, "listings": []}
                    )
                    if final_url not in held["listings"]:
                        held["listings"].append(final_url)
                state["excluded"].update({e["url"]: e for e in excluded})
                for page, link in pages.items():
                    if page in state["pages"] or page in state["resolved"]:
                        continue
                    if page not in state["pending"]:
                        state["pending"].append(page)
                    if link:
                        state["linked_from"].setdefault(page, link)
        if state["pages"][url]["status"] != "failed":
            state["linked_from"].pop(url, None)  # a failed page may still turn out to be a file
        state["pending"].pop(0)
        save(state)
        if state["pages"][url].get("kind") == "robots":
            break  # do not spend the run probing other routes on a disallowed host
    return state


def download_one(
    state,
    commune,
    client,
    polite,
    index,
    *,
    limit: int | None = None,
    retry_failed: bool = False,
    deadline: float | None = None,
):
    """Use the production SHA/URL store without rewalking any listing pages.

    Mixed archive downloads use an unknown body; an inventory never guesses it.
    Its JSON retains every listing occurrence, including duplicate-content URLs.
    """
    attempted = 0
    for url, doc in state["documents"].items():
        if deadline is not None and time.monotonic() >= deadline:
            break
        previous = state["downloads"].get(url, {})
        known = index.get(store.canonical_url(url))
        if not (known and store.held(known["sha256"])) and doc.get("via"):
            # a page that redirected to the file may be held under its own URL
            known = index.get(store.canonical_url(doc["via"]))
        if known and store.held(known["sha256"]):
            state["downloads"][url] = {"status": "held", "sha256": known["sha256"]}
            continue
        if previous.get("status") in {"not_pdf", "failed"} and not retry_failed:
            continue
        if not same_site(doc["listing_url"], state["website"]) or not same_site(
            url, state["website"]
        ):
            state.setdefault("review_documents", {})[url] = "external_document_or_listing"
            continue
        if limit is not None and attempted >= limit:
            break
        attempted += 1
        # Archive scope is mixed: dates and legislative type are not inferred from the listing.
        link = DocumentLink(
            **{k: v for k, v in doc.items() if k not in {"listings", "via"}}, body=commune.body
        )
        try:
            store._download(link, commune, client, polite, index)
        except http.FetchFailure as exc:
            state["downloads"][url] = {
                "status": "not_pdf" if exc.kind == "not_pdf" else "failed",
                "kind": exc.kind,
                "message": exc.message,
            }
        else:
            state["downloads"][url] = {
                "status": "held",
                "sha256": index[store.canonical_url(url)]["sha256"],
            }
        save(state)
        if state["downloads"][url].get("kind") == "robots":
            break
    save(state)
    return state


def summary(state: dict) -> dict:
    statuses = [p["status"] for p in state["pages"].values()]
    downloads = [d["status"] for d in state["downloads"].values()]
    return {
        "bfs_id": state["bfs_id"],
        "name": state["name"],
        "pages": len(statuses),
        "pending_pages": len(state["pending"]),
        "failed_pages": statuses.count("failed"),
        "document_candidates": len(state["documents"]),
        "held_urls": downloads.count("held"),
        "onboarding": state.get("onboarding"),
        "review_documents": len(state.get("review_documents", {})),
        "non_pdf": downloads.count("not_pdf") + len(state["excluded"]),
        "failed_downloads": downloads.count("failed"),
        "listing_queue_exhausted": not state["pending"] and "failed" not in statuses,
    }


def archive_source(entry, state):
    """Validate automatic archive attribution without inventing a body or language."""
    from ..communes import bfs_register

    reason = None
    identity = bfs_register().get(entry.bfs_id)
    if identity is None or identity[1] != entry.canton:
        reason = "registry_identity_mismatch"
    elif entry.vendor != "i-web" or state.get("website") != entry.website:
        reason = "website_or_vendor_changed"
    elif not state["documents"]:
        reason = "no_document_candidates"
    state["onboarding"] = {"status": "review" if reason else "eligible", "reason": reason}
    save(state)
    if reason:
        return None
    return SimpleNamespace(
        bfs_id=entry.bfs_id, name=entry.name, canton=entry.canton, body="unknown"
    )


def run(
    entries,
    *,
    mode: str,
    communes=(),
    page_budget=100,
    limit=None,
    retry_failed=False,
    refresh=False,
    max_minutes=None,
    emit=print,
):
    settings.ensure_dirs()
    # Repeated bounded syncs advance across the register before deepening existing inventories.
    if mode == "sync":
        entries = sorted(
            entries,
            key=lambda e: (
                (read(e.bfs_id) or {}).get("refreshed_at", "") if refresh else "",
                len((read(e.bfs_id) or {}).get("pages", {})) / share(e),
            ),
        )
    deadline = time.monotonic() + max_minutes * 60 if max_minutes is not None else None
    with locked(), http.make_client() as client:
        # One worker and one shared limiter, even when different domains resolve to one hoster.
        delay = max(2.0, settings.request_delay_seconds)
        polite = http.Politeness(client, delay, settings.ua)
        for entry in entries:
            polite._network(urlsplit(entry.website).netloc)
        index = store.load_index() if mode in {"fetch", "sync"} else {}
        for entry in entries:
            if deadline is not None and time.monotonic() >= deadline:
                emit(
                    json.dumps(
                        {"status": "time_budget_reached", "resume": "repeat the same command"}
                    )
                )
                break
            if mode in {"discover", "sync"}:
                try:
                    state = discover_one(
                        entry,
                        client,
                        polite,
                        page_budget=page_budget * share(entry),
                        retry_failed=retry_failed,
                        refresh=refresh,
                        deadline=deadline,
                    )
                except WebsiteChanged:
                    # One commune awaiting review must not stop the pass for all the others.
                    emit(json.dumps({"bfs_id": entry.bfs_id, "status": "website_changed"}))
                    continue
            else:
                state = read(entry.bfs_id)
            if mode in {"fetch", "sync"}:
                if state is None:
                    emit(json.dumps({"bfs_id": entry.bfs_id, "status": "needs_inventory"}))
                    continue
                commune = archive_source(entry, state)
                if commune is None:
                    emit(json.dumps(summary(state), ensure_ascii=False))
                    continue
                state = download_one(
                    state,
                    commune,
                    client,
                    polite,
                    index,
                    limit=limit,
                    retry_failed=retry_failed,
                    deadline=deadline,
                )
            emit(json.dumps(summary(state), ensure_ascii=False))
