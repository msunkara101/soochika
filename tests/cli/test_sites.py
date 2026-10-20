from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentready.cli import app

from .config_copy import ROOT, copy_repo_config

FIXTURE = ROOT / "tests" / "fixtures" / "site_directory" / "state_web_directory.html"
PAGE_URL = "https://www.telangana.gov.in/state-web-directory/"


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the repo config and an empty data dir, so tests never touch the real ones."""
    config = tmp_path / "config"
    copy_repo_config(config)
    monkeypatch.chdir(tmp_path)  # keep a real .env out of the test
    monkeypatch.setenv("AGENTREADY_CONFIG_DIR", str(config))
    monkeypatch.setenv("AGENTREADY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AGENTREADY_DB_PATH", raising=False)
    return tmp_path


def run(*args: str):
    return CliRunner().invoke(app, list(args))


def test_sites_list(workdir: Path) -> None:
    result = run("sites", "list")
    assert result.exit_code == 0, result.output
    assert "agri" in result.stdout
    assert "https://agri.telangana.gov.in/" in result.stdout
    assert "5 site(s), 5 active." in result.stdout


def test_sites_list_reports_validation_errors(workdir: Path) -> None:
    sites = workdir / "config" / "sites.yaml"
    sites.write_text(
        sites.read_text(encoding="utf-8").replace("id: transport", "id: agri"), encoding="utf-8"
    )
    result = run("sites", "list")
    assert result.exit_code == 2
    assert "duplicate id 'agri'" in result.output


def test_sites_propose_writes_review_file_only(workdir: Path) -> None:
    sites_yaml = workdir / "config" / "sites.yaml"
    before = sites_yaml.read_bytes()
    result = run("sites", "propose", PAGE_URL, "--html", str(FIXTURE))
    assert result.exit_code == 0, result.output
    assert "Proposed 6 new site(s)" in result.stdout

    proposed = (workdir / "data" / "proposed_sites.yaml").read_text(encoding="utf-8")
    assert "id: ccla" in proposed
    assert "id: agri" not in proposed  # already in sites.yaml
    assert sites_yaml.read_bytes() == before


def test_sites_propose_without_html_needs_a_real_contact_url(workdir: Path) -> None:
    result = run("sites", "propose", PAGE_URL)
    assert result.exit_code == 2
    assert "contact_url" in result.output and "placeholder" in result.output


def test_sites_sync(workdir: Path) -> None:
    first = run("sites", "sync")
    assert first.exit_code == 0, first.output
    assert "added          5" in first.stdout
    second = run("sites", "sync")
    assert "unchanged      5" in second.stdout
