-- Links found while crawling (needed for most-linked pages, broken links and linked PDFs),
-- and why each crawl run stopped.

ALTER TABLE crawl_runs ADD COLUMN stop_reason TEXT;

CREATE TABLE links (
    id                INTEGER PRIMARY KEY,
    crawl_run_id      INTEGER NOT NULL REFERENCES crawl_runs (id),
    site_id           TEXT NOT NULL REFERENCES sites (id),
    from_resource_id  INTEGER NOT NULL REFERENCES fetched_resources (id),
    url               TEXT NOT NULL,
    text              TEXT,
    in_scope          INTEGER NOT NULL,
    is_document       INTEGER NOT NULL
);
CREATE INDEX ix_links_run ON links (crawl_run_id);
CREATE INDEX ix_links_url ON links (site_id, url);
