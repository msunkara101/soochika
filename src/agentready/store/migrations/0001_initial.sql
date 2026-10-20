-- Initial schema. Tables mirror the models in agentready.core.
-- Timestamps are ISO 8601 strings in UTC; dates are YYYY-MM-DD.
-- chunks_fts is added by the index task (T15), which owns the tokenizer choice.

CREATE TABLE sites (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    department  TEXT NOT NULL,
    base_url    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    languages   TEXT NOT NULL,          -- JSON array of language codes
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE crawl_runs (
    id             INTEGER PRIMARY KEY,
    site_id        TEXT NOT NULL REFERENCES sites (id),
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL,
    pages_fetched  INTEGER NOT NULL DEFAULT 0,
    error_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_crawl_runs_site ON crawl_runs (site_id, started_at);

CREATE TABLE fetched_resources (
    id             INTEGER PRIMARY KEY,
    site_id        TEXT NOT NULL REFERENCES sites (id),
    crawl_run_id   INTEGER REFERENCES crawl_runs (id),
    url            TEXT NOT NULL,
    final_url      TEXT,
    status_code    INTEGER,
    content_type   TEXT,
    sha256         TEXT,
    etag           TEXT,
    last_modified  TEXT,
    fetched_at     TEXT NOT NULL,
    raw_path       TEXT,
    error          TEXT
);
CREATE INDEX ix_fetched_resources_url ON fetched_resources (site_id, url, fetched_at);
CREATE INDEX ix_fetched_resources_run ON fetched_resources (crawl_run_id);

CREATE TABLE documents (
    id              INTEGER PRIMARY KEY,
    site_id         TEXT NOT NULL REFERENCES sites (id),
    resource_id     INTEGER NOT NULL REFERENCES fetched_resources (id),
    source_url      TEXT NOT NULL,
    title           TEXT NOT NULL,
    department      TEXT NOT NULL,
    doc_type        TEXT NOT NULL,
    language        TEXT NOT NULL,
    published_on    TEXT,
    go_number       TEXT,
    text            TEXT NOT NULL,
    page_count      INTEGER,
    ocr_used        INTEGER NOT NULL DEFAULT 0,
    ocr_confidence  REAL,
    sha256          TEXT NOT NULL,
    first_seen      TEXT NOT NULL,
    last_checked    TEXT NOT NULL,
    change_status   TEXT NOT NULL,
    superseded_by   INTEGER REFERENCES documents (id),
    UNIQUE (site_id, source_url)
);
CREATE INDEX ix_documents_go_number ON documents (go_number);

CREATE TABLE chunks (
    id           INTEGER PRIMARY KEY,
    document_id  INTEGER NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    ordinal      INTEGER NOT NULL,
    heading      TEXT,
    text         TEXT NOT NULL,
    UNIQUE (document_id, ordinal)
);

CREATE TABLE audit_findings (
    id          INTEGER PRIMARY KEY,
    site_id     TEXT NOT NULL REFERENCES sites (id),
    url         TEXT NOT NULL,
    check_id    TEXT NOT NULL,
    severity    TEXT NOT NULL,
    message     TEXT NOT NULL,
    evidence    TEXT,
    audited_at  TEXT NOT NULL
);
CREATE INDEX ix_audit_findings_site ON audit_findings (site_id, check_id);

CREATE TABLE site_scores (
    id               INTEGER PRIMARY KEY,
    site_id          TEXT NOT NULL REFERENCES sites (id),
    accessibility    REAL NOT NULL,
    agent_readiness  REAL NOT NULL,
    overall          REAL NOT NULL,
    computed_at      TEXT NOT NULL
);
CREATE INDEX ix_site_scores_site ON site_scores (site_id, computed_at);
