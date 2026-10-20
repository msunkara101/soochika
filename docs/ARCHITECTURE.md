# Architecture

## Pipeline

```
Telangana sites ──► Collect ──────────► Extract ───────────► Store & track ──► Outputs
(dept + district)   site_directory      extract_html         store             query_api ─► mcp_server   (AI agents)
                    crawl_policy        extract_pdf          index                       ─► web_app      (citizens: reader view)
                    fetcher             ocr                  change_detector
                                        normalizer
                         │
                         └──► a11y_auditor + agent_auditor ──► scorecard ──► web_app (department reports)
                                                                         └─► fixkit (files departments upload)
```

The auditors only need fetched pages, not extracted text, so the scorecard ships before extraction is finished.

## Repository layout

```
payspace/
  CLAUDE.md
  README.md
  pyproject.toml
  .env.example
  config/
    sites.yaml        # the site list (reviewed by a human)
    crawl.yaml        # rate limits, page caps, user agent, contact URL
    scoring.yaml      # check weights for the scorecard
    audit.yaml        # how the accessibility auditor renders pages
  src/agentready/
    core/  store/  site_directory/  crawl_policy/  fetcher/
    a11y_auditor/  agent_auditor/  scorecard/
    extract_html/  extract_pdf/  ocr/  normalizer/  change_detector/
    index/  query_api/  mcp_server/  fixkit/  web_app/  cli/
  tests/
    fixtures/         # saved HTML and PDF samples, never live fetches
    <module>/
  eval/
    ocr/              # ground-truth OCR samples
    questions.yaml    # citizen questions for the agent evaluation
  vendor/axe.min.js   # pinned axe-core build
  data/               # gitignored: raw files, SQLite DB, reports
```

## Modules (one responsibility each)

| Layer | Module | Owns | Never does |
| --- | --- | --- | --- |
| 0 | `core` | Pydantic models, enums, settings loader | I/O of any kind |
| 1 | `store` | SQLite schema, migrations, repository functions | Business rules |
| 1 | `crawl_policy` | robots.txt rules, per-host rate limits, page caps (pure logic) | Network calls |
| 1 | `extract_html` | Main text, headings, links, tables, language from HTML bytes | Fetching, PDFs |
| 1 | `extract_pdf` | Text and tables from PDFs with a text layer; flags scanned pages | OCR |
| 1 | `ocr` | Text and confidence from page images (engine adapters: Tesseract first) | Deciding what to OCR |
| 2 | `site_directory` | Loading and validating `config/sites.yaml`; proposing new sites for review | Crawling |
| 2 | `fetcher` | Polite downloading, conditional GET, link discovery, raw file storage | Parsing content |
| 2 | `normalizer` | One `Document` record per page or file: title, department, type, dates, language, GO number | Storage |
| 2 | `change_detector` | New / changed / removed / superseded status across crawls | Indexing |
| 2 | `a11y_auditor` | WCAG 2.1 A/AA findings via Playwright + axe-core | Agent checks |
| 2 | `agent_auditor` | Agent-readiness findings (checks AR001–AR011) | Accessibility checks |
| 3 | `index` | Full-text search (SQLite FTS5) over document chunks | Answer formatting |
| 3 | `scorecard` | Scores and fix lists from audit findings; JSON, CSV and Markdown reports | Running audits |
| 3 | `fixkit` | Per-site `llms.txt`, `sitemap.xml`, JSON-LD, accessible HTML text versions | Auditing |
| 4 | `query_api` | Service functions + FastAPI routes; every result carries a citation | DB access except via `store` / `index` |
| 5 | `mcp_server` | MCP tools wrapping `query_api` service functions | DB access |
| 5 | `web_app` | Server-rendered leaderboard, department reports, reader view | Business logic |
| 6 | `cli` | Wiring commands together (`typer`) | Logic of its own |

**Dependency rule:** a module may import only from a lower layer, plus `core`. Same-layer imports are allowed only where listed:

- `agent_auditor` → `extract_pdf` (to check for a text layer)
- `normalizer` → `extract_html`, `extract_pdf`, `ocr`

Encode this as `import-linter` layer contracts in `pyproject.toml`.

## Core data model (`agentready.core`)

```python
class SiteKind(StrEnum): DEPARTMENT = "department"; DISTRICT = "district"; OTHER = "other"
class Language(StrEnum): TE = "te"; EN = "en"; UR = "ur"; HI = "hi"; UNKNOWN = "unknown"
class DocType(StrEnum):
    GO = "go"; CIRCULAR = "circular"; NOTICE = "notice"; NOTIFICATION = "notification"
    TENDER = "tender"; SCHEME = "scheme"; FORM = "form"; CONTACT = "contact"
    PAGE = "page"; OTHER = "other"
class ChangeStatus(StrEnum): NEW = "new"; UNCHANGED = "unchanged"; CHANGED = "changed"; REMOVED = "removed"; SUPERSEDED = "superseded"
class CrawlStatus(StrEnum): RUNNING = "running"; FINISHED = "finished"; FAILED = "failed"
class Severity(StrEnum): CRITICAL; HIGH; MEDIUM; LOW; INFO
class AxeImpact(StrEnum): MINOR; MODERATE; SERIOUS; CRITICAL
class AuditKind(StrEnum): A11Y = "a11y"; AGENT = "agent"

class Site:              # id (slug), name, department, base_url, kind, languages, active
class CrawlRun:          # id, site_id, started_at, finished_at, status, pages_fetched, error_count, stop_reason
class Link:              # id, crawl_run_id, site_id, from_resource_id, url, text, in_scope, is_document
class FetchedResource:   # id, site_id, crawl_run_id, url, final_url, status_code, content_type, sha256,
                         # etag, last_modified, fetched_at, raw_path, error
class Document:          # id, site_id, resource_id, source_url, title, department, doc_type,
                         # language, published_on, go_number, text, page_count,
                         # ocr_used, ocr_confidence, sha256, first_seen, last_checked,
                         # change_status, superseded_by
class Chunk:             # id, document_id, ordinal, heading, text
class Citation:          # source_url, department, title, published_on, last_checked, unofficial=True
class AuditFinding:      # site_id, url, check_id, severity, message, evidence, audited_at (kind derived from check_id)
class AuditedPage:       # site_id, kind, url, audited_at, ok, error, violations, requests_made, requests_blocked
class SiteScore:         # site_id, accessibility, agent_readiness, overall, computed_at
```

Every API and MCP response that returns content includes a `Citation`.

## Storage (v1: SQLite)

Tables mirror the models: `sites`, `fetched_resources`, `documents`, `chunks`, `chunks_fts` (FTS5, `unicode61` tokenizer; test Telugu tokenization early and switch to the `trigram` tokenizer if recall is poor), `audit_findings`, `site_scores`, `crawl_runs`, `links`, `audited_pages`. Migrations are numbered SQL files in `src/agentready/store/migrations/` (`0001_initial.sql`, ...); `chunks_fts` arrives with the index task (T15).

Raw files live on disk at `data/raw/<sha256[:2]>/<sha256>` so identical files are stored once.

## Agent-readiness checks (`agent_auditor`)

| ID | Check | Severity |
| --- | --- | --- |
| AR001 | `robots.txt` blocks common AI agents (GPTBot, ClaudeBot, PerplexityBot, Google-Extended, CCBot) | high |
| AR002 | No `sitemap.xml` (or not referenced in robots.txt) | medium |
| AR003 | Linked PDFs with no text layer (scanned) | high |
| AR004 | CAPTCHA on an information (non-form) page | high |
| AR005 | Main content needs JavaScript (raw HTML text far shorter than rendered text) | high |
| AR006 | Missing or wrong `lang` attribute | medium |
| AR007 | Text likely published as images (large content images with no alt text) | medium |
| AR008 | Broken internal links above 5% | medium |
| AR009 | Notice or circular listings without visible dates | medium |
| AR010 | HTTPS missing or TLS certificate errors | high |
| AR011 | No `llms.txt` | info |

## Configuration

- `config/crawl.yaml`: `user_agent`, `contact_url`, `min_delay_seconds: 5`, `max_pages_per_site: 500`, `max_file_mb: 50`, `allowed_content_types`, `crawl_window` (for example 22:00–06:00 IST), URL trap limits (`max_url_length`, `max_query_params`, `max_path_depth`, `max_segment_repeats`)
- `config/scoring.yaml`: weight per check ID and per axe impact level
- `config/audit.yaml`: pages per site, page timeout, per-page request cap, resource types the browser may load, axe-core path and WCAG tags
- `.env`: `AGENTREADY_DATA_DIR`, `AGENTREADY_DB_PATH`, `TESSERACT_CMD` (Windows path), LLM provider keys for the evaluation only
