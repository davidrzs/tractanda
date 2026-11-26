"""HTTP for the crawl: robots.txt, rate limiting per host and per hosting network, retries.

Etiquette: robots.txt honoured for every URL requested, redirect targets included, and a host whose
robots.txt answers 5xx is left alone for the run; descriptive User-Agent; one request per second per
host and per hosting network; Crawl-delay honoured.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import email.utils
import logging
import random
import re
import socket
import threading
import time
from collections import defaultdict
from typing import ClassVar
from urllib.parse import urlsplit

import httpx

from ..config import settings
from . import access, cooldown

log = logging.getLogger(__name__)

ATTEMPTS = 3  # per request, on connect errors, timeouts, 5xx and 429
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
BACKOFF_SECONDS = (
    2.0  # first pause between attempts; doubled each time, plus up to a second of jitter
)
RETRY_AFTER_MAX = 120.0  # a longer Retry-After is honoured by not retrying at all
MAX_REDIRECTS = 10
# Product tokens the crawler used before its current name (lower case). robots.txt groups written
# for them still apply to it.
FORMER_NAMES = ("opencommunebot",)


class RobotsRules:
    """Minimal robots.txt: groups by User-agent, Allow and Disallow with `*` and `$` wildcards, Crawl-delay.
    The standard library parser ignores wildcards and would fetch what `Disallow: /*.pdf$` forbids."""

    def __init__(self, text: str, user_agent: str) -> None:
        # Our name and the names we used before: a group written for any of them binds us.
        tokens = [user_agent.split("/")[0].strip().lower(), *FORMER_NAMES]
        groups: list[tuple[list[str], list[tuple[bool, str]], float | None]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        delay: float | None = None
        in_rules = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if in_rules:
                    groups.append((agents, rules, delay))
                    agents, rules, delay, in_rules = [], [], None, False
                agents.append(value.lower())
            elif key in ("allow", "disallow"):
                in_rules = True
                if value:
                    rules.append((key == "allow", value))
            elif key == "crawl-delay":
                in_rules = True
                with contextlib.suppress(ValueError):
                    delay = float(value)
        if agents:
            groups.append((agents, rules, delay))
        named = [
            g
            for g in groups
            if any(a != "*" and any(t.startswith(a) or a in t for t in tokens) for a in g[0])
        ]
        # The groups that name us all apply, so a refusal of a former name still holds (a URL is
        # allowed only if each allows it); without one, the `*` group applies.
        chosen = named or [g for g in groups if "*" in g[0]][:1]
        self._groups = [[(allow, pat, self._compile(pat)) for allow, pat in g[1]] for g in chosen]
        delays = [g[2] for g in chosen if g[2] is not None]
        self.crawl_delay: float | None = max(delays) if delays else None

    @staticmethod
    def _compile(pattern: str) -> re.Pattern[str]:
        anchored = pattern.endswith("$")
        core = pattern[:-1] if anchored else pattern
        return re.compile(
            "^" + ".*".join(re.escape(part) for part in core.split("*")) + ("$" if anchored else "")
        )

    def allowed(self, url: str) -> bool:
        """In each group that applies, the longest matching rule wins, Allow wins a tie and no
        match means allowed; the URL is allowed only if every group allows it."""
        return self.refusing_rule(url) is None

    def refusing_rule(self, url: str) -> str | None:
        """The Disallow pattern that refuses the URL, or None when it is allowed."""
        parts = urlsplit(url)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        for rules in self._groups:
            best: tuple[int, bool, str] | None = None
            for allow, pattern, rx in rules:
                if rx.match(target) and (
                    best is None or len(pattern) > best[0] or (len(pattern) == best[0] and allow)
                ):
                    best = (len(pattern), allow, pattern)
            if best is not None and not best[1]:
                return best[2]
        return None


class Politeness:
    """Rate limiting per host and per hosting network (/24), plus robots.txt checks.

    Many Swiss commune sites share one provider (i-web alone serves hundreds from 195.65.10.0/24);
    limiting per hostname only is how a polite crawler still gets its IP dropped."""

    _network_last: ClassVar[dict[str, float]] = {}  # shared across instances and threads
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, client: httpx.Client, delay: float, user_agent: str) -> None:
        self.client = client
        self.delay = delay
        self.user_agent = user_agent
        self.requests = 0  # every request sent through _send, robots.txt and redirect hops included
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotsRules | None] = {}
        self._robots_status: dict[str, str] = {}
        self._robots_locks = defaultdict(threading.RLock)
        self._crawl_delay: dict[str, float] = {}  # per host, from robots.txt Crawl-delay
        self._networks: dict[str, str] = {}

    def _network(self, host: str) -> str:
        if host not in self._networks:
            try:
                ip = socket.gethostbyname(host.split(":")[0])
                self._networks[host] = ".".join(ip.split(".")[:3])
            except OSError:
                self._networks[host] = host
        return self._networks[host]

    def wait(self, url: str) -> None:
        _check_host_cooldown(url)
        host = urlsplit(url).netloc
        net = self._network(host)
        # Reserve the next slot under the lock and sleep outside it: a thread waiting on one host
        # must not hold up threads on other hosts and networks. A host's own Crawl-delay is waited
        # out first, without a reservation, so it never stretches the network's one-second cadence.
        while True:
            with self._lock:
                now = time.monotonic()
                host_delay = max(self.delay, self._crawl_delay.get(host, 0.0))
                host_due = self._last.get(host, 0.0) + host_delay
                if host_due <= now + self.delay:
                    slot = max(now, host_due, Politeness._network_last.get(net, 0.0) + self.delay)
                    self._last[host] = slot
                    Politeness._network_last[net] = slot
                    break
            time.sleep(min(host_due - now, 5.0))
        pause = slot - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        _check_host_cooldown(url)

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = parts.netloc
        with self._robots_locks[host]:
            # A long explicit Retry-After from robots.txt is a temporary host
            # cooldown, not a permanent robots prohibition for this document.
            _check_host_cooldown(url)
            # Preserve the current run's robots verdict when robots.txt itself
            # returned a temporary HTTP error without a durable cooldown.
            cached = self._robots.get(host)
            if (
                self._robots_status.get(host) in {"rate_limited", "robots_unavailable"}
                and cached is not None
                and not cached.allowed(url)
            ):
                access.record(url, self._robots_status[host], user_agent=self.user_agent)
                return False
            if host not in self._robots:
                self._robots[host] = self._fetch_rules(parts.scheme, host)
            _check_host_cooldown(url)
            rules = self._robots[host]
            refused_by = None if rules is None else rules.refusing_rule(url)
            allowed = refused_by is None
            status = self._robots_status.get(host, "ok")
            matched = None
            if status == "ok" and not allowed:
                status = "restricted"
                matched = refused_by
            access.record(url, status, rule=matched, user_agent=self.user_agent)
            return allowed

    def robots_status(self, url: str) -> str:
        """How this run read the host's robots.txt: ok, robots_unavailable or rate_limited."""
        return self._robots_status.get(urlsplit(url).netloc, "ok")

    def _fetch_rules(self, scheme: str, host: str) -> RobotsRules | None:
        """None means no rules: no robots.txt (other 4xx). A 429 disallows the host for this run,
        as do 5xx answers and an unreachable server (timeout, connection or TLS error; RFC 9309,
        section 2.3.1.4: unreachable through server or network errors means complete disallow)."""
        url = f"{scheme}://{host}/robots.txt"
        self._robots_status[host] = "ok"
        try:
            r = _send(self.client, self, self.client.build_request("GET", url), check_hops=False)
        except FetchFailure as exc:
            if exc.status is not None and (exc.status == 429 or exc.status >= 500):
                log.warning("%s answered %s: host disallowed for this run", url, exc.message)
                self._robots_status[host] = (
                    "rate_limited" if exc.status == 429 else "robots_unavailable"
                )
                return _disallow_all(self.user_agent)
            self._robots_status[host] = "robots_unavailable"
            log.warning("%s unreachable (%s): host disallowed for this run", url, exc.message)
            return _disallow_all(self.user_agent)
        if r.status_code == 429 or r.status_code >= 500:
            self._robots_status[host] = (
                "rate_limited" if r.status_code == 429 else "robots_unavailable"
            )
            log.warning("%s answered HTTP %d: host disallowed for this run", url, r.status_code)
            return _disallow_all(self.user_agent)
        if r.status_code != 200:
            if r.status_code not in {404, 410}:
                self._robots_status[host] = "robots_unavailable"
            return None
        rules = RobotsRules(r.text, self.user_agent)
        if rules.crawl_delay:
            self._crawl_delay[host] = rules.crawl_delay
            log.info("%s asks for a crawl delay of %ss", host, rules.crawl_delay)
        return rules


def _disallow_all(user_agent: str) -> RobotsRules:
    return RobotsRules("User-agent: *\nDisallow: /\n", user_agent)


class FetchFailure(Exception):
    """One request, page or download failed for a known reason. Raised inside the HTTP helpers and
    caught at the page or source level, where it is recorded as a FetchError."""

    def __init__(
        self,
        kind: str,
        url: str,
        message: str = "",
        status: int | None = None,
        retry_after: str | None = None,
        retry_at: float | None = None,
    ) -> None:
        super().__init__(f"{kind} {url}: {message}")
        self.kind = kind
        self.url = url
        self.message = message
        self.status = status  # HTTP status when the failure was a response
        self.retry_after = retry_after
        self.retry_at = retry_at


class Scoped:
    """Politeness limited to a source's scope: a URL outside it fails before any request is sent,
    redirect destinations included, since the client asks `allowed` before following one."""

    def __init__(self, polite, accepts, reason: str):
        self.polite, self.accepts, self.reason = polite, accepts, reason

    def allowed(self, url: str) -> bool:
        if not self.accepts(url):
            raise FetchFailure("external", url, self.reason)
        return self.polite.allowed(url)

    def __getattr__(self, name):
        return getattr(self.polite, name)


def _check_host_cooldown(url: str) -> None:
    try:
        active = cooldown.active(url)
    except (cooldown.CooldownStateError, OSError, OverflowError, ValueError) as exc:
        raise FetchFailure("http", url, f"local host cooldown state unavailable: {exc}") from exc
    if active:
        raise FetchFailure(
            "http",
            url,
            f"host cooldown until {active['until_utc']} after HTTP {active['status']} Retry-After",
            active["status"],
            retry_at=active["until_epoch"],
        )


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """No crawl proxy: requests leave from the machine itself. Only a machine-level egress proxy
    set in the environment (HTTP(S)_PROXY), where a network requires one, is used."""
    return httpx.Client(
        headers={"User-Agent": settings.ua, "Accept-Language": "de-CH,fr-CH,it-CH,en;q=0.5"},
        follow_redirects=True,
        timeout=httpx.Timeout(60.0, connect=15.0),
        transport=transport,
    )


# --- HTTP ---------------------------------------------------------------------


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__} {exc}".strip()


def _send(
    client: httpx.Client,
    polite: Politeness,
    request: httpx.Request,
    stream: bool = False,
    check_hops: bool = True,
) -> httpx.Response:
    """Send one request after the politeness pause, with up to ATTEMPTS attempts on connect errors,
    timeouts, 5xx and 429: exponential backoff with jitter, or the server's Retry-After, which is
    honoured by giving up when it exceeds RETRY_AFTER_MAX. Other 4xx are never retried. Redirects
    are followed one hop at a time, each target checked against robots.txt (unless `check_hops` is
    off, for robots.txt itself) and rate-limited before it is requested. Transport errors become a
    FetchFailure of kind timeout or http; the last response is returned whatever its status."""
    url = str(request.url)
    for attempt in range(1, ATTEMPTS + 1):
        _check_host_cooldown(url)
        polite.wait(url)
        polite.requests += 1
        delay = None
        try:
            r = _send_following(client, polite, request, stream, check_hops)
        except httpx.TimeoutException as exc:
            failure = FetchFailure("timeout", url, _describe(exc))
        except httpx.TransportError as exc:
            failure = FetchFailure("http", url, _describe(exc))
        except httpx.HTTPError as exc:  # an invalid URL, a protocol error: a retry would not help
            raise FetchFailure("http", url, _describe(exc)) from exc
        else:
            if r.status_code in RETRY_STATUS:
                delay = _retry_after(r)
                if delay is not None and delay > RETRY_AFTER_MAX:
                    try:
                        recorded = cooldown.record(str(r.url), delay, r.status_code)
                    except (
                        cooldown.CooldownStateError,
                        OSError,
                        OverflowError,
                        ValueError,
                    ) as exc:
                        r.close()
                        raise FetchFailure(
                            "http", url, f"cannot persist host Retry-After: {exc}", r.status_code
                        ) from exc
                    r.close()
                    if check_hops:
                        access.record(
                            url,
                            "rate_limited" if r.status_code == 429 else "technical_error",
                            channel="request",
                        )
                    raise FetchFailure(
                        "http",
                        url,
                        f"HTTP {r.status_code}, Retry-After {delay:.0f}s",
                        r.status_code,
                        r.headers.get("retry-after"),
                        recorded["until_epoch"],
                    )
            if r.status_code not in RETRY_STATUS or attempt == ATTEMPTS:
                if check_hops:
                    access.record(
                        url,
                        "rate_limited"
                        if r.status_code == 429
                        else "technical_error"
                        if r.status_code == 403 or r.status_code >= 500
                        else "ok",
                        channel="request",
                    )
                return r
            failure = FetchFailure("http", url, f"HTTP {r.status_code}", r.status_code)
            r.close()
        if attempt < ATTEMPTS:
            pause = (
                delay
                if delay is not None
                else BACKOFF_SECONDS * 2 ** (attempt - 1) + random.uniform(0, 1)
            )
            log.info(
                "%s: %s, attempt %d of %d in %.1fs",
                url,
                failure.message,
                attempt + 1,
                ATTEMPTS,
                pause,
            )
            _pause(pause)
    if check_hops:
        access.record(url, "technical_error", channel="request")
    raise failure


def _send_following(
    client, polite, request: httpx.Request, stream: bool, check_hops: bool
) -> httpx.Response:
    """Send and follow redirects hop by hop; raises FetchFailure (robots) before a disallowed hop.
    The closed redirect responses are kept in the answer's `history`, as httpx does."""
    url = str(request.url)
    hops: list[httpx.Response] = []
    r = client.send(request, stream=stream, follow_redirects=False)
    for _ in range(MAX_REDIRECTS):
        if not r.is_redirect or r.next_request is None:
            r.history = hops
            return r
        following = r.next_request
        target = str(following.url)
        r.close()
        hops.append(r)
        _check_host_cooldown(target)
        if check_hops and not polite.allowed(target):
            raise FetchFailure("robots", url, f"redirect to {target} is disallowed by robots.txt")
        polite.wait(target)
        polite.requests += 1
        r = client.send(following, stream=stream, follow_redirects=False)
    r.close()
    raise FetchFailure("http", url, f"more than {MAX_REDIRECTS} redirects")


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds asked for by a Retry-After header, given as a delay or an HTTP date; None without one."""
    value = r.headers.get("retry-after", "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return max(0.0, (when - dt.datetime.now(dt.UTC)).total_seconds())


def _ensure_ok(r: httpx.Response, url: str) -> None:
    if r.status_code >= 400:
        r.close()
        raise FetchFailure("http", url, f"HTTP {r.status_code}", r.status_code)


def _pause(seconds: float) -> None:
    time.sleep(seconds)
