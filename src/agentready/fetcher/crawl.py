"""Breadth-first crawl of one site: ask crawl_policy, fetch politely, store, follow links."""

import sqlite3
from collections import Counter, deque
from dataclasses import dataclass, field

import httpx

from agentready.core import CrawlConfig, CrawlRun, CrawlStatus, FetchedResource, Link, Site
from agentready.crawl_policy import (
    RobotsRules,
    Verdict,
    check_scope,
    content_type_allowed,
    in_crawl_window,
    media_type,
    normalize_url,
    page_cap,
    should_fetch,
    type_from_extension,
)
from agentready.fetcher.clock import Clock, SystemClock
from agentready.fetcher.http import FetchResult, PoliteClient
from agentready.fetcher.links import HTML_TYPES, extract_links
from agentready.store import (
    RawStore,
    add_crawl_run,
    add_fetched_resource,
    add_links,
    latest_fetched_resource,
    update_crawl_run,
)

DOCUMENT_TYPES = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)


class StopReason:
    COMPLETED = "completed"
    PAGE_CAP_REACHED = "page_cap_reached"
    OUTSIDE_CRAWL_WINDOW = "outside_crawl_window"


@dataclass
class CrawlReport:
    run: CrawlRun
    stored_new: int = 0
    """Files written to the raw store for the first time."""
    not_modified: int = 0
    """304 responses: the stored copy was reused."""
    links_found: int = 0
    documents_found: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    """URLs not fetched, by reason (robots_disallowed, other_host, ...)."""
    robots_status: int | None = None


def without_crawl_window(config: CrawlConfig) -> CrawlConfig:
    """A copy of ``config`` with no crawl window (for manual runs). Delays still apply."""
    window = config.crawl_window.model_copy(update={"end": config.crawl_window.start})
    return config.model_copy(update={"crawl_window": window})


def is_document_url(url: str) -> bool:
    return type_from_extension(url) in DOCUMENT_TYPES


async def crawl_site(
    conn: sqlite3.Connection,
    raw: RawStore,
    site: Site,
    config: CrawlConfig,
    *,
    user_agent: str,
    max_pages: int | None = None,
    clock: Clock | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CrawlReport:
    """Crawl ``site`` once and record every request. Per-request errors never stop the run."""
    crawler = _Crawler(conn, raw, site, config, clock or SystemClock(), page_cap(config, max_pages))
    async with PoliteClient(
        config, user_agent=user_agent, clock=crawler.clock, transport=transport
    ) as client:
        return await crawler.run(client)


class _Crawler:
    def __init__(
        self,
        conn: sqlite3.Connection,
        raw: RawStore,
        site: Site,
        config: CrawlConfig,
        clock: Clock,
        cap: int,
    ) -> None:
        self.conn, self.raw, self.site, self.config, self.clock, self.cap = (
            conn,
            raw,
            site,
            config,
            clock,
            cap,
        )
        self.base_url = normalize_url(str(site.base_url)) or str(site.base_url)
        self.robots = RobotsRules()

    async def run(self, client: PoliteClient) -> CrawlReport:
        run = add_crawl_run(self.conn, CrawlRun(site_id=self.site.id, started_at=self.clock.now()))
        report = CrawlReport(run)
        try:
            stop_reason = await self._crawl(client, report)
        except BaseException as exc:
            report.run = self._finish(
                report, CrawlStatus.FAILED, f"error: {type(exc).__name__}: {exc}"
            )
            raise
        report.run = self._finish(report, CrawlStatus.FINISHED, stop_reason)
        return report

    async def _crawl(self, client: PoliteClient, report: CrawlReport) -> str:
        if not in_crawl_window(self.clock.now(), self.config.crawl_window):
            return StopReason.OUTSIDE_CRAWL_WINDOW

        self.robots, robots_result = await client.fetch_robots(self.base_url)
        report.robots_status = robots_result.status_code
        self._record(report, robots_result, previous=None)

        queue: deque[tuple[str, int]] = deque([(self.base_url, 0)])
        seen = {self.base_url}
        while queue:
            url, depth = queue.popleft()
            decision = should_fetch(
                url,
                base_url=self.base_url,
                robots=self.robots,
                pages_fetched=report.run.pages_fetched,
                cap=self.cap,
                now=self.clock.now(),
                config=self.config,
            )
            if decision.verdict is Verdict.OUTSIDE_CRAWL_WINDOW:
                return StopReason.OUTSIDE_CRAWL_WINDOW
            if decision.verdict is Verdict.PAGE_CAP_REACHED:
                return StopReason.PAGE_CAP_REACHED
            if not decision.allowed:
                report.skipped[str(decision.scope_reason or decision.verdict)] += 1
                continue
            assert decision.url is not None

            resource, body = await self._fetch(client, decision.url, report)
            report.run = report.run.model_copy(
                update={
                    "pages_fetched": report.run.pages_fetched + 1,
                    "error_count": report.run.error_count + (resource.error is not None),
                }
            )
            if body is None or media_type(resource.content_type) not in HTML_TYPES:
                continue
            for link_url in self._record_links(resource, body, report):
                if link_url in seen:
                    continue
                if depth < self.config.max_depth or (
                    depth == self.config.max_depth and is_document_url(link_url)
                ):
                    seen.add(link_url)
                    queue.append((link_url, depth + 1))
        return StopReason.COMPLETED

    async def _fetch(
        self, client: PoliteClient, url: str, report: CrawlReport
    ) -> tuple[FetchedResource, bytes | None]:
        previous = latest_fetched_resource(self.conn, self.site.id, url)
        conditional: dict[str, str] = {}
        if previous and previous.sha256 and self.raw.exists(previous.sha256):
            if previous.etag:
                conditional["If-None-Match"] = previous.etag
            if previous.last_modified:
                conditional["If-Modified-Since"] = previous.last_modified
        result = await client.fetch(
            url,
            keep_body=lambda content_type, u: content_type_allowed(content_type, u, self.config),
            conditional=conditional,
            may_follow=self._may_follow,
        )
        return self._record(report, result, previous if conditional else None)

    def _record(
        self, report: CrawlReport, result: FetchResult, previous: FetchedResource | None
    ) -> tuple[FetchedResource, bytes | None]:
        """Store the body (once per unique content) and add a fetched_resources row."""
        values: dict[str, object] = {
            "site_id": self.site.id,
            "crawl_run_id": report.run.id,
            "url": result.url,
            "final_url": result.final_url,
            "status_code": result.status_code,
            "content_type": result.content_type,
            "etag": result.etag,
            "last_modified": result.last_modified,
            "fetched_at": self.clock.now(),
            "error": result.error,
        }
        body = result.body
        if result.not_modified:
            if previous is None or previous.sha256 is None:
                values["error"] = "304 Not Modified without a stored copy"
            else:
                report.not_modified += 1
                body = self.raw.get(previous.sha256)
                values.update(
                    sha256=previous.sha256,
                    raw_path=previous.raw_path,
                    content_type=previous.content_type,
                    etag=result.etag or previous.etag,
                    last_modified=result.last_modified or previous.last_modified,
                )
        elif body is not None:
            stored = self.raw.put(body)
            report.stored_new += stored.created
            values.update(sha256=stored.sha256, raw_path=stored.raw_path)
        resource = add_fetched_resource(self.conn, FetchedResource.model_validate(values))
        return resource, body

    def _record_links(
        self, resource: FetchedResource, body: bytes, report: CrawlReport
    ) -> list[str]:
        """Save the page's links and return the in-scope ones, in page order."""
        assert resource.id is not None and report.run.id is not None
        page_url = str(resource.final_url or resource.url)
        rows: list[Link] = []
        follow: list[str] = []
        for found in extract_links(body, page_url):
            in_scope = check_scope(found.url, self.base_url, self.config).in_scope
            is_document = is_document_url(found.url)
            rows.append(
                Link(
                    crawl_run_id=report.run.id,
                    site_id=self.site.id,
                    from_resource_id=resource.id,
                    url=found.url,
                    text=found.text,
                    in_scope=in_scope,
                    is_document=is_document,
                )
            )
            report.documents_found += is_document
            if in_scope:
                follow.append(found.url)
        report.links_found += add_links(self.conn, rows)
        return follow

    def _may_follow(self, target: str) -> str | None:
        scope = check_scope(target, self.base_url, self.config)
        if not scope.in_scope:
            return f"out of scope ({scope.reason})"
        if not self.robots.can_fetch(target):
            return "disallowed by robots.txt"
        return None

    def _finish(self, report: CrawlReport, status: CrawlStatus, reason: str) -> CrawlRun:
        run = report.run.model_copy(
            update={"finished_at": self.clock.now(), "status": status, "stop_reason": reason}
        )
        return update_crawl_run(self.conn, run)
