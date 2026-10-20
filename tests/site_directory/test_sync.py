import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentready.core import Site
from agentready.site_directory import sync_sites
from agentready.store import connect, get_site, list_sites, migrate


def make(site_id: str, **overrides: object) -> Site:
    data: dict[str, object] = {
        "id": site_id,
        "name": f"{site_id} department",
        "department": site_id,
        "base_url": f"https://{site_id}.telangana.gov.in/",
        "kind": "department",
        "languages": ["en", "te"],
    }
    return Site.model_validate(data | overrides)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "test.db")
    migrate(connection)
    yield connection
    connection.close()


def test_first_sync_adds_everything(conn: sqlite3.Connection) -> None:
    result = sync_sites(conn, [make("agri"), make("it")])
    assert result.added == ["agri", "it"]
    assert [s.id for s in list_sites(conn)] == ["agri", "it"]


def test_resync_reports_updates_and_unchanged(conn: sqlite3.Connection) -> None:
    sync_sites(conn, [make("agri"), make("it")])
    result = sync_sites(conn, [make("agri", name="Agriculture"), make("it")])
    assert result.updated == ["agri"]
    assert result.unchanged == ["it"]
    assert result.added == []
    site = get_site(conn, "agri")
    assert site is not None
    assert site.name == "Agriculture"


def test_removed_sites_are_deactivated_not_deleted(conn: sqlite3.Connection) -> None:
    sync_sites(conn, [make("agri"), make("it")])
    result = sync_sites(conn, [make("agri")])
    assert result.deactivated == ["it"]
    it = get_site(conn, "it")
    assert it is not None
    assert it.active is False
    # Already inactive: not reported again.
    assert sync_sites(conn, [make("agri")]).deactivated == []
    # Re-listing the site reactivates it.
    assert sync_sites(conn, [make("agri"), make("it")]).updated == ["it"]
