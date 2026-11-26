"""Redaction: page text with the personal data the privacy check found replaced by a mark.

Nothing is changed in place. The text stays as text.db holds it and the spans as privacy.db holds
them; the index build calls `redact` each time it writes fulltext.db. A new privacy run or a change
here takes effect at the next build.

Every span of a label in MARKS is replaced, whatever its score: gheim's calibrated detector has
already decided it. Spans of a label in KEPT stay as text.

The detector often finds a person once or twice in a document and misses the later mentions.
`mentions` therefore gathers what it found on all pages of a document, and `redact` replaces it
wherever else it stands in that document: a name in full and each of its words alone (the surname
mentioned again, the first name), an address or e-mail address as found. Words that are not names
stay: titles and roles (Herr, Mme, Syndic, Gemeinderat), words without a lower-case letter (SVP,
KESB), words shorter than three letters, parts of commune names, and words the document also writes
in lower case (Page, Der). The detector sometimes takes a place for an address, so an address is
repeated only when it holds a number and a word that is not part of a commune's name.

Some personal data is found by its form, without the detector (`by_form`): a date of birth after
"geb.", "geboren", "né(e) le" or "nato/a il" (the word stays), the capitalised words right before
that word, which name the person born, and a Swiss mobile number.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

# A mark is a word in full-width brackets (U+FF3B, U+FF3D), which page text does not use. The index
# keeps these brackets inside a word (fulltext.SCHEMA), so a search for "Name" does not find the
# mark for a name.
OPEN, CLOSE = "\uff3b", "\uff3d"
MARKS = {
    label: f"{OPEN}{word}{CLOSE}"
    for label, word in (
        ("private_person", "name"),
        ("private_address", "address"),
        ("private_email", "email"),
        ("secret", "secret"),
        ("birth_date", "date"),
        ("mobile_phone", "phone"),
    )
}
# Dates, web pages, phone numbers and account numbers (IBAN, AHV) stay as text. Most are meeting
# dates and the commune's own pages, numbers and accounts; most "account numbers" the model finds
# are budget account codes.
KEPT = ("private_date", "private_url", "private_phone", "account_number")

# found by their form (group 1 of BIRTH_DATE is the date)
BIRTH_DATE = re.compile(
    r"(?<!\w)(?:geb\.|geboren(?:\s+am)?|née?\s+le|nat[oa]\s+il)\s*"
    r"(\d{1,2}\.?\s?(?:\d{1,2}\.\s?|[^\W\d_]{3,10}\.?\s)(?:19|20)\d{2})(?!\d)",
    re.IGNORECASE,
)
# the name right before a date of birth or a year of birth: one to four capitalised words
NAME_WORD = r"[A-ZÀ-ÖØ-Þ][\w'\u2019-]*"
BORN_NAME = re.compile(
    rf"({NAME_WORD}(?:[ \t]+{NAME_WORD}){{0,3}})[ \t]*[,(]?[ \t]*"
    r"(?i:geb\.|geboren|née?\s+le|nat[oa]\s+il)\s*(?:am\s+)?\d"
)
# a person listed with a year of birth, as naturalisation decisions do: the name before "1954,
# celibe" (a civil status), or after "al signor" or "alla signora" and before a year
CIVIL_STATUS = (
    r"celibe|nubile|coniugat[oa]|sposat[oa]|divorziat[oa]|vedov[oa]|separat[oa]|ledig|verheiratet|"
    r"geschieden|verwitwet|c[ée]libataire|mari[ée]e?|divorc[ée]e?|veu(?:f|ve)"
)
LISTED_NAME = rf"({NAME_WORD}(?:[ \t]+(?:{NAME_WORD}|nat[oa]|née?)){{0,5}})"
YEAR_NAME = re.compile(
    rf"{LISTED_NAME},[ \t]*(?:19|20)\d{{2}},[ \t]*(?i:{CIVIL_STATUS})(?!\w)"
    rf"|(?i:\bal(?:la)?[ \t]+signor[a]?)[ \t]+{LISTED_NAME},[ \t]*(?:19|20)\d{{2}}(?!\d)"
)
# not inside a figure: budgets write 1'076 234.50
MOBILE = re.compile(
    r"(?<![\w+.'\u2019-])(?:(?:\+|00)41\s?(?:\(0\)\s?)?|0)7[5-9][\s/]?\d{3}[\s.]?\d{2}[\s.]?\d{2}"
    r"(?![\d'\u2019]|[.,]\d)"
)


def by_form(text: str) -> list:
    """The spans found by their form: dates of birth, the name before them, names listed with a
    year of birth, mobile numbers."""
    listed = [(m, 1 if m.group(1) else 2) for m in YEAR_NAME.finditer(text)]
    return (
        [["birth_date", m.start(1), m.end(1), 1.0] for m in BIRTH_DATE.finditer(text)]
        + [["private_person", m.start(1), m.end(1), 1.0] for m in BORN_NAME.finditer(text)]
        + [["private_person", m.start(g), m.end(g), 1.0] for m, g in listed]
        + [["mobile_phone", m.start(), m.end(), 1.0] for m in MOBILE.finditer(text)]
    )


WORD = re.compile(r"[^\W\d_][\w'\u2019-]*")  # a letter, then letters, apostrophes, hyphens
# a word of running text, not part of an e-mail or web address (hans@dorf.ch, www.dorf.ch)
RUNNING = re.compile(r"(?<![@./\w])[^\W\d_][\w'\u2019-]*(?![\w@]|\.\w)")
# titles, roles, abbreviations and small words that stand in or next to names (lower case)
NOT_NAMES = frozenset(
    """
    herr herrn frau fräulein frl familie fam hr fr dr monsieur madame mademoiselle messieurs
    mesdames mme mmes mlle mlles signor signora signore signori sig sigg sigra dott dottor
    dottoressa doktor docteur prof professor ing lic dipl präsident präsidentin vizepräsident
    vizepräsidentin gemeindepräsident gemeindepräsidentin stadtpräsident stadtpräsidentin
    gemeinderat gemeinderätin gemeinderäte stadtrat stadträtin gemeindeschreiber
    gemeindeschreiberin stadtschreiber stadtschreiberin gemeindeammann syndic syndique maire
    municipal municipale conseiller conseillère président présidente secrétaire sindaco sindaca
    consigliere consigliera segretario segretaria presidente der die das den dem des ein eine und
    oder von vom les une pour par avec sur dans aux con per del della dei page seite pagina
    """.split()  # noqa: SIM905 - one word list reads better than a column of strings
)

RULE = (
    "Personal data found by the privacy check is replaced by a mark: "
    + ", ".join(f"{label} by {mark}" for label, mark in MARKS.items())
    + f" (full-width brackets). Kept as text: {', '.join(KEPT)}. Each span is replaced where it "
    "was found, whatever its score. What the check found anywhere in a document is also replaced "
    "wherever else it stands in that document: a name in full, and each of its words alone unless "
    "the word is a title or role, has no lower-case letter, is shorter than three letters, is "
    "part of a commune's name or stands in lower case elsewhere in the document; an address only "
    "when it holds a number and a word that is not part of a commune's name; an e-mail address "
    "or secret as found. A date of birth after geb., geboren, né(e) le or nato/a il, the "
    "capitalised words right before that word (the person born), a name listed with a year of "
    "birth (the capitalised words before '1954, celibe' or another civil status, or after 'al "
    "signor' or 'alla signora' and before a year) and a Swiss mobile number (07x, +41 7x) are "
    "found by their form. Overlapping spans become one mark. A "
    "page is indexed only when the privacy check read the same text."
)

Mentions = dict[str, re.Pattern]


def mentions(pages: Iterable[tuple[str, list]], keep: frozenset[str] = frozenset()) -> Mentions:
    """What the privacy check found on the pages of one document, given as (text, spans), as one
    pattern per label for `redact`. `keep`: the words of commune names, never names and never
    enough for an address."""
    pages = list(pages)
    # words the document also writes in lower case are ordinary words there (page, der, mars)
    ordinary = {word for text, _ in pages for word in RUNNING.findall(text) if word[0].islower()}

    def place(word: str) -> bool:
        return word in keep or any(part in keep for part in word.split("-"))

    def titled(word: str) -> bool:  # may be part of a name
        return (
            word[0].isupper()
            and any(c.islower() for c in word)
            and word.casefold() not in NOT_NAMES
        )

    def alone(word: str) -> bool:  # may be replaced on its own
        return len(word) >= 3 and word[0].lower() + word[1:] not in ordinary and not place(word)

    found: dict[str, set[str]] = {}
    for text, spans in pages:
        for label, start, end, _score in [*spans, *by_form(text)]:
            if label not in MARKS:
                continue
            words = text[start:end].split()
            variants = found.setdefault(label, set())
            if label == "private_person":
                names = [w for w in WORD.findall(" ".join(words)) if titled(w)]
                variants.update(re.escape(w) for w in names if alone(w))
                if len(names) >= 2:
                    variants.add(r"\s+".join(map(re.escape, names)))
                if len(names) == 2:
                    variants.add(r"\s+".join(map(re.escape, reversed(names))))
                continue
            if label == "private_address" and not (
                any(c.isdigit() for c in "".join(words))
                and any(not place(w) for w in WORD.findall(" ".join(words)))
            ):
                continue  # a place or a postcode and a place, not an address
            if len("".join(words)) >= 5:
                variants.add(r"\s+".join(map(re.escape, words)))
    patterns = {}
    for label, variants in found.items():
        if variants:
            ending = r"(?:s)?" if label == "private_person" else ""  # a genitive: Musters
            either = "|".join(sorted(variants, key=len, reverse=True))
            patterns[label] = re.compile(rf"(?<![\w{OPEN}])(?:{either}){ending}(?![\w{CLOSE}])")
    return patterns


def redact(text: str, spans: list, others: Mentions | None = None) -> tuple[str, Counter]:
    """`text` with each span [label, start, end, score] of a label in MARKS replaced, with every
    other mention of what the privacy check found in the document (`others`, from `mentions`), and
    with dates of birth and mobile numbers; and the number of marks per label. A label in neither
    MARKS nor KEPT is an error: it needs a decision."""
    unknown = {span[0] for span in spans} - MARKS.keys() - set(KEPT)
    if unknown:
        raise ValueError(f"no rule for the labels {sorted(unknown)}")
    hidden = [s for s in spans if s[0] in MARKS]
    for label, pattern in (others or {}).items():
        hidden += [[label, m.start(), m.end(), 1.0] for m in pattern.finditer(text)]
    hidden += by_form(text)
    hidden.sort(key=lambda s: (s[1], -s[2]))
    parts: list[str] = []
    marks: Counter = Counter()
    at = 0
    for label, start, end, _score in hidden:
        if start < at:  # overlaps the mark before
            at = max(at, end)
            continue
        parts += [text[at:start], MARKS[label]]
        marks[label] += 1
        at = end
    parts.append(text[at:])
    return "".join(parts), marks
