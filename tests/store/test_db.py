import sqlite3
from pathlib import Path

import pytest

from agentready.store import (
    Migration,
    MigrationError,
    applied_versions,
    connect,
    load_migrations,
    migrate,
)

EXPECTED_TABLES = {
    "sites",
    "crawl_runs",
    "fetched_resources",
    "documents",
    "chunks",
    "audit_findings",
    "site_scores",
    "links",
    "schema_migrations",
}


def tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row["name"] for row in rows}


def test_packaged_migrations_are_numbered_from_one() -> None:
    migrations = load_migrations()
    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))
    assert migrations[0].name == "initial"


def test_migrate_creates_schema_and_is_idempotent(tmp_path: Path) -> None:
    conn = connect(tmp_path / "nested" / "dir" / "agentready.db")
    assert migrate(conn) == [1, 2]
    assert tables(conn) >= EXPECTED_TABLES
    assert migrate(conn) == []
    assert applied_versions(conn) == [1, 2]
    conn.close()


def test_migrations_survive_reopening(tmp_path: Path) -> None:
    db = tmp_path / "agentready.db"
    conn = connect(db)
    migrate(conn)
    conn.close()
    conn = connect(db)
    assert migrate(conn) == []
    conn.close()


def test_connect_enables_foreign_keys(tmp_path: Path) -> None:
    conn = connect(tmp_path / "agentready.db")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_only_pending_migrations_are_applied() -> None:
    conn = connect(":memory:")
    first = [Migration(1, "one", "CREATE TABLE one (id INTEGER);")]
    both = [*first, Migration(2, "two", "CREATE TABLE two (id INTEGER);")]
    assert migrate(conn, first) == [1]
    assert migrate(conn, both) == [2]
    assert applied_versions(conn) == [1, 2]


def test_failed_migration_rolls_back() -> None:
    conn = connect(":memory:")
    broken = [
        Migration(1, "good", "CREATE TABLE good (id INTEGER);"),
        Migration(2, "bad", "CREATE TABLE half (id INTEGER); INSERT INTO missing VALUES (1);"),
    ]
    with pytest.raises(MigrationError, match="0002_bad"):
        migrate(conn, broken)
    assert applied_versions(conn) == [1]
    assert "half" not in tables(conn)
    assert "good" in tables(conn)


def test_load_migrations_reads_files_in_version_order(tmp_path: Path) -> None:
    (tmp_path / "0002_second.sql").write_text("SELECT 2;", encoding="utf-8")
    (tmp_path / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "README.md").write_text("ignored", encoding="utf-8")
    migrations = load_migrations(tmp_path)
    assert [(m.version, m.name) for m in migrations] == [(1, "first"), (2, "second")]


def test_load_migrations_rejects_duplicate_versions(tmp_path: Path) -> None:
    (tmp_path / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0001_again.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="duplicate migration version 0001"):
        load_migrations(tmp_path)
