"""Adapter for the WordPress REST media endpoint.

`GET /wp-json/wp/v2/media?search=<term>&mime_type=application/pdf&per_page=100&page=N` lists every
uploaded PDF whose title or file name contains the term, newest upload first, without knowing the
page that links them. Most WordPress sites answer it; those whose security plugin disables the
REST API do not.

Config: the listing URL is the endpoint with the search term (or the site root, then the term is
missing and every PDF is listed):

    listing_urls: ["https://www.brig-glis.ch/wp-json/wp/v2/media?search=protokoll"]
    adapter: wp_media
    adapter_config: {steps: [{selector: media, text_regex: "^Protokoll der Urversammlung"}]}

`listing_urls()` fills in `per_page`, `page` and the PDF mime filter when the URL lacks them. The
step's `selector` is unused (the response is JSON); `text_regex`, `url_regex` and `exclude_regex`
filter on the title and the file URL. The date comes from the title or the file name through the
shared `parse_date`; the item's own `date` is the upload date and is not used. A title with month
and year only gives a month-precision date. `next_url` follows `X-WP-TotalPages` when the driver
passes the response headers and otherwise asks for another page while a page is full.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from collections.abc import Mapping
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from ...schemas import AdapterStep, SourceConfig
from .base import Link, ListingAdapter, excluded
from .html_links import _first_date, clean_text, parse_month_year

ENDPOINT = "/wp-json/wp/v2/media"
PAGE_SIZE = 100
PDF = "application/pdf"


def _query(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))


def _with_query(url: str, query: Mapping[str, str]) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


class WpMediaAdapter(ListingAdapter):
    name = "wp_media"

    def listing_urls(self, source: SourceConfig) -> list[str]:
        """The endpoint with mime filter, page size and page number filled in."""
        out: list[str] = []
        for url in source.listing_urls:
            parts = urlsplit(url)
            path = parts.path if ENDPOINT in parts.path else parts.path.rstrip("/") + ENDPOINT
            query = _query(url)
            query.setdefault("mime_type", PDF)
            query.setdefault("per_page", str(PAGE_SIZE))
            query.setdefault("page", "1")
            out.append(urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), "")))
        return out

    def request(self, step: AdapterStep, url: str) -> httpx.Request:
        return httpx.Request("GET", url, headers={"Accept": "application/json"})

    def extract_links(
        self,
        html: str,
        base_url: str,
        step: AdapterStep,
        date_regex: str | None,
        date_order: str,
        parent: Link | None = None,
    ) -> list[Link]:
        try:
            items = json.loads(html)
        except ValueError:
            return []
        if not isinstance(items, list):  # an error object such as rest_post_invalid_page_number
            return []
        keep = re.compile(step.text_regex, re.IGNORECASE) if step.text_regex else None
        url_re = re.compile(step.url_regex, re.IGNORECASE) if step.url_regex else None
        drop = re.compile(step.exclude_regex, re.IGNORECASE) if step.exclude_regex else None
        seen: set[str] = set()
        links: list[Link] = []
        for item in items:
            if not isinstance(item, dict) or item.get("mime_type", PDF) != PDF:
                continue
            url = item.get("source_url") or (item.get("guid") or {}).get("rendered")
            if not url:
                continue
            url = urljoin(base_url, url)
            title = clean_text(
                htmllib.unescape(str((item.get("title") or {}).get("rendered") or ""))
            )
            if keep and not keep.search(title):
                continue
            if url_re and not url_re.search(url):
                continue
            if excluded(drop, title + " " + url):
                continue
            if url in seen:
                continue
            seen.add(url)
            # the file name only: the upload folder (/uploads/2026/03/) is not the meeting's year
            file_name = unquote(url.rsplit("/", 1)[-1])
            date = _first_date([title], file_name, date_regex, date_order)
            precision = "day"
            if date is None:
                date = parse_month_year(title) or parse_month_year(file_name)
                if date is not None:
                    precision = "month"
            links.append(
                Link(
                    url=url,
                    text=title or file_name,
                    date=date,
                    date_precision=precision,
                    upstream_id=str(item["id"]) if item.get("id") is not None else None,
                )
            )
        return links

    def next_url(
        self, content: str, url: str, step: AdapterStep, headers: Mapping[str, str] | None = None
    ) -> str | None:
        query = _query(url)
        page = int(query.get("page") or 1)
        total_pages = None
        if headers:
            raw = {k.lower(): v for k, v in headers.items()}.get("x-wp-totalpages", "")
            if raw.isdigit():
                total_pages = int(raw)
        if total_pages is not None:
            if page >= total_pages:
                return None
        else:
            try:
                items = json.loads(content)
            except ValueError:
                return None
            if not isinstance(items, list) or len(items) < int(query.get("per_page") or 10):
                return None
        query["page"] = str(page + 1)
        return _with_query(url, query)
