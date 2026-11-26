"""Coverage register: what every Swiss commune publishes, where, and why not.

Verified facts only, kept in `data/register/<CANTON>.yaml` (one file per canton, one row per
commune, sorted by BFS id). Crawl state (what the archive holds, when a site was last crawled) lives
in `DATA_DIR` and is never written here, so the tracked files only change when someone learns
something about a commune.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .config import settings
from .schemas import CommuneConfig

PublicationBody = Literal["assembly", "parliament", "executive"]
PublicationKind = Literal["minutes", "decisions", "extracts", "results", "agenda", "documents"]
Status = Literal[
    "configured",  # in communes.yaml, crawled
    "candidate",  # a listing was seen but nobody has written or validated a configuration
    "not_published",  # site checked, the body publishes no minutes or decisions
    "not_found",  # site checked, no page found within the time spent
    "blocked_robots",
    "blocked_js",
    "blocked_network",  # host unreachable from the crawler's network (i-web hosting block)
    "blocked_waf",
    "needs_feature",  # the crawler cannot express the listing yet (see the obstacle)
    "not_examined",
]

PUBLICATION_BODIES: tuple[str, ...] = PublicationBody.__args__  # type: ignore[attr-defined]
PUBLICATION_KINDS: tuple[str, ...] = PublicationKind.__args__  # type: ignore[attr-defined]
STATUSES: tuple[str, ...] = Status.__args__  # type: ignore[attr-defined]
BLOCKED_STATUSES = ("blocked_robots", "blocked_js", "blocked_network", "blocked_waf")

_CADENCE_PER_YEAR = re.compile(r"^(\d+)\s*(?:per year|a year|/\s*year)$")
_CADENCE_MONTHS = re.compile(r"^(\d+(?:\.\d+)?)\s*months?$")
_YEAR_OR_DATE = re.compile(r"^\d{4}(-\d{2}-\d{2})?$")
_CANTON = re.compile(r"^[A-Z]{2}$")


def cadence_interval_days(cadence: str | None) -> float | None:
    """`4 per year` and `3 months` both give about 91 days; None when no cadence is recorded."""
    if not cadence:
        return None
    text = cadence.strip().lower()
    if m := _CADENCE_PER_YEAR.match(text):
        return 365.25 / int(m[1])
    if m := _CADENCE_MONTHS.match(text):
        return 365.25 / 12 * float(m[1])
    raise ValueError(f"cadence {cadence!r}: use '<n> per year' or '<n> months'")


class Publication(BaseModel):
    """One thing a body publishes: the session minutes, a decision list, extracts, vote results or
    the agenda. `from` and `to` are years or ISO dates as a person read them off the site."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    body: PublicationBody
    kind: PublicationKind
    status: Status
    listing_url: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    cadence: str | None = None
    obstacle: str | None = None
    how: str | None = None

    @field_validator("from_", "to", mode="before")
    @classmethod
    def _year_or_date(cls, v):
        if v is None:
            return None
        text = v.isoformat() if isinstance(v, dt.date) else str(v)
        if not _YEAR_OR_DATE.match(text):
            raise ValueError(f"{text!r} is not a year or an ISO date")
        return text

    @field_validator("cadence")
    @classmethod
    def _cadence(cls, v):
        cadence_interval_days(v)
        return v

    @field_validator("listing_url")
    @classmethod
    def _absolute(cls, v):
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError(f"listing_url {v!r} is not absolute")
        return v

    @model_validator(mode="after")
    def _configured_needs_url(self):
        if self.status == "configured" and not self.listing_url:
            raise ValueError("a configured publication needs its listing_url")
        return self


class RegisterEntry(BaseModel):
    """One commune. `status` is the headline: `configured` as soon as one publication is crawled,
    otherwise the most informative thing a person found out."""

    model_config = ConfigDict(extra="forbid")

    bfs_id: int
    name: str
    canton: str
    status: Status = "not_examined"
    website: str | None = None
    vendor: str | None = None
    reachable: bool | None = None
    checked: dt.date | None = None
    checked_by: str | None = None
    notes: str | None = None
    publications: list[Publication] = Field(default_factory=list)

    @field_validator("canton")
    @classmethod
    def _canton(cls, v):
        if not _CANTON.match(v):
            raise ValueError(f"canton {v!r} is not a two-letter code")
        return v

    def publication_for(self, body: str) -> Publication | None:
        """The most advanced publication of a body (configured before candidate before the rest)."""
        pubs = [p for p in self.publications if p.body == body]
        return min(pubs, key=lambda p: STATUSES.index(p.status), default=None)


def publication_note(entry: RegisterEntry | None, pub: Publication | None) -> str | None:
    """What a reader needs to know about a publication: the obstacle first; for anything not yet
    configured also how it was found; the commune notes as a fallback. A configured publication
    without an obstacle needs no note (its `how` describes the configuration, not a problem)."""
    if entry is None:
        return None
    if pub is None:
        return entry.notes
    if pub.obstacle:
        return pub.obstacle
    if pub.status != "configured":
        return pub.how or entry.notes
    return None


def register_dir() -> Path:
    return settings.config_root / "register"


def read_register_files(directory: Path | None = None) -> list[tuple[Path, list[RegisterEntry]]]:
    """Every register file with its rows in file order. A file that does not validate raises a
    ValueError naming the file and the row."""
    d = directory or register_dir()
    out: list[tuple[Path, list[RegisterEntry]]] = []
    for path in sorted(d.glob("*.yaml")) if d.is_dir() else []:
        rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        entries: list[RegisterEntry] = []
        for i, item in enumerate(rows):
            try:
                entries.append(RegisterEntry.model_validate(item))
            except ValidationError as exc:
                who = item.get("name") or item.get("bfs_id") if isinstance(item, dict) else i
                problems = "; ".join(
                    f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()
                )
                raise ValueError(f"{path.name}, row {i + 1} ({who}): {problems}") from exc
        out.append((path, entries))
    return out


def load_register(directory: Path | None = None) -> list[RegisterEntry]:
    """All rows, sorted by canton then BFS id; [] when there is no register yet."""
    entries = [e for _, rows in read_register_files(directory) for e in rows]
    entries.sort(key=lambda e: (e.canton, e.bfs_id))
    return entries


def write_register(entries: list[RegisterEntry], directory: Path | None = None) -> list[Path]:
    """One file per canton, rows by BFS id, unset fields left out. Comments are not preserved."""
    d = directory or register_dir()
    d.mkdir(parents=True, exist_ok=True)
    by_canton: dict[str, list[RegisterEntry]] = {}
    for e in entries:
        by_canton.setdefault(e.canton, []).append(e)
    written = []
    for canton, rows in sorted(by_canton.items()):
        rows.sort(key=lambda e: e.bfs_id)
        data = []
        for e in rows:
            item = e.model_dump(by_alias=True, exclude_none=True)
            item["publications"] = [
                p.model_dump(by_alias=True, exclude_none=True) for p in e.publications
            ]
            if not item["publications"]:
                del item["publications"]
            data.append(item)
        path = d / f"{canton}.yaml"
        text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=110)
        path.write_text(
            f"# Register of commune publications, canton {canton}. What each commune publishes and whether it is collected.\n{text}",
            encoding="utf-8",
        )
        written.append(path)
    return written


def source_key(commune: CommuneConfig, source) -> tuple[int, str, str]:
    """A configured source is identified by the commune, its first listing URL and its body: one page can list two bodies' minutes, told apart
    by the sources' text patterns."""
    return commune.bfs_id, source.listing_urls[0], source.body or commune.body


def check(
    entries: list[RegisterEntry],
    communes: list[CommuneConfig],
    bfs: dict[int, tuple[str, str]] | None = None,
    files: list[tuple[Path, list[RegisterEntry]]] | None = None,
) -> list[str]:
    """Consistency of the register with communes.yaml and the BFS snapshot; [] when everything holds.

    `bfs` maps a BFS id to (name, canton); pass None when the snapshot is not available.
    `files` lets the check see file names and row order."""
    problems: list[str] = []
    by_id: dict[int, RegisterEntry] = {}
    for e in entries:
        if e.bfs_id in by_id:
            problems.append(f"{e.bfs_id} {e.name}: listed twice")
        by_id[e.bfs_id] = e
    for path, rows in files or []:
        for e in rows:
            if e.canton != path.stem:
                problems.append(f"{e.bfs_id} {e.name}: canton {e.canton} in file {path.name}")
        ids = [e.bfs_id for e in rows]
        if ids != sorted(ids):
            problems.append(f"{path.name}: rows are not sorted by bfs_id")
    if bfs is not None:
        for e in entries:
            official = bfs.get(e.bfs_id)
            if official is None:
                problems.append(f"{e.bfs_id} {e.name}: not in the BFS snapshot")
                continue
            name, canton = official
            if e.canton != canton:
                problems.append(f"{e.bfs_id} {e.name}: canton {e.canton}, BFS says {canton}")
            if e.name not in (name, name.split(" (")[0]):
                problems.append(f"{e.bfs_id} {e.name}: BFS name is {name}")
        if len(bfs) and len(entries) < len(bfs):
            missing = sorted(set(bfs) - set(by_id))
            problems.append(
                f"{len(missing)} communes of the BFS snapshot have no row, first: {missing[:5]}"
            )
    for e in entries:
        configured = [p for p in e.publications if p.status == "configured"]
        if configured and e.status != "configured":
            problems.append(
                f"{e.bfs_id} {e.name}: has a configured publication but status {e.status}"
            )
        if e.status == "configured" and not configured:
            problems.append(
                f"{e.bfs_id} {e.name}: status configured without a configured publication"
            )
        pairs = [(p.listing_url, p.body) for p in configured]
        if len(pairs) != len(set(pairs)):
            problems.append(
                f"{e.bfs_id} {e.name}: the same listing URL is configured twice for one body"
            )
    sources: dict[tuple[int, str, str], CommuneConfig] = {}
    for c in communes:
        for s in c.all_sources():
            sources[source_key(c, s)] = c
    seen: set[tuple[int, str, str]] = set()
    for e in entries:
        for p in e.publications:
            if p.status != "configured":
                continue
            key = (e.bfs_id, p.listing_url or "", p.body)
            if key not in sources:
                bodies = [b for (i, u, b) in sources if i == e.bfs_id and u == p.listing_url]
                if bodies:
                    seen.update(
                        (e.bfs_id, p.listing_url or "", b) for b in bodies
                    )  # reported once, here
                    problems.append(
                        f"{e.bfs_id} {e.name}: publication body {p.body}, communes.yaml says {', '.join(bodies)} for {p.listing_url}"
                    )
                else:
                    problems.append(
                        f"{e.bfs_id} {e.name}: configured {p.body} {p.kind} at {p.listing_url} is not a source in communes.yaml"
                    )
                continue
            seen.add(key)
    for key, c in sources.items():
        if key in seen:
            continue
        if c.bfs_id not in by_id:
            problems.append(f"{c.bfs_id} {c.name}: in communes.yaml but not in the register")
        else:
            problems.append(
                f"{c.bfs_id} {c.name}: source {key[1]} ({key[2]}) has no configured publication in the register"
            )
    return problems
