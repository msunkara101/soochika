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

**Docker Desktop on Windows:** the images are based on Playwright's official image
(`mcr.microsoft.com/playwright/python`, Ubuntu 24.04), whose tag must match the playwright
version in `uv.lock` (`PLAYWRIGHT_VERSION` in the `Dockerfile`). On the earlier
`python:3.12-slim` base, Playwright's Node.js driver, Chromium and even Python itself
segfaulted at random under Docker Desktop's WSL2 VM (about 1 browser start in 10), which
made 1-2 tests fail per run. On the Playwright base it is about 1 start in 200, the
auditor retries those, and the suite passes. If random crashes come back, check the host
before the code: a custom WSL kernel (`kernel=` in `%UserProfile%\.wslconfig`; remove the
line and run `wsl --shutdown` to go back to Microsoft's kernel) and Intel 13th/14th-gen
CPU instability (update the BIOS for microcode 0x12B or later) both cause exactly this.
