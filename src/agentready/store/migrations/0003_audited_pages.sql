-- Every page an auditor looked at, so a clean page counts as "audited, no problems"
-- rather than being invisible. The scorecard (T09) needs this.

CREATE TABLE audited_pages (
    id                INTEGER PRIMARY KEY,
    site_id           TEXT NOT NULL REFERENCES sites (id),
    kind              TEXT NOT NULL,          -- a11y | agent
    url               TEXT NOT NULL,
    audited_at        TEXT NOT NULL,
    ok                INTEGER NOT NULL,
    error             TEXT,
    violations        INTEGER NOT NULL DEFAULT 0,
    requests_made     INTEGER NOT NULL DEFAULT 0,
    requests_blocked  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_audited_pages_site ON audited_pages (site_id, kind);
