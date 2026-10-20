"""Which browser requests may reach the site's server, and how fast. No browser code here."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit

from agentready.core import AuditConfig, CrawlConfig
from agentready.crawl_policy import (
    RobotsRules,
    host_delay,
    normalize_url,
    same_site,
    seconds_until_allowed,
)


class Clock(Protocol):
    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)


class Block(StrEnum):
    RESOURCE_TYPE = "resource_type"
    NOT_HTTP = "not_http"
    OTHER_HOST = "other_host"
    ROBOTS = "robots_disallowed"
    PAGE_CAP = "page_request_cap"


@dataclass(frozen=True)
class Cached:
    status: int
    headers: dict[str, str]
    body: bytes


@dataclass
class PageBudget:
    """Requests for the page being audited."""

    cap: int
    made: int = 0
    blocked: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def block(self, reason: str) -> None:
        self.blocked += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1


class RequestGate:
    """Decides each subresource request and spaces out the ones that go to the server.

    Responses are cached for the whole audit, so a stylesheet shared by every page is
    fetched once.
    """

    def __init__(
        self,
        *,
        site_url: str,
        robots: RobotsRules,
        crawl: CrawlConfig,
        audit: AuditConfig,
        clock: Clock,
    ) -> None:
        self.site_host = urlsplit(site_url).hostname or ""
        self.robots = robots
        self.audit = audit
        self.clock = clock
        self.delay = host_delay(crawl, robots)
        self.cache: dict[str, Cached] = {}
        self.request_times: list[tuple[str, datetime]] = []
        self._last_done: datetime | None = None
        self._lock = asyncio.Lock()

    def check(self, url: str, resource_type: str, budget: PageBudget) -> str | Block:
        """The normalised URL to load, or the reason it is blocked (no request is made)."""
        if resource_type not in self.audit.resource_types:
            return Block.RESOURCE_TYPE
        normalised = normalize_url(url)
        if normalised is None:
            return Block.NOT_HTTP
        if not same_site(urlsplit(normalised).hostname or "", self.site_host):
            return Block.OTHER_HOST
        if not self.robots.can_fetch(normalised):
            return Block.ROBOTS
        if normalised not in self.cache and budget.made >= budget.cap:
            return Block.PAGE_CAP
        return normalised

    async def turn(self, url: str) -> "_Turn":
        """Wait for the host's delay; use as ``async with await gate.turn(url):``."""
        await self._lock.acquire()
        try:
            wait = seconds_until_allowed(self._last_done, self.clock.now(), self.delay)
            await self.clock.sleep(wait)
        except BaseException:
            self._lock.release()
            raise
        return _Turn(self, url)


class _Turn:
    def __init__(self, gate: RequestGate, url: str) -> None:
        self.gate = gate
        self.url = url

    async def __aenter__(self) -> None:
        self.gate.request_times.append((self.url, self.gate.clock.now()))

    async def __aexit__(self, *exc_info: object) -> None:
        # The next request waits a full delay after this one has finished.
        self.gate._last_done = self.gate.clock.now()
        self.gate._lock.release()
