"""Typed records passed between modules. Validation only; no I/O."""

from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl

from agentready.core.enums import (
    ChangeStatus,
    CrawlStatus,
    DocType,
    Language,
    Severity,
    SiteKind,
)

SiteId = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)]
"""Lowercase slug, for example ``tg-finance`` or ``hyderabad-district``."""

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonEmptyStr = Annotated[str, Field(min_length=1)]
CheckId = Annotated[str, Field(min_length=1, max_length=100)]
Score = Annotated[float, Field(ge=0, le=100)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Site(_Model):
    id: SiteId
    name: NonEmptyStr
    department: NonEmptyStr
    base_url: HttpUrl
    kind: SiteKind
    languages: list[Language] = Field(min_length=1)
    active: bool = True


class CrawlRun(_Model):
    id: int | None = None
    site_id: SiteId
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    status: CrawlStatus = CrawlStatus.RUNNING
    pages_fetched: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    stop_reason: str | None = None
    """Why the run ended: ``completed``, ``page_cap_reached``, ``outside_crawl_window``..."""


class FetchedResource(_Model):
    id: int | None = None
    site_id: SiteId
    crawl_run_id: int | None = None
    url: HttpUrl
    final_url: HttpUrl | None = None
    status_code: int | None = Field(default=None, ge=100, le=599)
    content_type: str | None = None
    sha256: Sha256 | None = None
    etag: str | None = None
    last_modified: str | None = None
    """Raw ``Last-Modified`` header value, sent back unchanged in conditional requests."""
    fetched_at: AwareDatetime
    raw_path: str | None = None
    error: str | None = None


class Link(_Model):
    """A link found on a fetched HTML page during a crawl run."""

    id: int | None = None
    crawl_run_id: int
    site_id: SiteId
    from_resource_id: int
    url: HttpUrl
    text: str | None = None
    in_scope: bool
    """Same site and not a crawler trap: the crawler may follow it."""
    is_document: bool
    """Points at a PDF, Word or Excel file (by extension)."""


class Document(_Model):
    id: int | None = None
    site_id: SiteId
    resource_id: int
    source_url: HttpUrl
    title: NonEmptyStr
    department: NonEmptyStr
    doc_type: DocType
    language: Language
    published_on: date | None = None
    go_number: str | None = None
    text: str
    page_count: int | None = Field(default=None, ge=1)
    ocr_used: bool = False
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)
    """Mean OCR confidence across OCR'd pages, from 0 to 1."""
    sha256: Sha256
    first_seen: AwareDatetime
    last_checked: AwareDatetime
    change_status: ChangeStatus = ChangeStatus.NEW
    superseded_by: int | None = None
    """Id of the document that supersedes this one."""


class Chunk(_Model):
    id: int | None = None
    document_id: int
    ordinal: int = Field(ge=0)
    heading: str | None = None
    text: NonEmptyStr


class Citation(_Model):
    source_url: HttpUrl
    department: NonEmptyStr
    title: NonEmptyStr
    published_on: date | None = None
    last_checked: AwareDatetime
    unofficial: Literal[True] = True


class AuditFinding(_Model):
    site_id: SiteId
    url: HttpUrl
    check_id: CheckId
    """``AR001``-``AR011`` for agent-readiness checks, or the axe rule id."""
    severity: Severity
    message: NonEmptyStr
    evidence: str | None = None
    audited_at: AwareDatetime


class SiteScore(_Model):
    site_id: SiteId
    accessibility: Score
    agent_readiness: Score
    overall: Score
    computed_at: AwareDatetime
