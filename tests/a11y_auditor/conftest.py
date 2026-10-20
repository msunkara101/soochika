import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_httpserver import HTTPServer

from agentready.a11y_auditor import A11yReport, audit_site
from agentready.core import AuditConfig, CrawlConfig
from agentready.fetcher import crawl_site

from ..fetcher.conftest import UA, Env, FakeClock, make_config

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "a11y"
CLEAN = (FIXTURES / "clean.html").read_bytes()
VIOLATIONS = (FIXTURES / "violations.html").read_bytes()
STYLE = b"body { font-family: sans-serif; color: #1a1a1a; background: #ffffff; }"
OTHER = (
    b'<!DOCTYPE html><html lang="en"><head><title>Contact</title>'
    b'<link rel="stylesheet" href="/style.css"></head>'
    b'<body><main><h1>Contact</h1><p><a href="/">Home</a></p></main></body></html>'
)


def audit_config(**overrides: object) -> AuditConfig:
    data: dict[str, object] = {"axe_path": ROOT / "vendor" / "axe.min.js", "settle_seconds": 0}
    return AuditConfig.model_validate(data | overrides)


def serve(httpserver: HTTPServer) -> None:
    httpserver.expect_request("/robots.txt").respond_with_data(
        "User-agent: *\nDisallow: /private/\n", content_type="text/plain"
    )
    httpserver.expect_request("/").respond_with_data(CLEAN, content_type="text/html; charset=utf-8")
    httpserver.expect_request("/bad.html").respond_with_data(VIOLATIONS, content_type="text/html")
    httpserver.expect_request("/other.html").respond_with_data(OTHER, content_type="text/html")
    httpserver.expect_request("/style.css").respond_with_data(STYLE, content_type="text/css")
    httpserver.expect_request("/private/hidden.css").respond_with_data(
        "body{}", content_type="text/css"
    )
    httpserver.expect_request("/logo.png").respond_with_data(b"\x89PNG", content_type="image/png")


class AuditEnv(Env):
    def crawl_then_clear(self, httpserver: HTTPServer, config: CrawlConfig | None = None) -> None:
        """Crawl the test site (as the real pipeline would), then forget those requests."""
        asyncio.run(
            crawl_site(
                self.conn,
                self.raw,
                self.site,
                config or make_config(),
                user_agent=UA,
                clock=self.clock,
            )
        )
        httpserver.clear_log()

    def audit(
        self,
        crawl: CrawlConfig | None = None,
        audit: AuditConfig | None = None,
        clock: FakeClock | None = None,
        **kwargs: object,
    ) -> A11yReport:
        return asyncio.run(
            audit_site(
                self.conn,
                self.raw,
                self.site,
                crawl or make_config(),
                audit or audit_config(),
                user_agent=UA,
                clock=clock or self.clock,
                **kwargs,  # type: ignore[arg-type]
            )
        )


@pytest.fixture
def env(tmp_path: Path, httpserver: HTTPServer) -> Iterator[AuditEnv]:
    environment = AuditEnv(tmp_path, httpserver.url_for("/"))
    yield environment
    environment.conn.close()
