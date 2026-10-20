import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentready.cli import app

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "config"
    shutil.copytree(ROOT / "config", config)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTREADY_CONFIG_DIR", str(config))
    monkeypatch.setenv("AGENTREADY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AGENTREADY_DB_PATH", raising=False)
    return tmp_path


def run(*args: str):
    return CliRunner().invoke(app, list(args))


def test_crawl_refuses_placeholder_contact_url(workdir: Path) -> None:
    result = run("crawl", "--site", "agri")
    assert result.exit_code == 2
    assert "placeholder" in result.output
    assert not (workdir / "data" / "raw").exists()


@pytest.mark.parametrize("args", [[], ["--site", "agri", "--all"]])
def test_crawl_needs_exactly_one_target(workdir: Path, args: list[str]) -> None:
    result = run("crawl", *args)
    assert result.exit_code == 2
    assert "exactly one of --site" in result.output


def test_crawl_rejects_unknown_site(workdir: Path) -> None:
    result = run("crawl", "--site", "nope")
    assert result.exit_code == 2
    assert "Unknown site id 'nope'" in result.output


def test_crawl_rejects_zero_max_pages(workdir: Path) -> None:
    result = run("crawl", "--site", "agri", "--max-pages", "0")
    assert result.exit_code == 2
