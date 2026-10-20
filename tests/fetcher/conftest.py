import asyncio
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pytest_httpserver import HTTPServer

from agentready.core import CrawlConfig, FetchedResource, Site
from agentready.fetcher import CrawlReport, crawl_site
from agentready.store import RawStore, connect, list_fetched_resources, migrate, upsert_site

UA = "agentready-bot/0.1.0 (+https://agentready.test/bot)"
START = datetime(2026, 10, 20, 17, 0, tzinfo=UTC)  # 22:30 IST, inside the default window


class FakeClock:
    """Time moves only when the code under test sleeps, so delays are exact and instant."""

    def __init__(self, start: datetime = START) -> None:
        self.current = start
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self.sleeps.append(seconds)
            self.current += timedelta(seconds=seconds)


def make_config(**overrides: object) -> CrawlConfig:
    data: dict[str, object] = {
        "contact_url": "https://agentready.test/bot",
        "allowed_content_types": ["text/html", "application/xhtml+xml", "application/pdf"],
        "crawl_window": {"start": "22:00", "end": "06:00", "timezone": "Asia/Kolkata"},
        "max_retries": 2,
        "retry_backoff_seconds": 10,
        "request_timeout_seconds": 5,
    }
    return CrawlConfig.model_validate(data | overrides)


def html(*links: str, title: str = "page") -> str:
    anchors = "".join(f'<a href="{href}">{href}</a>' for href in links)
    return f"<html><head><title>{title}</title></head><body>{anchors}</body></html>"


class Env:
    """A database, raw store, site and fake clock for one test."""

    def __init__(self, tmp_path: Path, base_url: str) -> None:
        self.conn = connect(tmp_path / "test.db")
        migrate(self.conn)
        self.raw = RawStore(tmp_path / "data")
        self.clock = FakeClock()
        self.site = upsert_site(
            self.conn,
            Site(
                id="local",
                name="Local test site",
                department="Testing",
                base_url=base_url,
                kind="department",
                languages=["en"],
            ),
        )

    def crawl(self, config: CrawlConfig | None = None, **kwargs: object) -> CrawlReport:
        return asyncio.run(
            crawl_site(
                self.conn,
                self.raw,
                self.site,
                config or make_config(),
                user_agent=UA,
                clock=self.clock,
                **kwargs,  # type: ignore[arg-type]
            )
        )

    def resources(self, crawl_run_id: int | None = None) -> list[FetchedResource]:
        return list_fetched_resources(self.conn, site_id=self.site.id, crawl_run_id=crawl_run_id)

    def by_path(self, crawl_run_id: int | None = None) -> dict[str, FetchedResource]:
        return {r.url.path or "/": r for r in self.resources(crawl_run_id)}

    def stored_files(self) -> list[Path]:
        root = self.raw.data_dir / "raw"
        return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


@pytest.fixture
def env(tmp_path: Path, httpserver: HTTPServer) -> Iterator[Env]:
    environment = Env(tmp_path, httpserver.url_for("/"))
    yield environment
    environment.conn.close()


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "test.db")
    migrate(connection)
    yield connection
    connection.close()


def requested_paths(httpserver: HTTPServer) -> list[str]:
    return [request.path for request, _ in httpserver.log]
