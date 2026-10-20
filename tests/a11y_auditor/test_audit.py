import json
from datetime import UTC, datetime

import pytest
from pytest_httpserver import HTTPServer

from agentready.a11y_auditor import AuditError, pages_to_audit
from agentready.core import AuditFinding, AuditKind, Severity
from agentready.store import (
    add_audit_findings,
    latest_finished_crawl_run,
    list_audit_findings,
    list_audited_pages,
)

from ..fetcher.conftest import UA, requested_paths
from .conftest import AuditEnv, audit_config, serve

EXPECTED_VIOLATIONS = {
    "html-has-lang",
    "document-title",
    "image-alt",
    "label",
    "link-name",
    "button-name",
    "color-contrast",
}


def by_page(findings: list[AuditFinding]) -> dict[str, set[str]]:
    pages: dict[str, set[str]] = {}
    for finding in findings:
        pages.setdefault(finding.url.path or "/", set()).add(finding.check_id)
    return pages


def test_clean_page_has_no_findings_and_bad_page_has_the_known_ones(
    env: AuditEnv, httpserver: HTTPServer
) -> None:
    serve(httpserver)
    env.crawl_then_clear(httpserver)
    report = env.audit()

    rules = by_page(report.findings)
    assert "/" not in rules  # clean page: zero violations
    assert "/other.html" not in rules
    assert rules["/bad.html"] == EXPECTED_VIOLATIONS
    assert all(page.ok for page in report.pages)
    assert {page.url.path: page.violations for page in report.pages} == {
        "/": 0,
        "/bad.html": len(EXPECTED_VIOLATIONS),
        "/other.html": 0,
    }


def test_results_are_saved_one_finding_per_rule_per_page(
    env: AuditEnv, httpserver: HTTPServer
) -> None:
    serve(httpserver)
    env.crawl_then_clear(httpserver)
    env.audit()

    stored = list_audit_findings(env.conn, env.site.id)
    assert len(stored) == len(EXPECTED_VIOLATIONS)
    assert all(f.kind is AuditKind.A11Y for f in stored)
    assert len(list_audited_pages(env.conn, env.site.id, AuditKind.A11Y)) == 3

    image_alt = next(f for f in stored if f.check_id == "image-alt")
    assert image_alt.severity is Severity.CRITICAL
    assert image_alt.message == "Images must have alternative text"
    evidence = json.loads(image_alt.evidence or "{}")
    assert evidence["nodes"] == 2  # both images without alt text, including the linked one
    assert evidence["examples"][0]["html"].startswith("<img")
    assert "wcag2a" in evidence["wcag"]
    assert evidence["help_url"].startswith("https://dequeuniversity.com/")

    contrast = next(f for f in stored if f.check_id == "color-contrast")
    assert contrast.severity is Severity.HIGH  # axe "serious"


def test_pages_come_from_the_store_and_only_allowed_css_is_fetched(
    env: AuditEnv, httpserver: HTTPServer
) -> None:
    serve(httpserver)
    env.crawl_then_clear(httpserver)
    report = env.audit()

    # The pages themselves were not downloaded again; the shared stylesheet once.
    assert requested_paths(httpserver) == ["/style.css"]
    assert {r.headers.get("User-Agent") for r, _ in httpserver.log} == {UA}
    # Blocked: the robots.txt-disallowed CSS (asked for twice: by the page, then by axe-core
    # itself over XHR), images (3), and the third-party script.
    assert report.blocked["robots_disallowed"] == 2
    assert report.blocked["resource_type"] >= 3
    assert report.blocked["other_host"] == 1


def test_page_choice_is_home_then_most_linked(env: AuditEnv, httpserver: HTTPServer) -> None:
    serve(httpserver)
    env.crawl_then_clear(httpserver)
    run = latest_finished_crawl_run(env.conn, env.site.id)
    assert run is not None and run.id is not None

    chosen = [r.url.path for r in pages_to_audit(env.conn, env.site, run.id, 10)]
    # /other.html is linked from 2 pages (home and bad), /bad.html from 1.
    assert chosen == ["/", "/other.html", "/bad.html"]
    assert [r.url.path for r in pages_to_audit(env.conn, env.site, run.id, 2)] == [
        "/",
        "/other.html",
    ]


def test_page_limit(env: AuditEnv, httpserver: HTTPServer) -> None:
    serve(httpserver)
    env.crawl_then_clear(httpserver)
    report = env.audit(pages=1)
    assert [p.url.path for p in report.pages] == ["/"]


def test_reaudit_replaces_accessibility_results_only(env: AuditEnv, httpserver: HTTPServer) -> None:
    serve(httpserver)
    env.crawl_then_clear(httpserver)
    agent_finding = AuditFinding(
        site_id=env.site.id,
        url=httpserver.url_for("/"),
        check_id="AR011",
        severity=Severity.INFO,
        message="No llms.txt",
        audited_at=datetime(2026, 10, 20, tzinfo=UTC),
    )
    add_audit_findings(env.conn, [agent_finding])

    env.audit()
    env.audit()  # again: results replaced, not duplicated

    stored = list_audit_findings(env.conn, env.site.id)
    assert sum(f.check_id == "AR011" for f in stored) == 1
    assert sum(f.kind is AuditKind.A11Y for f in stored) == len(EXPECTED_VIOLATIONS)
    assert len(list_audited_pages(env.conn, env.site.id, AuditKind.A11Y)) == 3


def test_missing_stored_copy_is_recorded_not_fatal(env: AuditEnv, httpserver: HTTPServer) -> None:
    serve(httpserver)
    env.crawl_then_clear(httpserver)
    for path in env.stored_files():
        if path.read_bytes().startswith(b"<!DOCTYPE html>\n<!--\n  Hand-made fixture with"):
            path.unlink()
    report = env.audit()
    bad = next(p for p in report.pages if p.url.path == "/bad.html")
    assert not bad.ok
    assert bad.error == "stored copy missing"
    assert sum(p.ok for p in report.pages) == 2


def test_audit_needs_a_finished_crawl(env: AuditEnv) -> None:
    with pytest.raises(AuditError, match="no finished crawl"):
        env.audit()


def test_audit_respects_the_crawl_window(env: AuditEnv, httpserver: HTTPServer) -> None:
    serve(httpserver)
    env.crawl_then_clear(httpserver)
    env.clock.current = env.clock.current.replace(hour=8)  # 13:30 IST
    with pytest.raises(AuditError, match="outside the crawl window"):
        env.audit()
    assert requested_paths(httpserver) == []


def test_missing_axe_build_is_a_clear_error(
    env: AuditEnv, httpserver: HTTPServer, tmp_path
) -> None:
    serve(httpserver)
    env.crawl_then_clear(httpserver)
    with pytest.raises(AuditError, match="axe-core not found"):
        env.audit(audit=audit_config(axe_path=tmp_path / "missing.js"))


def test_a_failed_browser_start_is_retried(
    env: AuditEnv, httpserver: HTTPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentready.a11y_auditor.audit as audit_module

    real = audit_module.async_playwright
    starts = []

    def flaky():
        starts.append(1)
        if len(starts) == 1:
            raise Exception("Connection closed while reading from the driver")
        return real()

    monkeypatch.setattr(audit_module, "async_playwright", flaky)
    serve(httpserver)
    env.crawl_then_clear(httpserver)
    report = env.audit(pages=1)
    assert len(starts) == 2
    assert report.pages[0].ok


def test_browser_that_never_starts_is_a_clear_error(
    env: AuditEnv, httpserver: HTTPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentready.a11y_auditor.audit as audit_module

    def broken():
        raise Exception("Connection closed while reading from the driver")

    monkeypatch.setattr(audit_module, "async_playwright", broken)
    serve(httpserver)
    env.crawl_then_clear(httpserver)
    with pytest.raises(AuditError, match="could not start Chromium after 3 attempts"):
        env.audit(pages=1)
