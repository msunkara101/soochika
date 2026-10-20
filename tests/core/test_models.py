from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from agentready.core import (
    AuditFinding,
    ChangeStatus,
    Chunk,
    Citation,
    DocType,
    Document,
    FetchedResource,
    Language,
    Severity,
    Site,
    SiteKind,
    SiteScore,
)

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
SHA = "a" * 64


def site(**overrides: object) -> Site:
    data: dict[str, object] = {
        "id": "tg-finance",
        "name": "Finance Department",
        "department": "Finance",
        "base_url": "https://finance.telangana.gov.in/",
        "kind": "department",
        "languages": ["te", "en"],
    }
    return Site.model_validate(data | overrides)


def document(**overrides: object) -> Document:
    data: dict[str, object] = {
        "site_id": "tg-finance",
        "resource_id": 1,
        "source_url": "https://finance.telangana.gov.in/go/123.pdf",
        "title": "G.O.Ms.No. 123",
        "department": "Finance",
        "doc_type": "go",
        "language": "en",
        "text": "Orders are issued...",
        "sha256": SHA,
        "first_seen": NOW,
        "last_checked": NOW,
    }
    return Document.model_validate(data | overrides)


# --- Site -----------------------------------------------------------------


def test_valid_site_parses_enums() -> None:
    s = site()
    assert s.kind is SiteKind.DEPARTMENT
    assert s.languages == [Language.TE, Language.EN]
    assert s.active is True


@pytest.mark.parametrize(
    "bad_url",
    [
        "not a url",
        "finance.telangana.gov.in",  # no scheme
        "ftp://finance.telangana.gov.in/",
        "javascript:alert(1)",
        "mailto:info@telangana.gov.in",
        "https://",
        "",
    ],
)
def test_site_rejects_bad_urls(bad_url: str) -> None:
    with pytest.raises(ValidationError, match="base_url"):
        site(base_url=bad_url)


@pytest.mark.parametrize("bad_id", ["TG-Finance", "tg finance", "-tg", "tg--finance", "tg_fin", ""])
def test_site_rejects_bad_ids(bad_id: str) -> None:
    with pytest.raises(ValidationError, match="id"):
        site(id=bad_id)


def test_site_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError, match="kind"):
        site(kind="ministry")


def test_site_rejects_unknown_language() -> None:
    with pytest.raises(ValidationError, match="languages"):
        site(languages=["te", "fr"])


def test_site_requires_a_language() -> None:
    with pytest.raises(ValidationError, match="languages"):
        site(languages=[])


def test_site_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="owner"):
        site(owner="someone")


# --- FetchedResource ------------------------------------------------------


def test_fetched_resource_records_an_error_without_a_status() -> None:
    r = FetchedResource(
        site_id="tg-finance",
        url="https://finance.telangana.gov.in/",
        fetched_at=NOW,
        error="certificate verify failed",
    )
    assert r.status_code is None
    assert r.error == "certificate verify failed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status_code", 99),
        ("status_code", 600),
        ("sha256", "abc"),
        ("sha256", "A" * 64),  # must be lowercase hex
        ("fetched_at", datetime(2026, 10, 19, 10, 0)),  # naive datetime
        ("final_url", "not a url"),
    ],
)
def test_fetched_resource_rejects_bad_values(field: str, value: object) -> None:
    data = {"site_id": "tg-finance", "url": "https://finance.telangana.gov.in/", "fetched_at": NOW}
    with pytest.raises(ValidationError, match=field):
        FetchedResource.model_validate(data | {field: value})


# --- Document and Chunk ---------------------------------------------------


def test_document_defaults_and_enums() -> None:
    d = document(published_on="2026-10-01")
    assert d.doc_type is DocType.GO
    assert d.change_status is ChangeStatus.NEW
    assert d.published_on == date(2026, 10, 1)
    assert d.ocr_used is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("doc_type", "memo"),
        ("language", "telugu"),
        ("change_status", "deleted"),
        ("ocr_confidence", 1.5),
        ("page_count", 0),
        ("source_url", "file:///C:/go.pdf"),
        ("title", ""),
    ],
)
def test_document_rejects_bad_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError, match=field):
        document(**{field: value})


def test_chunk_requires_text_and_non_negative_ordinal() -> None:
    assert Chunk(document_id=1, ordinal=0, text="body").heading is None
    with pytest.raises(ValidationError, match="ordinal"):
        Chunk(document_id=1, ordinal=-1, text="body")
    with pytest.raises(ValidationError, match="text"):
        Chunk(document_id=1, ordinal=0, text="   ")


# --- Citation, findings, scores -------------------------------------------


def test_citation_is_always_unofficial() -> None:
    c = Citation(
        source_url="https://finance.telangana.gov.in/go/123.pdf",
        department="Finance",
        title="G.O.Ms.No. 123",
        last_checked=NOW,
    )
    assert c.unofficial is True
    assert c.model_dump(mode="json")["unofficial"] is True
    with pytest.raises(ValidationError, match="unofficial"):
        Citation.model_validate(c.model_dump() | {"unofficial": False})


def test_audit_finding_enforces_severity() -> None:
    data = {
        "site_id": "tg-finance",
        "url": "https://finance.telangana.gov.in/",
        "check_id": "AR011",
        "severity": "info",
        "message": "No llms.txt",
        "audited_at": NOW,
    }
    assert AuditFinding.model_validate(data).severity is Severity.INFO
    with pytest.raises(ValidationError, match="severity"):
        AuditFinding.model_validate(data | {"severity": "urgent"})


@pytest.mark.parametrize("bad", [-1, 100.1])
def test_site_score_bounds(bad: float) -> None:
    with pytest.raises(ValidationError, match="overall"):
        SiteScore(
            site_id="tg-finance", accessibility=50, agent_readiness=50, overall=bad, computed_at=NOW
        )
