"""Settings from environment variables, ``.env`` and the YAML files in ``config/``."""

from datetime import time
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from agentready.core.enums import AxeImpact

DEFAULT_CONFIG_DIR = Path("config")
CRAWL_FILE = "crawl.yaml"
SCORING_FILE = "scoring.yaml"
AUDIT_FILE = "audit.yaml"
SITES_FILE = "sites.yaml"
PROPOSED_SITES_FILE = "proposed_sites.yaml"

ArCheckId = Annotated[str, Field(pattern=r"^AR\d{3}$")]
Weight = Annotated[float, Field(ge=0)]


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CrawlWindow(_Config):
    """Local hours when crawling is allowed. ``start`` may be after ``end`` (overnight)."""

    start: time
    end: time
    timezone: str = "Asia/Kolkata"

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value!r}") from exc
        return value


class CrawlConfig(_Config):
    user_agent: str = Field(default="agentready-bot", pattern=r"^[A-Za-z0-9._-]+$")
    contact_url: HttpUrl
    min_delay_seconds: float = Field(default=5, ge=5)
    max_pages_per_site: int = Field(default=500, ge=1)
    max_file_mb: float = Field(default=50, gt=0)
    allowed_content_types: list[str] = Field(min_length=1)
    crawl_window: CrawlWindow
    # Crawler-trap limits for URLs (calendars, session ids, faceted search).
    max_url_length: int = Field(default=500, ge=50)
    max_query_params: int = Field(default=3, ge=0)
    max_path_depth: int = Field(default=12, ge=1)
    max_segment_repeats: int = Field(default=2, ge=1)
    """A path segment appearing more often than this (``/a/b/a/b/a/``) is a trap."""
    # Fetching.
    max_depth: int = Field(default=3, ge=0)
    """Link hops from the home page. Documents linked from the deepest pages are still fetched."""
    request_timeout_seconds: float = Field(default=30, gt=0)
    max_retries: int = Field(default=2, ge=0)
    """Extra attempts after a 5xx response or a timeout."""
    retry_backoff_seconds: float = Field(default=10, ge=0)
    """First retry waits this long (doubling each time), never less than the host delay."""
    max_redirects: int = Field(default=5, ge=0)

    @field_validator("allowed_content_types")
    @classmethod
    def _lowercase_types(cls, value: list[str]) -> list[str]:
        return [v.strip().lower() for v in value]

    def user_agent_header(self, version: str) -> str:
        """``agentready-bot/<version> (+<contact URL>)``."""
        return f"{self.user_agent}/{version} (+{self.contact_url})"


class AuditConfig(_Config):
    """How the accessibility auditor renders pages (config/audit.yaml)."""

    pages_per_site: int = Field(default=10, ge=1)
    """The home page plus the most-linked pages from the latest crawl."""
    page_timeout_seconds: float = Field(default=60, gt=0)
    settle_seconds: float = Field(default=1.0, ge=0)
    """Wait after the load event, for scripts that build the page."""
    max_requests_per_page: int = Field(default=30, ge=0)
    """Subresources fetched from the site's server per page (each one is rate-limited)."""
    resource_types: list[str] = Field(default=["stylesheet", "script", "xhr", "fetch"])
    """Playwright resource types worth loading. Images, fonts and media never are."""
    axe_path: Path = Path("vendor/axe.min.js")
    axe_tags: list[str] = Field(default=["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"], min_length=1)


class ScoringConfig(_Config):
    check_weights: dict[ArCheckId, Weight]
    axe_impact_weights: dict[AxeImpact, Weight]

    @field_validator("axe_impact_weights")
    @classmethod
    def _every_impact_weighted(cls, value: dict[AxeImpact, float]) -> dict[AxeImpact, float]:
        missing = sorted(set(AxeImpact) - set(value))
        if missing:
            raise ValueError(f"missing weights for axe impact levels: {', '.join(missing)}")
        return value


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"config file not found: {path}")
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    if content is None:
        return {}
    if not isinstance(content, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    return content


class Settings(BaseSettings):
    """Application settings.

    Paths and secrets come from ``AGENTREADY_*`` environment variables or ``.env``;
    crawl, scoring and audit rules come from ``crawl.yaml``, ``scoring.yaml`` and
    ``audit.yaml`` in ``config_dir``. Values passed to the constructor win over both.
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENTREADY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    config_dir: Path = DEFAULT_CONFIG_DIR
    data_dir: Path = Path("data")
    db_path: Path | None = None
    """Defaults to ``<data_dir>/agentready.db``."""
    tesseract_cmd: Path | None = Field(
        default=None, validation_alias=AliasChoices("TESSERACT_CMD", "tesseract_cmd")
    )
    crawl: CrawlConfig
    scoring: ScoringConfig
    audit: AuditConfig

    @field_validator("db_path", "tesseract_cmd", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: Any) -> Any:
        return None if value == "" else value

    @model_validator(mode="before")
    @classmethod
    def _load_yaml_files(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        config_dir = Path(data.get("config_dir") or DEFAULT_CONFIG_DIR)
        if not config_dir.is_dir():
            raise ValueError(f"config directory not found: {config_dir}")
        data = dict(data)
        data.setdefault("crawl", _read_yaml(config_dir / CRAWL_FILE))
        data.setdefault("scoring", _read_yaml(config_dir / SCORING_FILE))
        data.setdefault("audit", _read_yaml(config_dir / AUDIT_FILE))
        return data

    @model_validator(mode="after")
    def _default_db_path(self) -> "Settings":
        if self.db_path is None:
            object.__setattr__(self, "db_path", self.data_dir / "agentready.db")
        return self

    @property
    def sites_file(self) -> Path:
        """The human-reviewed site list."""
        return self.config_dir / SITES_FILE

    @property
    def proposed_sites_file(self) -> Path:
        """Where ``sites propose`` writes candidates for review. Never ``sites.yaml``."""
        return self.data_dir / PROPOSED_SITES_FILE
