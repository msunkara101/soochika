"""Repository functions: core models in, core models out. One section per table.

Each write commits. Timestamps are stored as UTC ISO 8601 strings so they sort correctly.
"""

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from pydantic import AnyUrl, BaseModel

from agentready.core import (
    AuditedPage,
    AuditFinding,
    AuditKind,
    Chunk,
    CrawlRun,
    CrawlStatus,
    Document,
    FetchedResource,
    Link,
    Site,
    SiteScore,
)


class NotFoundError(LookupError):
    pass


# --- helpers ----------------------------------------------------------------


def _to_db(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, AnyUrl):
        return str(value)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, list):
        return json.dumps([_to_db(v) for v in value])
    return value


def _row_values(model: BaseModel, *, exclude: frozenset[str] = frozenset({"id"})) -> dict[str, Any]:
    return {
        name: _to_db(getattr(model, name))
        for name in type(model).model_fields
        if name not in exclude
    }


def _insert(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> int:
    columns = ", ".join(values)
    placeholders = ", ".join(f":{name}" for name in values)
    with conn:
        cursor = conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", values)
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def _update(
    conn: sqlite3.Connection, table: str, row_id: int | None, values: dict[str, Any]
) -> None:
    if row_id is None:
        raise ValueError(f"cannot update a {table} row without an id")
    assignments = ", ".join(f"{name} = :{name}" for name in values)
    with conn:
        cursor = conn.execute(
            f"UPDATE {table} SET {assignments} WHERE id = :id", {**values, "id": row_id}
        )
    if cursor.rowcount == 0:
        raise NotFoundError(f"{table} row {row_id} not found")


def _one[M: BaseModel](model: type[M], row: sqlite3.Row | None) -> M | None:
    return None if row is None else model.model_validate(dict(row))


def _all[M: BaseModel](model: type[M], rows: Iterable[sqlite3.Row]) -> list[M]:
    return [model.model_validate(dict(row)) for row in rows]


def _audit_finding(row: sqlite3.Row) -> AuditFinding:
    data = dict(row)
    data.pop("id")
    return AuditFinding.model_validate(data)


def _site_score(row: sqlite3.Row) -> SiteScore:
    data = dict(row)
    data.pop("id")
    return SiteScore.model_validate(data)


def _site(row: sqlite3.Row) -> Site:
    data = dict(row)
    data["languages"] = json.loads(data["languages"])
    return Site.model_validate(data)


# --- sites --------------------------------------------------------------------


def upsert_site(conn: sqlite3.Connection, site: Site) -> Site:
    values = _row_values(site, exclude=frozenset())
    updates = ", ".join(f"{name} = excluded.{name}" for name in values if name != "id")
    with conn:
        conn.execute(
            f"INSERT INTO sites ({', '.join(values)}) "
            f"VALUES ({', '.join(f':{name}' for name in values)}) "
            f"ON CONFLICT (id) DO UPDATE SET {updates}",
            values,
        )
    return site


def get_site(conn: sqlite3.Connection, site_id: str) -> Site | None:
    row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    return None if row is None else _site(row)


def list_sites(conn: sqlite3.Connection, *, active_only: bool = False) -> list[Site]:
    sql = "SELECT * FROM sites" + (" WHERE active = 1" if active_only else "") + " ORDER BY id"
    return [_site(row) for row in conn.execute(sql)]


# --- crawl_runs ---------------------------------------------------------------


def add_crawl_run(conn: sqlite3.Connection, run: CrawlRun) -> CrawlRun:
    return run.model_copy(update={"id": _insert(conn, "crawl_runs", _row_values(run))})


def update_crawl_run(conn: sqlite3.Connection, run: CrawlRun) -> CrawlRun:
    _update(conn, "crawl_runs", run.id, _row_values(run))
    return run


def get_crawl_run(conn: sqlite3.Connection, run_id: int) -> CrawlRun | None:
    row = conn.execute("SELECT * FROM crawl_runs WHERE id = ?", (run_id,)).fetchone()
    return _one(CrawlRun, row)


def latest_finished_crawl_run(conn: sqlite3.Connection, site_id: str) -> CrawlRun | None:
    row = conn.execute(
        "SELECT * FROM crawl_runs WHERE site_id = ? AND status = ? "
        "ORDER BY started_at DESC, id DESC LIMIT 1",
        (site_id, CrawlStatus.FINISHED.value),
    ).fetchone()
    return _one(CrawlRun, row)


def list_crawl_runs(conn: sqlite3.Connection, site_id: str) -> list[CrawlRun]:
    """Newest first."""
    rows = conn.execute(
        "SELECT * FROM crawl_runs WHERE site_id = ? ORDER BY started_at DESC, id DESC", (site_id,)
    )
    return _all(CrawlRun, rows)


# --- fetched_resources --------------------------------------------------------


def add_fetched_resource(conn: sqlite3.Connection, resource: FetchedResource) -> FetchedResource:
    new_id = _insert(conn, "fetched_resources", _row_values(resource))
    return resource.model_copy(update={"id": new_id})


def get_fetched_resource(conn: sqlite3.Connection, resource_id: int) -> FetchedResource | None:
    row = conn.execute("SELECT * FROM fetched_resources WHERE id = ?", (resource_id,)).fetchone()
    return _one(FetchedResource, row)


def latest_fetched_resource(
    conn: sqlite3.Connection, site_id: str, url: str
) -> FetchedResource | None:
    """Most recent fetch of ``url`` that stored a file: the source of ETag / Last-Modified."""
    row = conn.execute(
        "SELECT * FROM fetched_resources WHERE site_id = ? AND url = ? AND sha256 IS NOT NULL "
        "ORDER BY fetched_at DESC, id DESC LIMIT 1",
        (site_id, url),
    ).fetchone()
    return _one(FetchedResource, row)


def get_fetched_in_run(
    conn: sqlite3.Connection, crawl_run_id: int, url: str
) -> FetchedResource | None:
    """The row for ``url`` (as requested) in one crawl run, e.g. its robots.txt."""
    row = conn.execute(
        "SELECT * FROM fetched_resources WHERE crawl_run_id = ? AND url = ? ORDER BY id LIMIT 1",
        (crawl_run_id, url),
    ).fetchone()
    return _one(FetchedResource, row)


def most_linked_pages(
    conn: sqlite3.Connection, crawl_run_id: int, *, limit: int, exclude: Iterable[str] = ()
) -> list[tuple[FetchedResource, int]]:
    """HTML pages stored in a crawl run, most linked-to first (by number of linking pages).

    Only pages that were fetched and stored as HTML count; documents and errors don't.
    Ties are broken by URL so the choice is repeatable.
    """
    excluded = list(exclude)
    not_in = f" AND r.url NOT IN ({', '.join('?' * len(excluded))})" if excluded else ""
    rows = conn.execute(
        "SELECT r.*, count(DISTINCT l.from_resource_id) AS inbound "
        "FROM fetched_resources r JOIN links l "
        "  ON l.crawl_run_id = r.crawl_run_id AND l.url = r.url AND l.in_scope = 1 "
        "WHERE r.crawl_run_id = ? AND r.sha256 IS NOT NULL "
        "  AND (r.content_type LIKE 'text/html%' OR r.content_type LIKE 'application/xhtml+xml%')"
        f"{not_in} "
        "GROUP BY r.id ORDER BY inbound DESC, r.url LIMIT ?",
        [crawl_run_id, *excluded, limit],
    ).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        inbound = data.pop("inbound")
        result.append((FetchedResource.model_validate(data), inbound))
    return result


def list_fetched_resources(
    conn: sqlite3.Connection, *, site_id: str | None = None, crawl_run_id: int | None = None
) -> list[FetchedResource]:
    clauses, params = [], []
    if site_id is not None:
        clauses.append("site_id = ?")
        params.append(site_id)
    if crawl_run_id is not None:
        clauses.append("crawl_run_id = ?")
        params.append(crawl_run_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"SELECT * FROM fetched_resources{where} ORDER BY id", params)
    return _all(FetchedResource, rows)


# --- links -------------------------------------------------------------------


def add_links(conn: sqlite3.Connection, links: Iterable[Link]) -> int:
    rows = [_row_values(link) for link in links]
    if not rows:
        return 0
    with conn:
        conn.executemany(
            "INSERT INTO links "
            "(crawl_run_id, site_id, from_resource_id, url, text, in_scope, is_document) "
            "VALUES (:crawl_run_id, :site_id, :from_resource_id, :url, :text, :in_scope, "
            ":is_document)",
            rows,
        )
    return len(rows)


def list_links(
    conn: sqlite3.Connection,
    crawl_run_id: int,
    *,
    from_resource_id: int | None = None,
    documents_only: bool = False,
) -> list[Link]:
    sql, params = "SELECT * FROM links WHERE crawl_run_id = ?", [crawl_run_id]
    if from_resource_id is not None:
        sql += " AND from_resource_id = ?"
        params.append(from_resource_id)
    if documents_only:
        sql += " AND is_document = 1"
    return _all(Link, conn.execute(sql + " ORDER BY id", params))


# --- documents ----------------------------------------------------------------


def add_document(conn: sqlite3.Connection, document: Document) -> Document:
    return document.model_copy(update={"id": _insert(conn, "documents", _row_values(document))})


def update_document(conn: sqlite3.Connection, document: Document) -> Document:
    _update(conn, "documents", document.id, _row_values(document))
    return document


def get_document(conn: sqlite3.Connection, document_id: int) -> Document | None:
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    return _one(Document, row)


def get_document_by_url(conn: sqlite3.Connection, site_id: str, source_url: str) -> Document | None:
    row = conn.execute(
        "SELECT * FROM documents WHERE site_id = ? AND source_url = ?", (site_id, source_url)
    ).fetchone()
    return _one(Document, row)


def find_documents_by_go_number(conn: sqlite3.Connection, go_number: str) -> list[Document]:
    rows = conn.execute("SELECT * FROM documents WHERE go_number = ? ORDER BY id", (go_number,))
    return _all(Document, rows)


def list_documents(conn: sqlite3.Connection, *, site_id: str | None = None) -> list[Document]:
    if site_id is None:
        rows = conn.execute("SELECT * FROM documents ORDER BY id")
    else:
        rows = conn.execute("SELECT * FROM documents WHERE site_id = ? ORDER BY id", (site_id,))
    return _all(Document, rows)


# --- chunks -------------------------------------------------------------------


def replace_chunks(conn: sqlite3.Connection, document_id: int, chunks: list[Chunk]) -> list[Chunk]:
    """Delete a document's chunks and insert ``chunks`` in one transaction."""
    stored: list[Chunk] = []
    with conn:
        conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        for chunk in chunks:
            if chunk.document_id != document_id:
                raise ValueError(
                    f"chunk belongs to document {chunk.document_id}, not {document_id}"
                )
            values = _row_values(chunk)
            cursor = conn.execute(
                "INSERT INTO chunks (document_id, ordinal, heading, text) "
                "VALUES (:document_id, :ordinal, :heading, :text)",
                values,
            )
            stored.append(chunk.model_copy(update={"id": cursor.lastrowid}))
    return stored


def list_chunks(conn: sqlite3.Connection, document_id: int) -> list[Chunk]:
    rows = conn.execute(
        "SELECT * FROM chunks WHERE document_id = ? ORDER BY ordinal", (document_id,)
    )
    return _all(Chunk, rows)


# --- audit_findings -----------------------------------------------------------


def add_audit_findings(conn: sqlite3.Connection, findings: Iterable[AuditFinding]) -> int:
    rows = [_row_values(finding) for finding in findings]
    if not rows:
        return 0
    with conn:
        conn.executemany(
            "INSERT INTO audit_findings "
            "(site_id, url, check_id, severity, message, evidence, audited_at) "
            "VALUES (:site_id, :url, :check_id, :severity, :message, :evidence, :audited_at)",
            rows,
        )
    return len(rows)


def list_audit_findings(
    conn: sqlite3.Connection, site_id: str, *, check_ids: Iterable[str] | None = None
) -> list[AuditFinding]:
    sql, params = "SELECT * FROM audit_findings WHERE site_id = ?", [site_id]
    if check_ids is not None:
        ids = list(check_ids)
        sql += f" AND check_id IN ({', '.join('?' * len(ids))})"
        params += ids
    return [_audit_finding(row) for row in conn.execute(sql + " ORDER BY id", params)]


_AGENT_GLOB = "AR[0-9][0-9][0-9]"


def replace_audit_results(
    conn: sqlite3.Connection,
    site_id: str,
    kind: AuditKind,
    pages: Iterable[AuditedPage],
    findings: Iterable[AuditFinding],
) -> None:
    """Swap a site's previous results of one kind (a11y or agent) for new ones, atomically.

    Results of the other kind are left alone.
    """
    page_rows = [_row_values(page) for page in pages]
    finding_rows = [_row_values(finding) for finding in findings]
    for page in page_rows:
        if page["site_id"] != site_id or page["kind"] != kind.value:
            raise ValueError(f"audited page {page['url']} is not a {kind} result for {site_id}")
    agent = "" if kind is AuditKind.AGENT else "NOT "
    with conn:
        conn.execute(
            f"DELETE FROM audit_findings WHERE site_id = ? AND {agent}check_id GLOB ?",
            (site_id, _AGENT_GLOB),
        )
        conn.execute(
            "DELETE FROM audited_pages WHERE site_id = ? AND kind = ?", (site_id, kind.value)
        )
        for row in finding_rows:
            if AuditFinding.model_validate(row).kind is not kind or row["site_id"] != site_id:
                raise ValueError(f"finding {row['check_id']} is not a {kind} result for {site_id}")
        conn.executemany(
            "INSERT INTO audit_findings "
            "(site_id, url, check_id, severity, message, evidence, audited_at) "
            "VALUES (:site_id, :url, :check_id, :severity, :message, :evidence, :audited_at)",
            finding_rows,
        )
        conn.executemany(
            "INSERT INTO audited_pages (site_id, kind, url, audited_at, ok, error, violations, "
            "requests_made, requests_blocked) VALUES (:site_id, :kind, :url, :audited_at, :ok, "
            ":error, :violations, :requests_made, :requests_blocked)",
            page_rows,
        )


def list_audited_pages(
    conn: sqlite3.Connection, site_id: str, kind: AuditKind
) -> list[AuditedPage]:
    rows = conn.execute(
        "SELECT * FROM audited_pages WHERE site_id = ? AND kind = ? ORDER BY id",
        (site_id, kind.value),
    )
    return _all(AuditedPage, rows)


def delete_audit_findings(
    conn: sqlite3.Connection, site_id: str, *, check_ids: Iterable[str] | None = None
) -> int:
    """Delete a site's findings (optionally only some checks) before re-auditing."""
    sql, params = "DELETE FROM audit_findings WHERE site_id = ?", [site_id]
    if check_ids is not None:
        ids = list(check_ids)
        sql += f" AND check_id IN ({', '.join('?' * len(ids))})"
        params += ids
    with conn:
        return conn.execute(sql, params).rowcount


# --- site_scores --------------------------------------------------------------


def add_site_score(conn: sqlite3.Connection, score: SiteScore) -> SiteScore:
    _insert(conn, "site_scores", _row_values(score))
    return score


def latest_site_score(conn: sqlite3.Connection, site_id: str) -> SiteScore | None:
    row = conn.execute(
        "SELECT * FROM site_scores WHERE site_id = ? ORDER BY computed_at DESC, id DESC LIMIT 1",
        (site_id,),
    ).fetchone()
    return None if row is None else _site_score(row)


def list_latest_site_scores(conn: sqlite3.Connection) -> list[SiteScore]:
    """The newest score for every site, highest overall first."""
    rows = conn.execute(
        "SELECT * FROM site_scores AS s WHERE s.id = ("
        " SELECT id FROM site_scores WHERE site_id = s.site_id"
        " ORDER BY computed_at DESC, id DESC LIMIT 1)"
        " ORDER BY overall DESC, site_id"
    )
    return [_site_score(row) for row in rows]
