"""Render a site's most important pages in Chromium and run axe-core on each one."""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Browser, Page, Request, Route, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from agentready.a11y_auditor.axe import axe_options, findings_from_axe
from agentready.a11y_auditor.gate import Block, Cached, Clock, PageBudget, RequestGate, SystemClock
from agentready.core import (
    AuditConfig,
    AuditedPage,
    AuditFinding,
    AuditKind,
    CrawlConfig,
    FetchedResource,
    Site,
)
from agentready.crawl_policy import (
    DISALLOW_ALL,
    RobotsRules,
    in_crawl_window,
    max_file_bytes,
    normalize_url,
    robots_from_response,
)
from agentready.store import (
    RawStore,
    get_fetched_in_run,
    latest_finished_crawl_run,
    most_linked_pages,
    replace_audit_results,
)

# Headers that describe the original transfer, not the body we hand back to the browser.
_DROP_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})


class AuditError(RuntimeError):
    pass


@dataclass
class A11yReport:
    site_id: str
    crawl_run_id: int
    pages: list[AuditedPage] = field(default_factory=list)
    findings: list[AuditFinding] = field(default_factory=list)
    blocked: dict[str, int] = field(default_factory=dict)
    """Subresources not loaded, by reason, across all pages."""


def pages_to_audit(
    conn: sqlite3.Connection, site: Site, crawl_run_id: int, count: int
) -> list[FetchedResource]:
    """The home page (if stored) plus the most-linked HTML pages of the crawl run."""
    home_url = normalize_url(str(site.base_url)) or str(site.base_url)
    chosen: list[FetchedResource] = []
    home = get_fetched_in_run(conn, crawl_run_id, home_url)
    if home is not None and home.sha256 is not None and _is_html(home):
        chosen.append(home)
    remaining = count - len(chosen)
    if remaining > 0:
        chosen.extend(
            resource
            for resource, _inbound in most_linked_pages(
                conn, crawl_run_id, limit=remaining, exclude=[home_url]
            )
        )
    return chosen


def robots_for_run(
    conn: sqlite3.Connection, raw: RawStore, site: Site, crawl_run_id: int, user_agent: str
) -> RobotsRules:
    """robots.txt as stored by the crawl; unknown means nothing may be fetched."""
    parts = urlsplit(normalize_url(str(site.base_url)) or str(site.base_url))
    row = get_fetched_in_run(conn, crawl_run_id, f"{parts.scheme}://{parts.netloc}/robots.txt")
    if row is None:
        return DISALLOW_ALL
    body = raw.get(row.sha256) if row.sha256 and raw.exists(row.sha256) else b""
    return robots_from_response(row.status_code, body, user_agent)


async def audit_site(
    conn: sqlite3.Connection,
    raw: RawStore,
    site: Site,
    crawl: CrawlConfig,
    audit: AuditConfig,
    *,
    user_agent: str,
    pages: int | None = None,
    clock: Clock | None = None,
) -> A11yReport:
    """Audit the pages stored by the site's latest finished crawl and save the results.

    Replaces the site's previous accessibility results; agent-readiness results stay.
    """
    clock = clock or SystemClock()
    if not in_crawl_window(clock.now(), crawl.crawl_window):
        raise AuditError("outside the crawl window (see crawl_window in config/crawl.yaml)")
    run = latest_finished_crawl_run(conn, site.id)
    if run is None or run.id is None:
        raise AuditError(f"no finished crawl for {site.id}; run `agentready crawl` first")
    axe_source = _read_axe(audit.axe_path)

    targets = pages_to_audit(conn, site, run.id, pages or audit.pages_per_site)
    gate = RequestGate(
        site_url=str(site.base_url),
        robots=robots_for_run(conn, raw, site, run.id, user_agent),
        crawl=crawl,
        audit=audit,
        clock=clock,
    )
    report = A11yReport(site.id, run.id)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for target in targets:
                page_result, findings, budget = await _audit_page(
                    browser, raw, site, target, gate, crawl, audit, axe_source, user_agent, clock
                )
                report.pages.append(page_result)
                report.findings.extend(findings)
                for reason, n in budget.reasons.items():
                    report.blocked[reason] = report.blocked.get(reason, 0) + n
        finally:
            await browser.close()

    replace_audit_results(conn, site.id, AuditKind.A11Y, report.pages, report.findings)
    return report


async def _audit_page(
    browser: Browser,
    raw: RawStore,
    site: Site,
    target: FetchedResource,
    gate: RequestGate,
    crawl: CrawlConfig,
    audit: AuditConfig,
    axe_source: str,
    user_agent: str,
    clock: Clock,
) -> tuple[AuditedPage, list[AuditFinding], PageBudget]:
    page_url = normalize_url(str(target.final_url or target.url)) or str(target.url)
    budget = PageBudget(cap=audit.max_requests_per_page)
    base = {"site_id": site.id, "kind": AuditKind.A11Y, "url": page_url}

    if target.sha256 is None or not raw.exists(target.sha256):
        return (
            AuditedPage(**base, audited_at=clock.now(), ok=False, error="stored copy missing"),
            [],
            budget,
        )
    document = raw.get(target.sha256)
    content_type = target.content_type or "text/html"

    context = await browser.new_context(
        user_agent=user_agent,
        bypass_csp=True,  # so axe can be injected into pages with a strict CSP
        service_workers="block",
        ignore_https_errors=False,
    )
    try:
        # Hide AMD/CommonJS globals while axe loads so it always attaches to window.axe.
        await context.add_init_script(
            "(function (define, module, exports) {\n" + axe_source + "\n}).call(window);"
        )
        # Playwright's evaluate needs the browser's own eval and Function. Some pages replace
        # them (seen on pred.telangana.gov.in), which would make every page audit fail.
        await context.add_init_script(_LOCK_EVAL)
        page = await context.new_page()

        async def route(route: Route, request: Request) -> None:
            await _handle(
                route, request, page, page_url, document, content_type, gate, crawl, budget
            )

        await page.route("**/*", route)
        # Each allowed subresource can take a full host delay, so the load event of a
        # page with many CSS/JS files takes far longer than an unthrottled browser.
        load_budget = audit.page_timeout_seconds + audit.max_requests_per_page * gate.delay
        note: str | None = None
        try:
            await page.goto(
                page_url,
                wait_until="commit",  # the stored page is served at once; scripts may block parsing
                timeout=audit.page_timeout_seconds * 1000,
            )
            try:
                await page.wait_for_load_state("load", timeout=load_budget * 1000)
            except PlaywrightTimeoutError:
                note = f"load event timed out after {load_budget:.0f}s; audited the page as loaded"
            await page.wait_for_timeout(audit.settle_seconds * 1000)
            results = await _run_axe(page, audit.axe_tags)
        except PlaywrightError as exc:
            error = str(exc).splitlines()[0][:300]
            failed = AuditedPage(
                **base,
                audited_at=clock.now(),
                ok=False,
                error=error,
                requests_made=budget.made,
                requests_blocked=budget.blocked,
            )
            return failed, [], budget
    finally:
        await context.close()

    audited_at = clock.now()
    findings = findings_from_axe(results, site_id=site.id, url=page_url, audited_at=audited_at)
    result = AuditedPage(
        **base,
        audited_at=audited_at,
        ok=True,
        error=note,
        violations=len(findings),
        requests_made=budget.made,
        requests_blocked=budget.blocked,
    )
    return result, findings, budget


async def _handle(
    route: Route,
    request: Request,
    page: Page,
    page_url: str,
    document: bytes,
    content_type: str,
    gate: RequestGate,
    crawl: CrawlConfig,
    budget: PageBudget,
) -> None:
    """Serve the stored page, serve or politely fetch allowed subresources, block the rest."""
    if request.is_navigation_request() and request.frame == page.main_frame:
        if normalize_url(request.url) == page_url:
            await route.fulfill(status=200, headers={"content-type": content_type}, body=document)
        else:
            # A script or meta refresh tried to leave. Aborting would swap the page for
            # Chromium's error page; "204 No Content" makes the browser stay where it is.
            budget.block("navigation")
            await route.fulfill(status=204, body=b"")
        return

    decision = gate.check(request.url, request.resource_type, budget)
    if isinstance(decision, Block):
        budget.block(str(decision))
        await route.abort("blockedbyclient")
        return

    cached = gate.cache.get(decision)
    if cached is None:
        budget.made += 1
        try:
            async with await gate.turn(decision):
                # No automatic redirects: the browser re-requests the Location, and that
                # request goes through this gate like any other.
                response = await route.fetch(
                    max_redirects=0, timeout=crawl.request_timeout_seconds * 1000
                )
                body = await response.body()
        except PlaywrightError:
            budget.block("failed")
            await route.abort("failed")
            return
        if len(body) > max_file_bytes(crawl):
            budget.block("too_large")
            await route.abort("blockedbyclient")
            return
        headers = {k: v for k, v in response.headers.items() if k.lower() not in _DROP_HEADERS}
        cached = Cached(response.status, headers, body)
        gate.cache[decision] = cached
    await route.fulfill(status=cached.status, headers=cached.headers, body=cached.body)


_LOCK_EVAL = """
for (const name of ["eval", "Function"]) {
  const value = window[name];
  Object.defineProperty(window, name, { value, writable: false, configurable: false });
}
"""

# axe-core is added as an init script (see _audit_page), so it is defined before any of
# the page's own scripts run and they can't interfere with loading it.
_RUN_AXE = """
async (options) => {
  if (typeof window.axe === "undefined") throw new Error("axe-core missing from page");
  return await window.axe.run(document, options);
}
"""


async def _run_axe(page: Page, tags: list[str]) -> dict[str, object]:
    try:
        return await page.evaluate(_RUN_AXE, axe_options(tags))
    except PlaywrightError as exc:
        if "Execution context was destroyed" not in str(exc):
            raise
        # The page replaced its document after loading; run once more on what is there.
        await page.wait_for_load_state("domcontentloaded")
        return await page.evaluate(_RUN_AXE, axe_options(tags))


def _is_html(resource: FetchedResource) -> bool:
    media = (resource.content_type or "").split(";", 1)[0].strip().lower()
    return media in ("text/html", "application/xhtml+xml")


def _read_axe(path: Path) -> str:
    if not path.is_file():
        raise AuditError(f"axe-core not found at {path} (see vendor/README.md)")
    return path.read_text(encoding="utf-8")
