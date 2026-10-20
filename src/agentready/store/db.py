"""SQLite connections and the migration runner."""

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

MIGRATIONS_DIR: Traversable = files("agentready.store") / "migrations"
_MIGRATION_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open the database, creating its parent directory. ``":memory:"`` works too."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(db_path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def load_migrations(directory: Traversable | Path = MIGRATIONS_DIR) -> list[Migration]:
    """Read ``NNNN_name.sql`` files, sorted by version. Other files are ignored."""
    migrations: dict[int, Migration] = {}
    for entry in directory.iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if not match:
            continue
        version = int(match.group(1))
        if version in migrations:
            raise MigrationError(f"duplicate migration version {version:04d}: {entry.name}")
        migrations[version] = Migration(version, match.group(2), entry.read_text(encoding="utf-8"))
    return [migrations[v] for v in sorted(migrations)]


def applied_versions(conn: sqlite3.Connection) -> list[int]:
    _ensure_migrations_table(conn)
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    return [row["version"] for row in rows]


def migrate(conn: sqlite3.Connection, migrations: list[Migration] | None = None) -> list[int]:
    """Apply pending migrations in order, each in its own transaction.

    Returns the versions applied by this call (empty when already up to date).
    """
    migrations = load_migrations() if migrations is None else migrations
    done = set(applied_versions(conn))
    applied: list[int] = []
    for migration in migrations:
        if migration.version in done:
            continue
        applied_at = datetime.now(UTC).isoformat()
        script = (
            "BEGIN;\n"
            f"{migration.sql}\n;\n"
            "INSERT INTO schema_migrations (version, name, applied_at) "
            f"VALUES ({migration.version}, '{migration.name}', '{applied_at}');\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except sqlite3.Error as exc:
            conn.rollback()
            raise MigrationError(
                f"migration {migration.version:04d}_{migration.name} failed: {exc}"
            ) from exc
        applied.append(migration.version)
    return applied


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    conn.commit()
