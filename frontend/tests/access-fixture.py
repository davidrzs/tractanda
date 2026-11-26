"""Offline API fixture for the UI tests; never contacts municipal sites.

It answers the endpoints the front end reads (server/API.md) from a few invented communes and
files, and filters and counts the files the way the API does: for /api/documents each facet with
every filter except its own, for the full-text search (/api/pages) with every filter.
"""

import datetime as dt
import json
import re
import unicodedata
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

now = dt.datetime.now(dt.UTC).isoformat()
old = (dt.datetime.now(dt.UTC) - dt.timedelta(days=8)).isoformat()
RUN = "classify-65e415a748e2"


def access(status, stale=False):
    """One access observation for the commune's website."""
    return dict(
        status=status,
        checked_at=old if stale else now,
        restricted_at=old if stale else now if status == "restricted" else None,
        stale=stale,
        source_url="https://commune.example/",
        robots_url="https://commune.example/robots.txt",
    )


# bfs_id, name, register status, access observations
COMMUNES = [
    (1, "Aeugst am Albis", "blocked_robots", [access("restricted")]),
    (2, "Affoltern am Albis", "configured", [access("rate_limited")]),
    (3, "Bonstetten", "configured", [access("restricted", stale=True)]),
    (117, "Hinwil", "configured", []),
    (121, "Wetzikon", "blocked_robots", [access("restricted")]),
]
NAMES = {bfs: name for bfs, name, _, _ in COMMUNES}


FAMILY = dict(
    minutes="deliberation",
    decisions="deliberation",
    vote_result="deliberation",
    budget="money",
    accounts="money",
    invitation_agenda="proposals",
    proposal="proposals",
    member_request="proposals",
    regulation="rules",
    report="information",
    notice="information",
    plan_or_map="information",
    news="information",
    other="other",
    unclear="other",
)


def document(n, communes, sections=(), **fields):
    """A classified file with the API's fields; `sections` as (type, first, last, partly_unseen)."""
    sha = f"{n:064x}"
    year = fields.get("year")
    return dict(
        sha256=sha,
        title=fields.get("title"),
        body_name=fields.get("body_name"),
        first_seen="2026-09-01",
        listing_date=fields.get("listing_date"),
        date=fields.get("date"),
        year=year,
        years=[year] if year else [],
        page_count=fields.get("page_count", 40),
        pages_seen=min(fields.get("page_count") or 0, 150),
        run_id=RUN,
        body_kind=fields.get("body_kind", "executive"),
        languages=fields.get("languages", ["de"]),
        personal_data=False,
        sections=[
            dict(type=t, family=FAMILY[t], first_page=a, last_page=b, partly_unseen=u)
            for t, a, b, u in sections
        ],
        communes=[dict(bfs_id=b, name=NAMES[b], canton="ZH") for b in communes],
        original_url=f"/original/{sha}",
    )


DOCUMENTS = [
    # a meeting booklet with several sections
    document(
        1,
        [121],
        sections=[
            ("invitation_agenda", 1, 2, False),
            ("proposal", 3, 6, False),
            ("proposal", 7, 10, False),
            ("minutes", 11, 12, False),
            ("budget", 13, 40, False),
        ],
        title="Einladung zur Gemeindeversammlung",
        body_name="Gemeindeversammlung Wetzikon",
        date="2025-06-12",
        year=2025,
        body_kind="citizens_assembly",
    ),
    # no printed title, accounts without a date, shared with Hinwil, in two languages
    document(
        2,
        [117, 121],
        sections=[("accounts", 1, 150, False), ("report", 150, 220, True)],
        body_name="Stadt Wetzikon",
        year=2024,
        page_count=220,
        languages=["de", "fr"],
        body_kind="commune",
    ),
    # a bulletin in French: news around a budget
    document(
        3,
        [121],
        sections=[("news", 1, 8, False), ("budget", 9, 12, False), ("news", 13, 16, False)],
        title="Bulletin communal",
        listing_date="2024-11-05",
        year=2024,
        page_count=16,
        languages=["fr"],
        body_kind="commune",
    ),
    # nothing read: no title, no sections, no year
    document(4, [121], page_count=None, languages=[], body_kind="unknown"),
    # one regulation, a year but no date
    document(
        5,
        [121],
        sections=[("regulation", 1, 12, False)],
        title="Reglement über die Abfallentsorgung",
        year=2023,
        page_count=12,
    ),
] + [
    document(
        10 + i,
        [121],
        sections=[("budget" if i % 3 == 0 else "minutes", 1, 1 + i, False)],
        title=f"Protokoll {2000 + i}",
        body_name="Gemeinderat",
        date=f"{2000 + i}-05-0{1 + i % 9}",
        year=2000 + i,
        page_count=1 + i,
        body_kind="citizens_assembly" if i % 2 else "executive",
    )
    for i in range(20)
]


def matches(doc, filters, leave_out=None):
    """Whether the file passes every filter but `leave_out`."""
    for key, value in filters.items():
        if key == leave_out:
            continue
        if key == "commune" and int(value) not in [c["bfs_id"] for c in doc["communes"]]:
            return False
        if key == "canton" and value.upper() not in [c["canton"] for c in doc["communes"]]:
            return False
        if key == "section" and value not in [s["type"] for s in doc["sections"]]:
            return False
        if key == "family" and value not in [s["family"] for s in doc["sections"]]:
            return False
        if key == "first_family" and [s["family"] for s in doc["sections"][:1]] != [value]:
            return False
        if key == "body" and doc["body_kind"] != value:
            return False
        if key == "year" and str(doc["year"]) != value:
            return False
        if key == "q" and value.lower() not in (doc["title"] or "").lower():
            return False
    return True


def facet(docs, values, newest_first=False):
    """`[{value, count}]` over the files: values ascending (years newest first), null last."""
    counts = {}
    for doc in docs:
        for value in values(doc):
            counts[value] = counts.get(value, 0) + 1
    known = sorted((v for v in counts if v is not None), reverse=newest_first)
    order = known + ([None] if None in counts else [])
    return [dict(value=v, count=counts[v]) for v in order]


def documents(query):
    filters = {k: v[0] for k, v in query.items() if k not in ("offset", "limit") and v[0]}
    offset = int(query.get("offset", ["0"])[0])
    limit = min(int(query.get("limit", ["20"])[0]), 100)
    chosen = [d for d in DOCUMENTS if matches(d, filters)]
    # year descending with no year last, then date (else listing date) descending, then sha256
    chosen.sort(key=lambda d: d["sha256"])
    chosen.sort(key=lambda d: d["date"] or d["listing_date"] or "", reverse=True)
    chosen.sort(key=lambda d: (d["year"] is not None, d["year"] or 0), reverse=True)

    def others(key):
        return [d for d in DOCUMENTS if matches(d, filters, key)]

    return dict(
        total=len(chosen),
        offset=offset,
        limit=limit,
        facets=dict(
            years=facet(others("year"), lambda d: [d["year"]], newest_first=True),
            # a file without sections is in no entry
            sections=facet(others("section"), lambda d: {s["type"] for s in d["sections"]}),
            bodies=facet(others("body"), lambda d: [d["body_kind"]]),
        ),
        items=chosen[offset : offset + limit],
    )


# The text of the pages the full-text index holds, invented: pages inside a section of a searched
# type. The bulletin (3) is left out, as the index leaves out bulletins.
PAGES = {
    (1, 3): "Antrag des Gemeinderates: Für die Sanierung der Schulanlage wird ein "
    "Investitionskredit von 4,2 Millionen Franken beantragt.",
    (1, 11): "Protokoll der Gemeindeversammlung. Die Versammlung genehmigt den Investitionskredit "
    "für die Schulanlage. Der Steuerfuss bleibt bei 115 Prozent.",
    # text that looks like HTML: the page shows it as text
    (1, 13): "Budget 2026. Der Gemeinderat beantragt, den Steuerfuss auf 115 Prozent festzusetzen. "
    "Aufwand <b>und</b> Ertrag sind ausgeglichen.",
    (2, 5): "Jahresrechnung 2024. Der Steuerfuss betrug 115 Prozent; der Ertrag lag über "
    "dem Budget.",
    # a character outside the Basic Multilingual Plane before the match: offsets count code points
    (2, 6): "Comptes 2024 \U0001d7d0 : le crédit d\u2019engagement pour l\u2019école est bouclé.",
} | {
    (10 + i, 1): f"Protokoll der Sitzung {2000 + i}. Traktandum 1: Steuerfuss und Finanzplan."
    for i in range(20)
}
# The most matching pages a search ranks; the server's cap is 20,000.
CAP = 22


def fold(text):
    """Lower case without accents, and a space for every character that is not a letter or a digit:
    one character for each, so that offsets in the folded text are offsets in the text."""
    return "".join(
        unicodedata.normalize("NFD", c)[0].lower()[:1] if c.isalnum() else " " for c in text
    )


def patterns(q):
    """The words and "phrases" of a query as patterns over folded text; None when the server would
    refuse it: no word, more than 10 terms, or fewer than 3 characters before a *."""
    found = []
    for m in re.finditer(r'"([^"]*)"?(\*?)|([^\s"]+)', q):
        raw = m.group(1) if m.group(1) is not None else m.group(3)
        prefix = bool(m.group(2)) or raw.endswith("*")
        words = fold(raw.rstrip("*")).split()
        if not words:
            continue
        if prefix and len(words[-1]) < 3:
            return None
        end = r"\w*" if prefix else r"(?!\w)"
        found.append(r"(?<!\w)" + r"\s+".join(map(re.escape, words)) + end)
    return found if 0 < len(found) <= 10 else None


def pages(query):
    """GET /api/pages: the documents with a page that holds every term, best page first."""
    terms = patterns(query.get("q", [""])[0])
    if terms is None:
        return 400, dict(detail="q is not a search")
    keys = ("commune", "canton", "year", "body")
    filters = {k: v[0] for k, v in query.items() if k in keys and v[0]}
    section = query.get("section", [""])[0]
    family = query.get("family", [""])[0]
    offset = int(query.get("offset", ["0"])[0])
    limit = min(int(query.get("limit", ["10"])[0]), 50)
    by_sha = {d["sha256"]: d for d in DOCUMENTS}
    hits = []
    for (n, number), text in PAGES.items():
        doc = by_sha[f"{n:064x}"]
        holding = [s for s in doc["sections"] if s["first_page"] <= number <= s["last_page"]]
        if not matches(doc, filters):
            continue
        if section and section not in [s["type"] for s in holding]:
            continue
        if family and family not in [s["family"] for s in holding]:
            continue
        spans = [[m.span() for m in re.finditer(t, fold(text))] for t in terms]
        if all(spans):
            marks = sorted(span for found in spans for span in found)
            score = -len(marks)
            hits.append((score, doc["sha256"], number, [s["type"] for s in holding], text, marks))
    # the best pages first, cut at the cap; a document's first page is its best
    hits.sort(key=lambda h: h[:3])
    capped = len(hits) >= CAP
    found = {}
    for hit in hits[:CAP]:
        found.setdefault(hit[1], []).append(hit)
    docs = [by_sha[sha] for sha in found]
    communes = {}
    for d in docs:
        for c in d["communes"]:
            communes.setdefault(c["bfs_id"], dict(c, count=0))["count"] += 1
    items = [
        dict(
            d,
            matching_pages=len(found[d["sha256"]]),
            pages=[
                dict(
                    page=number,
                    section_types=types,
                    snippet=text,
                    highlights=[list(span) for span in marks],
                    original_url=f"/original/{sha}?page={number}",
                )
                for _, sha, number, types, text, marks in found[d["sha256"]][:3]
            ],
        )
        for d in docs[offset : offset + limit]
    ]
    return 200, dict(
        total=len(docs),
        capped=capped,
        offset=offset,
        limit=limit,
        facets=dict(
            years=facet(docs, lambda d: [d["year"]], newest_first=True),
            sections=facet(docs, lambda d: {t for h in found[d["sha256"]] for t in h[3]}),
            cantons=facet(docs, lambda d: {c["canton"] for c in d["communes"]}),
            communes=sorted(communes.values(), key=lambda c: (-c["count"], c["name"]))[:20],
        ),
        items=items,
    )


def listed(bfs, name, status):
    """A row of the commune list."""
    held = sum(bfs in [c["bfs_id"] for c in d["communes"]] for d in DOCUMENTS)
    return dict(
        bfs_id=bfs,
        name=name,
        canton="ZH",
        language="de",
        website="https://commune.example",
        lat=None,
        lon=None,
        documents=held,
        register_status=status,
        register_note=None,
    )


def detail(bfs, name, status, observations):
    """The commune page: the list's row with the register entry and the access observations."""
    row = listed(bfs, name, status)
    register = dict(
        bfs_id=bfs,
        name=name,
        canton="ZH",
        status=status,
        website=row["website"],
        vendor=None,
        reachable=True,
        checked="2026-09-08",
        checked_by="listing review",
        notes=None,
        publications=[],
    )
    keys = ("bfs_id", "name", "canton", "language", "website", "lat", "lon", "documents")
    return {
        **{k: row[k] for k in keys},
        "vendor": None,
        "register": register,
        "access": observations,
        "holdings": holdings(bfs),
    }


def holdings(bfs):
    """Per year and family, newest year first: the documents with a section of the family, the
    family's pages (a page two sections share counts for the first) and the documents whose first
    section is of the family, as the server gives them."""
    counts = {}
    for d in DOCUMENTS:
        if d["year"] is None or bfs not in [c["bfs_id"] for c in d["communes"]]:
            continue
        counted = set()
        for position, s in enumerate(d["sections"]):
            c = counts.setdefault((d["year"], s["family"]), {"docs": set(), "pages": 0, "first": 0})
            c["docs"].add(d["sha256"])
            low, high = sorted((s["first_page"], s["last_page"]))
            own = {p for p in range(low, high + 1) if not d["page_count"] or p <= d["page_count"]}
            c["pages"] += len(own - counted)
            counted |= own
            c["first"] += position == 0
    order = sorted(counts, key=lambda k: (-k[0], k[1]))
    return [
        dict(
            year=y,
            family=f,
            documents=len(counts[(y, f)]["docs"]),
            pages=counts[(y, f)]["pages"],
            first_part_documents=counts[(y, f)]["first"],
        )
        for y, f in order
    ]


RUNS = [
    dict(
        id=RUN,
        stage="classification",
        model="model-x",
        settings=dict(dpi=75, max_pages=150),
        note="Pages rendered at 75 dpi; at most 150 pages per document.",
        documents=len(DOCUMENTS),
    )
]


def release(version, date, documents, searchable, ocr):
    return dict(
        version=version,
        date=date,
        documents=documents,
        pages=documents * 10,
        communes=5,
        searchable_pages=searchable,
        ocr_pages=ocr,
        classification_run=RUN,
        git_commit=None,
        note=None,
    )


# a baseline and a version that added OCR pages; files downloaded on three days
RELEASES = dict(
    current="2026.11.26",
    releases=[
        release("2026.11.17", "2026-11-17", 20, 150, 0),
        release("2026.11.26", "2026-11-26", 25, 1384, 1234),
    ],
    downloads=[
        dict(day="2026-09-06", files=12),
        dict(day="2026-10-01", files=8),
        dict(day="2026-11-20", files=9),
    ],
)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        body = None
        status = 200
        if path == "/api/communes":
            body = [listed(bfs, name, status) for bfs, name, status, _ in COMMUNES]
        elif path.startswith("/api/communes/"):
            body = next((detail(*c) for c in COMMUNES if str(c[0]) == path.rsplit("/", 1)[1]), None)
        elif path == "/api/documents":
            body = documents(parse_qs(url.query))
        elif path == "/api/pages":
            status, body = pages(parse_qs(url.query))
        elif path == "/api/runs":
            body = RUNS
        elif path == "/api/releases":
            body = RELEASES
        elif path == "/api/openapi.json":
            # the server's own description, saved from a running server
            body = json.loads((Path(__file__).parent / "openapi.json").read_text())
        if body is None:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail": "not found"}')
            return
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", 8002), Handler).serve_forever()
