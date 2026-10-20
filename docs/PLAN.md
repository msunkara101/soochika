# Implementation Plan

Build in 8 phases and 22 tasks. Each task is sized for one or two Claude Code sessions and ends with something you can run.

**How to use this file with Claude Code:** start each session with

> Read CLAUDE.md and docs/PLAN.md. Implement the first unchecked task. Stop when its "Done when" list is met, tick it, and add a line to the progress log.

Every task's "Done when" also includes: `uv run pytest` passes, `uv run ruff check .` is clean, and `uv run lint-imports` passes.

---

## Phase 0: Project foundation

- [x] **T01: Scaffold the repository**
  - Create the `uv` project (`pyproject.toml`, Python 3.12), the `src/agentready/` package with an empty subpackage per module in `docs/ARCHITECTURE.md`, `tests/`, `config/`, `eval/`, `vendor/`, `data/` (gitignored), `.gitignore`, `.env.example`, and a short `README.md`.
  - Add dev tools: `pytest`, `ruff`, `import-linter`. Write the layer contracts from ARCHITECTURE.md into `pyproject.toml`.
  - Add a `typer` CLI entry point `agentready` with a `version` command.
  - **Done when:** `uv run agentready version` prints the version, and `uv run lint-imports` passes with the contracts in place.

- [x] **T02: Core models and settings (`core`)**
  - Implement the enums and Pydantic models from ARCHITECTURE.md, plus a `Settings` class (`pydantic-settings`) that reads `.env` and the YAML files in `config/`.
  - **Done when:** tests cover model validation (bad URLs rejected, enums enforced) and settings loading from a temporary config directory.

- [x] **T03: Storage (`store`)**
  - SQLite schema with numbered SQL migration files and a tiny migration runner; repository functions for every table, including content-addressed raw file storage (`data/raw/<sha[:2]>/<sha>`).
  - CLI: `agentready db init`.
  - **Done when:** tests run every repository function against a temporary database and confirm an identical file is stored only once.

## Phase 1: Collect

- [x] **T04: Site list (`site_directory`)**
  - Load and validate `config/sites.yaml` (id, name, department, base_url, kind, languages). Seed it with 5 Telangana department sites to start; you'll add the rest by hand.
  - A `propose` function that parses a directory page (such as the state portal's department list) and writes candidate sites to `data/proposed_sites.yaml` **for human review**. It never edits `sites.yaml` itself.
  - CLI: `agentready sites list`, `agentready sites propose <url> --html <saved page>`, `agentready sites sync` (copies `sites.yaml` into the `sites` table; sites removed from the file become inactive).
  - *Changed 2026-10-20:* `propose` reads a page saved from a browser, because polite fetching (robots.txt, rate limit, user agent) belongs to `fetcher` (T06), and `site_directory` may not import it (same layer). T06 makes `--html` optional.
  - **Done when:** duplicate IDs and malformed URLs fail validation with clear errors; `propose` is tested on a saved HTML fixture.

- [x] **T05: Crawl policy (`crawl_policy`)**
  - Pure functions: robots.txt rules for our user agent, the minimum delay per host, page and file-size caps, allowed content types, the crawl-window check, and in-scope URLs (same host as the site, ignoring `www.`; no `mailto:` or `javascript:`, no fragments, no query-string explosions).
  - *Changed 2026-10-20:* "same registrable domain" became "same host". `gov.in` is a public suffix, so every state site shares the registrable domain `telangana.gov.in`, and a crawl of one site would wander into all of them.
  - **Done when:** table-driven tests cover each rule, including a robots.txt that blocks us, a crawl outside the window, and an oversized file.

- [ ] **T06: Polite fetcher (`fetcher`)**
  - Async `httpx` client that asks `crawl_policy` before every request, sends conditional headers, follows redirects (recording `final_url`), retries with backoff on 5xx and timeouts, stores raw bytes through `store`, and records a `FetchedResource` row (including TLS and HTTP errors).
  - Breadth-first link discovery with a depth limit. Collect links to PDFs and other documents.
  - CLI: `agentready crawl --site <id> [--max-pages N]` and `agentready crawl --all`.
  - The crawl runs `sites sync` first (fetched rows reference the `sites` table).
  - Make `--html` optional on `agentready sites propose <url>`: without it, the CLI fetches the page through `fetcher` (one polite request) and passes the bytes to `site_directory.propose_sites`.
  - **Done when:** tests use a local test server (`pytest-httpserver` or similar) to prove the delay is respected, robots.txt is obeyed, an unchanged file returns 304 and isn't re-stored, and errors are recorded without crashing the run.
  - *Added 2026-10-20:* before the manual check, publish the bot contact page (`bot-page/`, static, hosted on Vercel) and set `contact_url` in `config/crawl.yaml` to it. The crawl refuses to run with the placeholder.
  - **Then do a manual check:** crawl the 5 seed sites with `--max-pages 50` and note anything surprising in the progress log.

## Phase 2: Scorecard (first pitch asset)

- [ ] **T07: Accessibility auditor (`a11y_auditor`)**
  - Use Playwright (Chromium) to load each site's top N pages (home page plus most-linked pages, N=10 by default), inject the vendored `vendor/axe.min.js`, run it with the `wcag2a`, `wcag2aa`, `wcag21a` and `wcag21aa` tags, and save one `AuditFinding` per violation per page.
  - CLI: `agentready audit a11y --site <id>`.
  - **Done when:** tests run against two local fixture pages (one clean, one with known violations) and get the expected findings.

- [ ] **T08: Agent-readiness auditor (`agent_auditor`)**
  - Implement checks AR001–AR011 from ARCHITECTURE.md, one function per check, each returning findings. Use already-fetched data from `store` where possible; only AR005 needs a browser render.
  - CLI: `agentready audit agent --site <id>`.
  - **Done when:** every check has a passing and a failing fixture test.

- [ ] **T09: Scorecard (`scorecard`)**
  - Turn findings into 0–100 scores (accessibility, agent readiness, overall) using weights from `config/scoring.yaml`. Produce a ranked fix list per site, a state-wide summary (for example, "share of PDFs with no text layer"), and exports: JSON, CSV and a Markdown report.
  - CLI: `agentready report [--site <id>] --format md|json|csv`.
  - **Done when:** scores are deterministic for the same findings, and the Markdown report on the 5 seed sites reads like something you'd show an official.
  - **Gate:** run crawl + audits + report across every site in `sites.yaml` before moving on.

## Phase 3: Extract

- [ ] **T10: HTML extraction (`extract_html`)**
  - From raw HTML: main text (`trafilatura`), title, heading outline, links, tables as rows, and a language guess (the `lang` attribute first, then Unicode script ratio: Telugu U+0C00–U+0C7F, Urdu via Arabic script, otherwise English).
  - **Done when:** tests on 5 saved Telangana pages (Telugu, English, mixed) produce the expected title, headings and language.

- [ ] **T11: PDF extraction (`extract_pdf`)**
  - Per-page text and tables (`pdfplumber`), page count, and a `needs_ocr` flag per page (characters below a threshold, or text that isn't readable Unicode, such as legacy Telugu fonts).
  - Expose `has_text_layer(pdf_bytes) -> bool` for `agent_auditor`.
  - **Done when:** tests on a text PDF, a scanned PDF and a legacy-font PDF set `needs_ocr` correctly.

- [ ] **T12: OCR (`ocr`)**
  - An `OcrEngine` protocol and a Tesseract adapter: render pages with `pypdfium2` at 300 DPI and run `tel+eng` or `urd+eng` depending on a script hint. Return text plus mean confidence per page.
  - A benchmark command that compares OCR output against ground truth in `eval/ocr/` using character error rate (CER).
  - CLI: `agentready ocr bench`.
  - **Done when:** the adapter is tested on one fixture page, and the benchmark runs on whatever samples are in `eval/ocr/`. Add 20–50 real scanned GO pages with hand-typed text there; record the CER in the progress log.

- [ ] **T13: Normalizer (`normalizer`)**
  - Build one `Document` per HTML page or PDF: choose the extractor, call OCR only for pages flagged `needs_ocr`, then classify `doc_type` with rules (for example "G.O.Ms.No", "G.O.Rt.No", "Circular", "Tender", "Notification", plus Telugu equivalents), extract `go_number`, and parse `published_on` from Indian date formats (dd-mm-yyyy, dd.mm.yyyy, dd/mm/yyyy, "19th October 2026", Telugu month names).
  - Split text into `Chunk`s by heading or page.
  - CLI: `agentready extract --site <id>`.
  - **Done when:** tests on fixtures cover each doc type, GO number formats and date formats; a hand-checked sample of 50 real documents has 95%+ correct title, date and department (record the result in the progress log).

- [ ] **T14: Change detection (`change_detector`)**
  - Compare each crawl with the last one by URL and hash: new, unchanged, changed or removed. Detect supersession from phrases like "in supersession of G.O.Ms.No. X" and link `superseded_by`.
  - **Done when:** tests simulate three crawls and get the right status for every document, including a superseded GO.

## Phase 4: Index and API

- [ ] **T15: Search index (`index`)**
  - Populate the FTS5 table from chunks; BM25 ranking; filters by site, department, doc type, language and date range; superseded documents ranked down and flagged.
  - Test Telugu queries early. If the `unicode61` tokenizer gives poor recall, switch to `trigram` and record why in the progress log.
  - **Done when:** tests with a small Telugu + English corpus return expected top results for 10 fixed queries.

- [ ] **T16: Query API (`query_api`)**
  - Service functions (plain Python, used by both the API and the MCP server): `search`, `get_document`, `list_departments`, `latest_notices`.
  - FastAPI routes: `GET /v1/search`, `GET /v1/documents/{id}`, `GET /v1/departments`, `GET /v1/departments/{id}/notices/latest`. Every result includes a `Citation` and the `unofficial: true` flag.
  - CLI: `agentready serve api`.
  - **Done when:** API tests cover every route, and the OpenAPI page loads.

## Phase 5: Agent interfaces and fix kit

- [ ] **T17: MCP server (`mcp_server`)**
  - Use the official `mcp` Python SDK. Tools: `search_documents`, `get_document`, `list_departments`, `latest_notices`. Each one calls `query_api` service functions and returns the citation with the text.
  - Tool descriptions tell agents to cite the source URL and date, and to warn when a document is superseded.
  - CLI: `agentready serve mcp` (stdio), plus an HTTP transport option.
  - **Done when:** tests call each tool in-process, and a manual check with the MCP Inspector works. Add setup instructions to `README.md`.

- [ ] **T18: Fix kit (`fixkit`)**
  - For each site, generate in `data/fixkit/<site_id>/`: `llms.txt` (department summary and key links), `sitemap.xml`, `notices.jsonld` (schema.org `GovernmentService` / `DigitalDocument` entries with dates), accessible HTML text versions of scanned PDFs, and a `README.txt` telling a webmaster where to upload each file. Zip it all.
  - CLI: `agentready fixkit --site <id>`.
  - **Done when:** the generated XML and JSON-LD validate, the HTML passes axe with zero violations, and the tests pass.

## Phase 6: Evaluation (the headline number)

- [ ] **T19: Agent evaluation harness (`eval/`)**
  - `eval/questions.yaml` holds citizen questions you write yourself (target 100): question, language, expected answer, expected source URL.
  - A runner that asks an LLM each question twice, once with ordinary web search and once with our MCP tools. It records the answers, then grades each for correct, cited and current (LLM-as-judge with a fixed rubric, plus a manual review of a 20-question sample).
  - The provider is configurable through `.env`. Keep this code outside `src/agentready/`, because it isn't part of the product.
  - **Done when:** the runner works end to end on 10 questions and writes a Markdown before/after report.

## Phase 7: Web app and operations

- [ ] **T20: Web app (`web_app`)**
  - Server-rendered FastAPI + Jinja2 pages: a state leaderboard, a per-site report (scores, fix list, fix-kit download), a document reader view with a persistent **Unofficial: always check the original** banner and a link to the source, and search. UI strings in Telugu, English and Urdu (right-to-left layout for Urdu).
  - The app must meet the standard it audits: run `a11y_auditor` against it in CI.
  - CLI: `agentready serve web`.
  - **Done when:** every page has zero axe violations and works without JavaScript.

- [ ] **T21: Daily run**
  - `agentready run-daily`: crawl (inside the crawl window), extract, detect changes, re-index, re-audit weekly, and regenerate reports. Write structured logs to `data/logs/`.
  - Document setting it up with Windows Task Scheduler in `README.md`.
  - **Done when:** two consecutive runs on the seed sites finish, and the second only re-processes changed files.

- [ ] **T22: Hardening pass**
  - Load-test the API (target p95 under 300 ms for search on the full corpus), add API rate limiting, review error handling across modules, and write `docs/OPERATIONS.md` (backups, re-crawl, adding a site, handling takedown requests).
  - **Done when:** the targets are met, and `OPERATIONS.md` exists.

---

## Parking lot (not in scope until the plan is updated)

- Postgres + pgvector instead of SQLite, and semantic search with embeddings
- Bhashini or vision-model OCR adapters
- Department contact extraction (`get_department_contacts` tool)
- Bulk feed and SLA for AI companies
- Citizen services and transactions (need department partnerships and backend access)

## Progress log

<!-- One line per finished task: YYYY-MM-DD · Txx · what was done · anything notable -->
2026-10-19 · T01 · uv project (Python 3.12, project-local .venv), 19 empty module packages, `agentready version` CLI, import-linter layer contract (exhaustive, same-layer modules independent) · also added Dockerfile (runtime + dev targets, venv at /opt/venv) and docker-compose.yml at the user's request
2026-10-19 · T02 · core enums + Pydantic models (strict: extra fields forbidden, aware datetimes, http/https URLs only, slug site ids, Citation.unofficial locked to true) and Settings reading AGENTREADY_* env/.env plus config/crawl.yaml and config/scoring.yaml · added Severity and AxeImpact enums (needed by AuditFinding and scoring weights); min_delay_seconds can't be set below 5; contact_url in crawl.yaml is a placeholder to replace before T06
2026-10-20 · T03 · SQLite schema (0001_initial.sql) + migration runner (one transaction per file, rollback on failure), repository functions for every table, content-addressed RawStore (atomic write, identical bytes stored once), `agentready db init` · added CrawlRun model, CrawlStatus enum and FetchedResource.crawl_run_id to core (needed for crawl_runs and T14); one Document per (site, source_url); chunks_fts deferred to T15 so the tokenizer decision lives there; verified in Docker
2026-10-20 · T04 · sites.yaml loader that reports every problem at once with the site and field (duplicate ids, duplicate hosts, bad URLs, unknown enums), 5 seed sites (it, transport, agri, schooledu, pred; checked against the State Web Directory), propose_sites + write_proposals (output is in sites.yaml format so accepted entries can be copied over), sites list/propose/sync · added sync_sites and Settings.sites_file / proposed_sites_file (needed after T03: the sites table must be filled before crawling); propose reads a saved page until T06 adds fetching (plan updated); the propose fixture is hand-made, modelled on the portal page, not a live capture
2026-10-20 · T05 · crawl_policy as pure functions: RFC 9309 robots.txt parser (wildcards, longest match, our group over *, merged groups, Crawl-delay, sitemaps, 4xx = allow all, 5xx/unreachable = disallow all), host-only scope with URL normalising (fragments, default ports, session ids, sorted query) and trap limits, delay = max(5 s, Crawl-delay), page cap (--max-pages can only lower it), file-size cap, content types with an extension fallback for application/octet-stream, overnight crawl window in IST, and should_fetch combining them · added URL trap limits to CrawlConfig and crawl.yaml; scope rule changed from registrable domain to host (plan updated)
2026-10-20 · T06 (automated part) · polite fetcher: per-host delay (max of 5 s and Crawl-delay) checked with a fake clock, robots.txt fetched and stored per run, conditional GET from the last stored copy (304 reuses the file, links still followed), redirects checked hop by hop against scope and robots, retries with doubling backoff and Retry-After on 5xx/timeouts, streaming size cap, TLS/connect errors recorded as tls_error/connect_error, BFS with depth limit (documents one hop deeper), `crawl --site/--all [--max-pages] [--ignore-window]`, `sites propose <url>` fetches when --html is omitted · added links table + Link model and crawl_runs.stop_reason (migration 0002, needed by T07/T08), fetch settings in crawl.yaml, percent-encoding in normalize_url; crawl refuses to run while contact_url is the example.org placeholder · manual crawl of the 5 seed sites NOT done yet: needs a real contact_url first
2026-10-20 · T06 prerequisite · static bot contact page in bot-page/ (what the bot does, its rules, robots.txt examples, contact and removal) with vercel.json and deploy notes · contact email added; waiting on: Vercel deploy, contact_url in crawl.yaml
