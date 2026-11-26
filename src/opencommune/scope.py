"""What the search covers: which pages, and their text as OCR, the privacy check and the index see it.

A page is in scope when it lies inside a section of one of `TYPES` and its document is neither a
bulletin nor from a private body. Sections come from the serving database, text from text.db. A
page whose text has fewer than `MIN_CHARS` non-space characters has no usable text; OCR is to give
it some. OCR, the privacy check and the index all take their pages from `pages`, so they agree on
which pages and which text.
"""

from __future__ import annotations

import html
import re
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path

# The section types the search covers: deliberation, money and proposals.
TYPES = (
    "minutes",
    "decisions",
    "vote_result",
    "budget",
    "accounts",
    "proposal",
    "invitation_agenda",
    "member_request",
)
# A page with less text has no usable text layer.
MIN_CHARS = 20

# A hyphen and a line break between two word characters; `join_split_words` checks the letters.
_SPLIT = re.compile(r"(?<=\w)-\n(?=\w)")
# A hyphen before these words shortens a compound ("Ein- und Ausgaben"); it is not a split word.
_SHORTENED = re.compile(r"(?:und|oder|bis|bzw|sowie)\b")


def join_split_words(text: str) -> str:
    r"""Join the words split at a line end: a letter, '-', a line break and a lower-case letter
    become one word. "Gemeinde-\nrat" becomes "Gemeinderat". "Gemeinde-\nRat" stays as it is, and
    so does "Ein-\nund Ausgaben"."""

    def join(match: re.Match) -> str:
        before, after = text[match.start() - 1], text[match.end()]
        if before.isalpha() and after.islower() and not _SHORTENED.match(text, match.end()):
            return ""
        return match[0]

    return _SPLIT.sub(join, text)


# The markup of an OCR answer (PaddleOCR-VL's Markdown): HTML tables, centred blocks, images.
_TABLE = re.compile(r"<table\b[^>]*>(.*?)</table>", re.S | re.I)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S | re.I)
_BREAK = re.compile(r"<br\s*/?>", re.I)
_IMAGE = re.compile(r"<img\b[^>]*>", re.I)
# Only these tags are removed: text in angle brackets that is not one of them stays.
_TAG = re.compile(
    r"</?(?:div|span|p|center|sup|sub|b|i|u|strong|em|thead|tbody|tfoot|caption|table|tr|t[dh])"
    r"\b[^>]*>",
    re.I,
)
_HEADING = re.compile(r"^#{1,6}[ \t]+", re.M)
_FOOTNOTE_MARK = re.compile(r"\$\s*\^\{([^{}$]*)\}\s*\$")  # "$ ^{*} $", a mark in a table cell


def _cells(row: str) -> str:
    cells = [" ".join(_TAG.sub(" ", cell).split()) for cell in _CELL.findall(row)]
    return " | ".join(cells)


def ocr_plain_text(markdown: str) -> str:
    """An OCR answer as plain text: each table row on a line of its own with its cells apart by
    " | ", headings and bold marks, images and the other tags removed, entities decoded. Text in
    angle brackets that is no HTML tag stays."""
    text = _TABLE.sub(
        lambda table: "\n\n" + "\n".join(_cells(row) for row in _ROW.findall(table[1])) + "\n\n",
        markdown,
    )
    text = _BREAK.sub("\n", text)
    text = _IMAGE.sub("", text)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    text = _HEADING.sub("", text).replace("**", "")
    text = _FOOTNOTE_MARK.sub(r"\1", text)
    lines = [line.rstrip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def chars(text: str | None) -> int:
    """Non-space characters."""
    return sum(map(len, (text or "").split()))


@dataclass(frozen=True, slots=True)
class Page:
    """A page in scope and the text text.db gives it."""

    sha256: str
    page: int  # 1 for the first page
    types: tuple[str, ...]  # the section types it lies in
    source: str | None  # layer or ocr; None when text.db has no text for the page
    run: str | None  # the text.db run the text comes from
    raw: str | None  # the text as text.db holds it

    @property
    def plain(self) -> str:
        """`raw` without markup: an OCR answer as plain text (`ocr_plain_text`), the text layer as
        it is."""
        if self.source == "ocr":
            return ocr_plain_text(self.raw or "")
        return self.raw or ""

    @property
    def usable(self) -> bool:
        return chars(self.plain) >= MIN_CHARS

    @property
    def text(self) -> str:
        """The text the privacy check and the index read: `plain` with split words joined."""
        return join_split_words(self.plain)


def page_texts(text_db: sqlite3.Connection, sha256: str) -> sqlite3.Cursor:
    """(page, source, run, text) for each page of the document that text.db has text for.

    Text is read here and nowhere else: the `page_text` view, which gives the OCR answer if one is
    ok, else the text layer."""
    return text_db.execute(
        "SELECT page, source, run, text FROM page_text WHERE sha256 = ? ORDER BY page", (sha256,)
    )


def pages(serving_db: Path, text_db: Path) -> Iterator[Page]:
    """Every page in scope, by document (in sha256 order) and page. Opens both files read-only."""
    marks = ",".join("?" * len(TYPES))
    with closing(_read_only(serving_db)) as serving, closing(_read_only(text_db)) as text:
        sections = serving.execute(
            "SELECT s.sha256, s.type, s.first_page, s.last_page, d.page_count "
            "FROM sections s JOIN documents d USING (sha256) "
            f"WHERE s.type IN ({marks}) AND d.kind IS NOT 'bulletin' "
            "AND d.body_kind IS NOT 'private' ORDER BY s.sha256",
            TYPES,
        )
        for sha, group in groupby(sections, key=lambda row: row[0]):
            inside: dict[int, set[str]] = defaultdict(set)
            for _, kind, first, last, page_count in group:
                first, last = min(first, last), max(first, last)
                for page in range(max(first, 1), min(last, page_count or last) + 1):
                    inside[page].add(kind)
            found = {row[0]: row[1:] for row in page_texts(text, sha)}
            for page in sorted(inside):
                source, run, raw = found.get(page, (None, None, None))
                yield Page(sha, page, tuple(sorted(inside[page])), source, run, raw)


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
