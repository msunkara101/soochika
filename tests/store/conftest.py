import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentready.core import Document, FetchedResource, Site
from agentready.store import add_document, add_fetched_resource, connect, migrate, upsert_site

NOW = datetime(2026, 10, 20, 9, 30, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "test.db")
    migrate(connection)
    yield connection
    connection.close()


def make_site(site_id: str = "tg-finance", **overrides: object) -> Site:
    data: dict[str, object] = {
        "id": site_id,
        "name": "Finance Department",
        "department": "Finance",
        "base_url": "https://finance.telangana.gov.in/",
        "kind": "department",
        "languages": ["te", "en"],
    }
    return Site.model_validate(data | overrides)


def make_resource(site_id: str = "tg-finance", **overrides: object) -> FetchedResource:
    data: dict[str, object] = {
        "site_id": site_id,
        "url": "https://finance.telangana.gov.in/go/123.pdf",
        "status_code": 200,
        "content_type": "application/pdf",
        "sha256": SHA_A,
        "etag": '"abc"',
        "fetched_at": NOW,
        "raw_path": f"raw/aa/{SHA_A}",
    }
    return FetchedResource.model_validate(data | overrides)


def make_document(resource_id: int, site_id: str = "tg-finance", **overrides: object) -> Document:
    data: dict[str, object] = {
        "site_id": site_id,
        "resource_id": resource_id,
        "source_url": "https://finance.telangana.gov.in/go/123.pdf",
        "title": "G.O.Ms.No. 123",
        "department": "Finance",
        "doc_type": "go",
        "language": "en",
        "published_on": "2026-10-01",
        "go_number": "G.O.Ms.No. 123",
        "text": "Orders are issued...",
        "page_count": 2,
        "sha256": SHA_A,
        "first_seen": NOW,
        "last_checked": NOW,
    }
    return Document.model_validate(data | overrides)


@pytest.fixture
def site(conn: sqlite3.Connection) -> Site:
    return upsert_site(conn, make_site())


@pytest.fixture
def resource(conn: sqlite3.Connection, site: Site) -> FetchedResource:
    return add_fetched_resource(conn, make_resource(site.id))


@pytest.fixture
def document(conn: sqlite3.Connection, resource: FetchedResource) -> Document:
    assert resource.id is not None
    return add_document(conn, make_document(resource.id))
