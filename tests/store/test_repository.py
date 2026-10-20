import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from agentready.core import (
    AuditFinding,
    ChangeStatus,
    Chunk,
    CrawlRun,
    CrawlStatus,
    Document,
    FetchedResource,
    Language,
    Severity,
    Site,
    SiteScore,
)
from agentready.store import (
    NotFoundError,
    add_audit_findings,
    add_crawl_run,
    add_document,
    add_fetched_resource,
    add_site_score,
    delete_audit_findings,
    find_documents_by_go_number,
    get_crawl_run,
    get_document,
    get_document_by_url,
    get_fetched_resource,
    get_site,
    latest_fetched_resource,
    latest_site_score,
    list_audit_findings,
    list_chunks,
    list_crawl_runs,
    list_documents,
    list_fetched_resources,
    list_latest_site_scores,
    list_sites,
    replace_chunks,
    update_crawl_run,
    update_document,
    upsert_site,
)

from .conftest import NOW, SHA_B, make_document, make_resource, make_site

IST = timezone(timedelta(hours=5, minutes=30))

# --- sites --------------------------------------------------------------------


def test_upsert_and_get_site(conn: sqlite3.Connection) -> None:
    site = make_site()
    upsert_site(conn, site)
    assert get_site(conn, "tg-finance") == site
    assert get_site(conn, "missing") is None


def test_upsert_site_updates_existing(conn: sqlite3.Connection, site: Site) -> None:
    changed = site.model_copy(update={"name": "Finance Dept.", "languages": [Language.EN]})
    upsert_site(conn, changed)
    assert get_site(conn, site.id) == changed
    assert len(list_sites(conn)) == 1


def test_list_sites_filters_inactive(conn: sqlite3.Connection) -> None:
    upsert_site(conn, make_site("tg-health"))
    upsert_site(conn, make_site("tg-finance", active=False))
    assert [s.id for s in list_sites(conn)] == ["tg-finance", "tg-health"]
    assert [s.id for s in list_sites(conn, active_only=True)] == ["tg-health"]


# --- crawl_runs ---------------------------------------------------------------


def test_crawl_run_lifecycle(conn: sqlite3.Connection, site: Site) -> None:
    run = add_crawl_run(conn, CrawlRun(site_id=site.id, started_at=NOW))
    assert run.id is not None
    assert get_crawl_run(conn, run.id) == run

    finished = run.model_copy(
        update={
            "finished_at": NOW + timedelta(minutes=10),
            "status": CrawlStatus.FINISHED,
            "pages_fetched": 42,
            "error_count": 3,
        }
    )
    update_crawl_run(conn, finished)
    assert get_crawl_run(conn, run.id) == finished
    assert get_crawl_run(conn, 999) is None


def test_list_crawl_runs_newest_first(conn: sqlite3.Connection, site: Site) -> None:
    older = add_crawl_run(conn, CrawlRun(site_id=site.id, started_at=NOW - timedelta(days=1)))
    newer = add_crawl_run(conn, CrawlRun(site_id=site.id, started_at=NOW))
    assert [r.id for r in list_crawl_runs(conn, site.id)] == [newer.id, older.id]


def test_update_without_id_or_unknown_id_fails(conn: sqlite3.Connection, site: Site) -> None:
    run = CrawlRun(site_id=site.id, started_at=NOW)
    with pytest.raises(ValueError, match="without an id"):
        update_crawl_run(conn, run)
    with pytest.raises(NotFoundError):
        update_crawl_run(conn, run.model_copy(update={"id": 999}))


# --- fetched_resources --------------------------------------------------------


def test_add_and_get_fetched_resource(conn: sqlite3.Connection, site: Site) -> None:
    run = add_crawl_run(conn, CrawlRun(site_id=site.id, started_at=NOW))
    resource = add_fetched_resource(conn, make_resource(site.id, crawl_run_id=run.id))
    assert resource.id is not None
    assert get_fetched_resource(conn, resource.id) == resource
    assert get_fetched_resource(conn, 999) is None


def test_fetched_resource_with_error_round_trips(conn: sqlite3.Connection, site: Site) -> None:
    failed = make_resource(
        site.id,
        status_code=None,
        content_type=None,
        sha256=None,
        etag=None,
        raw_path=None,
        error="SSL: CERTIFICATE_VERIFY_FAILED",
    )
    stored = add_fetched_resource(conn, failed)
    assert stored.id is not None
    assert get_fetched_resource(conn, stored.id) == stored


def test_timestamps_are_stored_in_utc(conn: sqlite3.Connection, site: Site) -> None:
    ist_time = datetime(2026, 10, 20, 15, 0, tzinfo=IST)
    stored = add_fetched_resource(conn, make_resource(site.id, fetched_at=ist_time))
    raw = conn.execute(
        "SELECT fetched_at FROM fetched_resources WHERE id = ?", (stored.id,)
    ).fetchone()[0]
    assert raw == "2026-10-20T09:30:00+00:00"
    loaded = get_fetched_resource(conn, stored.id or 0)
    assert loaded is not None
    assert loaded.fetched_at == ist_time
    assert loaded.fetched_at.tzinfo == UTC


def test_latest_fetched_resource_skips_failed_fetches(conn: sqlite3.Connection, site: Site) -> None:
    url = "https://finance.telangana.gov.in/go/123.pdf"
    add_fetched_resource(conn, make_resource(site.id, fetched_at=NOW - timedelta(days=2)))
    newest_ok = add_fetched_resource(
        conn, make_resource(site.id, sha256=SHA_B, fetched_at=NOW - timedelta(days=1))
    )
    add_fetched_resource(
        conn, make_resource(site.id, sha256=None, raw_path=None, error="timeout", fetched_at=NOW)
    )
    assert latest_fetched_resource(conn, site.id, url) == newest_ok
    assert latest_fetched_resource(conn, site.id, "https://finance.telangana.gov.in/x") is None


def test_list_fetched_resources_by_site_and_run(conn: sqlite3.Connection, site: Site) -> None:
    upsert_site(conn, make_site("tg-health"))
    run = add_crawl_run(conn, CrawlRun(site_id=site.id, started_at=NOW))
    in_run = add_fetched_resource(conn, make_resource(site.id, crawl_run_id=run.id))
    outside = add_fetched_resource(conn, make_resource(site.id))
    other = add_fetched_resource(conn, make_resource("tg-health"))
    assert list_fetched_resources(conn, crawl_run_id=run.id) == [in_run]
    assert list_fetched_resources(conn, site_id=site.id) == [in_run, outside]
    assert list_fetched_resources(conn) == [in_run, outside, other]


def test_fetched_resource_requires_known_site(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        add_fetched_resource(conn, make_resource("unknown-site"))


# --- documents ----------------------------------------------------------------


def test_add_and_get_document(conn: sqlite3.Connection, document: Document) -> None:
    assert document.id is not None
    loaded = get_document(conn, document.id)
    assert loaded == document
    assert loaded.published_on == date(2026, 10, 1)
    assert get_document(conn, 999) is None


def test_get_document_by_url(conn: sqlite3.Connection, document: Document) -> None:
    assert get_document_by_url(conn, document.site_id, str(document.source_url)) == document
    assert get_document_by_url(conn, document.site_id, "https://other/") is None


def test_one_document_per_url(conn: sqlite3.Connection, document: Document) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        add_document(conn, make_document(document.resource_id))


def test_update_document_marks_supersession(
    conn: sqlite3.Connection, document: Document, resource: FetchedResource
) -> None:
    assert resource.id is not None
    newer = add_document(
        conn,
        make_document(
            resource.id,
            source_url="https://finance.telangana.gov.in/go/200.pdf",
            go_number="G.O.Ms.No. 200",
        ),
    )
    superseded = document.model_copy(
        update={"change_status": ChangeStatus.SUPERSEDED, "superseded_by": newer.id}
    )
    update_document(conn, superseded)
    assert get_document(conn, document.id or 0) == superseded


def test_find_and_list_documents(
    conn: sqlite3.Connection, document: Document, resource: FetchedResource
) -> None:
    assert resource.id is not None
    upsert_site(conn, make_site("tg-health"))
    health_resource = add_fetched_resource(conn, make_resource("tg-health"))
    assert health_resource.id is not None
    other = add_document(
        conn,
        make_document(
            health_resource.id,
            site_id="tg-health",
            source_url="https://health.telangana.gov.in/c/1.pdf",
            go_number=None,
            doc_type="circular",
        ),
    )
    assert find_documents_by_go_number(conn, "G.O.Ms.No. 123") == [document]
    assert list_documents(conn) == [document, other]
    assert list_documents(conn, site_id="tg-health") == [other]


# --- chunks -------------------------------------------------------------------


def test_replace_and_list_chunks(conn: sqlite3.Connection, document: Document) -> None:
    doc_id = document.id
    assert doc_id is not None
    first = replace_chunks(
        conn,
        doc_id,
        [
            Chunk(document_id=doc_id, ordinal=1, heading="Orders", text="Second"),
            Chunk(document_id=doc_id, ordinal=0, heading=None, text="First"),
        ],
    )
    assert all(c.id is not None for c in first)
    assert [c.text for c in list_chunks(conn, doc_id)] == ["First", "Second"]

    replace_chunks(conn, doc_id, [Chunk(document_id=doc_id, ordinal=0, text="Only")])
    assert [c.text for c in list_chunks(conn, doc_id)] == ["Only"]


def test_replace_chunks_rejects_other_documents_chunks(
    conn: sqlite3.Connection, document: Document
) -> None:
    doc_id = document.id
    assert doc_id is not None
    replace_chunks(conn, doc_id, [Chunk(document_id=doc_id, ordinal=0, text="Kept")])
    with pytest.raises(ValueError, match="belongs to document"):
        replace_chunks(conn, doc_id, [Chunk(document_id=doc_id + 1, ordinal=0, text="x")])
    # The failed replace rolled back, so the old chunk is still there.
    assert [c.text for c in list_chunks(conn, doc_id)] == ["Kept"]


# --- audit_findings -----------------------------------------------------------


def finding(check_id: str, site_id: str = "tg-finance") -> AuditFinding:
    return AuditFinding(
        site_id=site_id,
        url="https://finance.telangana.gov.in/",
        check_id=check_id,
        severity=Severity.HIGH,
        message=f"{check_id} failed",
        evidence="<img src=notice.jpg>",
        audited_at=NOW,
    )


def test_add_list_and_delete_audit_findings(conn: sqlite3.Connection, site: Site) -> None:
    upsert_site(conn, make_site("tg-health"))
    findings = [finding("AR001"), finding("AR011"), finding("color-contrast")]
    assert add_audit_findings(conn, [*findings, finding("AR001", "tg-health")]) == 4
    assert add_audit_findings(conn, []) == 0

    assert list_audit_findings(conn, site.id) == findings
    assert list_audit_findings(conn, site.id, check_ids=["AR011"]) == [findings[1]]

    assert delete_audit_findings(conn, site.id, check_ids=["AR001", "AR011"]) == 2
    assert list_audit_findings(conn, site.id) == [findings[2]]
    assert delete_audit_findings(conn, site.id) == 1
    assert list_audit_findings(conn, site.id) == []
    assert len(list_audit_findings(conn, "tg-health")) == 1


# --- site_scores --------------------------------------------------------------


def score(site_id: str, overall: float, at: datetime) -> SiteScore:
    return SiteScore(
        site_id=site_id,
        accessibility=overall,
        agent_readiness=overall,
        overall=overall,
        computed_at=at,
    )


def test_site_scores_latest(conn: sqlite3.Connection, site: Site) -> None:
    upsert_site(conn, make_site("tg-health"))
    add_site_score(conn, score(site.id, 40, NOW - timedelta(days=7)))
    latest = add_site_score(conn, score(site.id, 55, NOW))
    health = add_site_score(conn, score("tg-health", 80, NOW))

    assert latest_site_score(conn, site.id) == latest
    assert latest_site_score(conn, "tg-unknown") is None
    assert list_latest_site_scores(conn) == [health, latest]


# --- links --------------------------------------------------------------------


def test_add_and_list_links(conn: sqlite3.Connection, site: Site) -> None:
    from agentready.core import Link
    from agentready.store import add_links, list_links

    run = add_crawl_run(conn, CrawlRun(site_id=site.id, started_at=NOW))
    page = add_fetched_resource(conn, make_resource(site.id, crawl_run_id=run.id))
    other = add_fetched_resource(conn, make_resource(site.id, crawl_run_id=run.id))
    assert run.id is not None and page.id is not None and other.id is not None

    def link(url: str, from_id: int, *, doc: bool = False) -> Link:
        return Link(
            crawl_run_id=run.id or 0,
            site_id=site.id,
            from_resource_id=from_id,
            url=url,
            text="GO",
            in_scope=True,
            is_document=doc,
        )

    links = [
        link("https://finance.telangana.gov.in/a.html", page.id),
        link("https://finance.telangana.gov.in/go.pdf", page.id, doc=True),
        link("https://finance.telangana.gov.in/b.html", other.id),
    ]
    assert add_links(conn, links) == 3
    assert add_links(conn, []) == 0
    assert [str(x.url) for x in list_links(conn, run.id, from_resource_id=page.id)] == [
        "https://finance.telangana.gov.in/a.html",
        "https://finance.telangana.gov.in/go.pdf",
    ]
    assert [str(x.url) for x in list_links(conn, run.id, documents_only=True)] == [
        "https://finance.telangana.gov.in/go.pdf"
    ]


def test_crawl_run_stop_reason_round_trips(conn: sqlite3.Connection, site: Site) -> None:
    run = add_crawl_run(conn, CrawlRun(site_id=site.id, started_at=NOW))
    done = update_crawl_run(conn, run.model_copy(update={"stop_reason": "page_cap_reached"}))
    assert get_crawl_run(conn, run.id or 0) == done
