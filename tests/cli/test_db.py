from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentready.cli import app
from agentready.store import applied_versions, connect, load_migrations

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)  # keep a real .env out of the test
    monkeypatch.setenv("AGENTREADY_CONFIG_DIR", str(REPO_CONFIG))
    path = tmp_path / "data" / "agentready.db"
    monkeypatch.setenv("AGENTREADY_DB_PATH", str(path))
    return path


def test_db_init_creates_and_migrates(db_path: Path) -> None:
    runner = CliRunner()
    first = runner.invoke(app, ["db", "init"])
    assert first.exit_code == 0, first.output
    every_version = [m.version for m in load_migrations()]
    assert f"Applied migrations {', '.join(f'{v:04d}' for v in every_version)}." in first.stdout
    assert db_path.is_file()

    second = runner.invoke(app, ["db", "init"])
    assert second.exit_code == 0, second.output
    assert "already up to date" in second.stdout

    conn = connect(db_path)
    assert applied_versions(conn) == every_version
    conn.close()


def test_db_init_reports_bad_config(db_path: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTREADY_CONFIG_DIR", str(tmp_path / "missing"))
    result = CliRunner().invoke(app, ["db", "init"])
    assert result.exit_code == 2
    assert "config directory not found" in result.output
