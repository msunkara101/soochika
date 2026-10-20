# Agent-ready Telangana (working name: `agentready`)

## What this project is

A pipeline that reads the **public** pages and documents of Telangana government department and district websites, turns them into clean, cited, dated text, and serves it to:

1. **AI agents**: an MCP server and REST API where every answer carries the official source URL and date.
2. **Departments**: an accessibility (GIGW 3.0 / WCAG 2.1 AA) and agent-readiness scorecard per site, plus a generated "fix kit" (`llms.txt`, `sitemap.xml`, structured data, text versions of scanned PDFs).
3. **Citizens**: an accessible reader view in Telugu, English and Urdu, clearly labelled **unofficial**.

We have **no backend access** to any government site. Everything starts from what a normal visitor can fetch.

- Plan and task list: `docs/PLAN.md` (the source of truth for what to build next)
- Module map, data model and dependency rules: `docs/ARCHITECTURE.md`

## How to work on this repo

- Work through `docs/PLAN.md` **one task at a time, in order**. Don't start a task until the one before it meets its "Done when" criteria.
- At the start of a session, read `docs/PLAN.md` and find the first unchecked task.
- When a task is finished: tick its checkbox in `docs/PLAN.md`, add one dated line to the "Progress log" at the bottom of that file, and commit.
- If a task turns out to be wrong or too big, propose a change to `docs/PLAN.md` before coding around it.
- Never add features that aren't in the current task. Note ideas under "Parking lot" in `docs/PLAN.md` instead.

## Engineering rules

- **Single responsibility per module.** Each package under `src/agentready/` owns exactly one job (see the table in `docs/ARCHITECTURE.md`). If a change makes a module do a second job, create a new module instead.
- **Dependency direction is enforced** with `import-linter` (contracts in `pyproject.toml`). Lower layers never import higher layers. Fix the design, not the contract.
- Modules talk through the typed models in `agentready.core` and through each module's public `__init__.py` API. No reaching into another module's internals.
- Pure logic (parsing, scoring, policy) stays free of I/O so it's easy to test. I/O lives in `fetcher`, `store` and the CLI or app edges.
- Every module has tests in `tests/<module>/`. Use recorded fixtures (saved HTML/PDF files in `tests/fixtures/`) and never hit live government sites in tests.
- Configuration goes in `config/*.yaml` and environment variables (`.env`, see `.env.example`), never hard-coded.

## Crawling rules (non-negotiable)

Government servers are fragile. The crawler must:

- obey `robots.txt` for our user agent
- default to **1 request per 5 seconds per host**, with a cap on pages per site per run
- identify itself: `agentready-bot/<version> (+<contact URL from config>)`
- use conditional requests (ETag / Last-Modified) and skip unchanged files
- never submit forms, log in, solve CAPTCHAs or bypass access controls
- never disable TLS verification. Record certificate errors as audit findings instead.

## Stack

- Python 3.12, managed with `uv`
- `httpx` (fetching), `trafilatura` + `selectolax` (HTML), `pdfplumber` + `pypdfium2` (PDF), `pytesseract` + Tesseract with `tel`, `urd`, `eng` language data (OCR)
- SQLite with FTS5 for storage and search in v1, behind the `store` and `index` interfaces so Postgres can replace it later
- Playwright for Python + vendored `axe-core` (accessibility audits)
- FastAPI (API and server-rendered web app with Jinja2), official `mcp` Python SDK (MCP server)
- `typer` (CLI), `pydantic` v2 (models), `pydantic-settings` (config)
- `pytest`, `ruff` (lint + format), `import-linter`

The development machine runs **Windows**. Prefer cross-platform commands (`uv run ...`), use `pathlib` for paths, and don't rely on `make` or bash-only scripts.

## Common commands

```
uv sync                         # install dependencies
uv run pytest                   # all tests
uv run ruff check . && uv run ruff format --check .
uv run lint-imports             # module dependency contracts
uv run agentready --help        # CLI
```

Every task's "Done when" includes: tests pass, ruff is clean and `lint-imports` passes.
