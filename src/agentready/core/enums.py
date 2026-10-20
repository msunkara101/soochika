"""Enumerations shared by every module."""

from enum import StrEnum


class SiteKind(StrEnum):
    DEPARTMENT = "department"
    DISTRICT = "district"
    OTHER = "other"


class Language(StrEnum):
    TE = "te"
    EN = "en"
    UR = "ur"
    HI = "hi"
    UNKNOWN = "unknown"


class DocType(StrEnum):
    GO = "go"
    CIRCULAR = "circular"
    NOTICE = "notice"
    NOTIFICATION = "notification"
    TENDER = "tender"
    SCHEME = "scheme"
    FORM = "form"
    CONTACT = "contact"
    PAGE = "page"
    OTHER = "other"


class ChangeStatus(StrEnum):
    NEW = "new"
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    REMOVED = "removed"
    SUPERSEDED = "superseded"


class CrawlStatus(StrEnum):
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"


class AuditKind(StrEnum):
    A11Y = "a11y"
    """WCAG findings from axe-core (check ids are axe rule ids)."""
    AGENT = "agent"
    """Agent-readiness checks AR001-AR011."""


class Severity(StrEnum):
    """Severity of an audit finding (agent-readiness checks and mapped axe impacts)."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AxeImpact(StrEnum):
    """Impact levels reported by axe-core for a violation."""

    MINOR = "minor"
    MODERATE = "moderate"
    SERIOUS = "serious"
    CRITICAL = "critical"
