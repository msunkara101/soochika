# Agent-ready Telangana (`agentready`)

A pipeline that reads the **public** pages and documents of Telangana government
department and district websites and turns them into clean, cited, dated text for
AI agents, accessibility and agent-readiness scorecards for departments, and an
**unofficial** reader view for citizens.

- What to build next: [docs/PLAN.md](docs/PLAN.md)
- Modules, data model and dependency rules: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Local setup (virtual environment)

Requires [uv](https://docs.astral.sh/uv/). Dependencies are installed into a
project-local virtual environment at `.venv/`, never into the system Python.
uv downloads Python 3.12 automatically if it isn't installed.

```
uv sync                         # create .venv and install dependencies
uv run agentready version       # CLI
uv run agentready db init       # create the database
uv run agentready sites list    # validate config/sites.yaml
uv run agentready sites sync    # copy the site list into the database
uv run agentready crawl --site agri --max-pages 50   # polite crawl of one site
uv run agentready crawl --all                       # every active site
uv run pytest                   # tests
uv run ruff check . && uv run ruff format --check .
uv run lint-imports             # module dependency contracts
```

Copy `.env.example` to `.env` to override settings.

Before the first crawl, set `contact_url` in `config/crawl.yaml` to a real page about the bot;
`crawl` refuses to run with the placeholder. Crawls only run inside `crawl_window`
(22:00-06:00 IST by default); `--ignore-window` is for manual checks and keeps every
other rule (robots.txt, 5-second delay, page cap).

## Docker

The image keeps its virtual environment at `/opt/venv`, separate from the host `.venv`.

```
docker compose build
docker compose run --rm agentready version          # run the CLI
docker compose run --rm dev                         # run the tests
docker compose run --rm dev ruff check .
docker compose run --rm dev lint-imports
```

`./data` and `./config` are mounted into the container, so the database and raw
files persist on the host.

**Known issue (Docker Desktop on Windows):** under Docker Desktop's WSL2 VM, the Node.js
process that Playwright uses, and occasionally Chromium, crash at random (roughly 1 start
in 10; reproduced with nothing but "start Playwright, launch Chromium, close it", and seen
with both Node 22 and 24). The auditor retries a failed browser start and re-audits a page
whose browser crashed, so real audits complete. The test suite, though, can show 1-2 random
failures per Docker run; it passes consistently when run natively (`uv run pytest`).
