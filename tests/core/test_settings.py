from datetime import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentready.core import AxeImpact, Settings

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"

CRAWL_YAML = """\
user_agent: agentready-bot
contact_url: https://example.org/bot
min_delay_seconds: 7
max_pages_per_site: 50
max_file_mb: 10
allowed_content_types: [text/html, application/pdf]
crawl_window: {start: "22:00", end: "06:00", timezone: Asia/Kolkata}
"""

SCORING_YAML = """\
check_weights: {AR001: 3, AR011: 0.5}
axe_impact_weights: {critical: 4, serious: 3, moderate: 2, minor: 1}
"""


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the developer's real environment and .env out of these tests."""
    for name in ("AGENTREADY_CONFIG_DIR", "AGENTREADY_DATA_DIR", "AGENTREADY_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    path = tmp_path / "cfg"
    path.mkdir()
    (path / "crawl.yaml").write_text(CRAWL_YAML, encoding="utf-8")
    (path / "scoring.yaml").write_text(SCORING_YAML, encoding="utf-8")
    return path


def test_loads_yaml_from_config_dir(config_dir: Path) -> None:
    s = Settings(config_dir=config_dir, _env_file=None)
    assert s.crawl.min_delay_seconds == 7
    assert s.crawl.max_pages_per_site == 50
    assert s.crawl.crawl_window.start == time(22, 0)
    assert s.crawl.user_agent_header("1.2.3") == "agentready-bot/1.2.3 (+https://example.org/bot)"
    assert s.scoring.check_weights == {"AR001": 3, "AR011": 0.5}
    assert s.scoring.axe_impact_weights[AxeImpact.CRITICAL] == 4


def test_defaults_for_paths(config_dir: Path) -> None:
    s = Settings(config_dir=config_dir, _env_file=None)
    assert s.data_dir == Path("data")
    assert s.db_path == Path("data") / "agentready.db"
    assert s.tesseract_cmd is None


def test_reads_environment_variables(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTREADY_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("AGENTREADY_DATA_DIR", "/srv/agentready")
    monkeypatch.setenv("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    s = Settings(_env_file=None)
    assert s.config_dir == config_dir
    assert s.db_path == Path("/srv/agentready") / "agentready.db"
    assert s.tesseract_cmd == Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def test_reads_dotenv_file(config_dir: Path, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"AGENTREADY_CONFIG_DIR={config_dir}\n"
        "AGENTREADY_DB_PATH=custom.db\n"
        "TESSERACT_CMD=\n"
        "EVAL_LLM_API_KEY=ignored\n",
        encoding="utf-8",
    )
    s = Settings(_env_file=env_file)
    assert s.config_dir == config_dir
    assert s.db_path == Path("custom.db")
    assert s.tesseract_cmd is None


def test_constructor_overrides_environment(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTREADY_DATA_DIR", "from-env")
    s = Settings(config_dir=config_dir, data_dir=Path("from-arg"), _env_file=None)
    assert s.data_dir == Path("from-arg")


def test_missing_config_dir(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="config directory not found"):
        Settings(config_dir=tmp_path / "nope", _env_file=None)


def test_missing_yaml_file(config_dir: Path) -> None:
    (config_dir / "scoring.yaml").unlink()
    with pytest.raises(ValidationError, match="config file not found"):
        Settings(config_dir=config_dir, _env_file=None)


def test_invalid_yaml(config_dir: Path) -> None:
    (config_dir / "crawl.yaml").write_text("user_agent: [unclosed", encoding="utf-8")
    with pytest.raises(ValidationError, match="invalid YAML"):
        Settings(config_dir=config_dir, _env_file=None)


@pytest.mark.parametrize(
    ("replace", "with_", "error"),
    [
        ("min_delay_seconds: 7", "min_delay_seconds: 1", "min_delay_seconds"),
        ("contact_url: https://example.org/bot", "contact_url: not-a-url", "contact_url"),
        ("timezone: Asia/Kolkata", "timezone: Mars/Olympus", "unknown timezone"),
        ("max_file_mb: 10", "max_file_mb: 10\nverify_tls: false", "verify_tls"),
    ],
)
def test_bad_crawl_values(config_dir: Path, replace: str, with_: str, error: str) -> None:
    (config_dir / "crawl.yaml").write_text(CRAWL_YAML.replace(replace, with_), encoding="utf-8")
    with pytest.raises(ValidationError, match=error):
        Settings(config_dir=config_dir, _env_file=None)


@pytest.mark.parametrize(
    ("replace", "with_", "error"),
    [
        ("AR011: 0.5", "AR11: 0.5", "check_weights"),
        ("AR011: 0.5", "AR011: -1", "check_weights"),
        ("minor: 1", "trivial: 1", "axe_impact_weights"),
        (", minor: 1", "", "missing weights for axe impact levels: minor"),
    ],
)
def test_bad_scoring_values(config_dir: Path, replace: str, with_: str, error: str) -> None:
    yaml_text = SCORING_YAML.replace(replace, with_)
    (config_dir / "scoring.yaml").write_text(yaml_text, encoding="utf-8")
    with pytest.raises(ValidationError, match=error):
        Settings(config_dir=config_dir, _env_file=None)


def test_repository_config_files_are_valid() -> None:
    s = Settings(config_dir=REPO_CONFIG, _env_file=None)
    assert s.crawl.min_delay_seconds >= 5
    assert set(s.scoring.check_weights) == {f"AR{n:03d}" for n in range(1, 12)}
