# syntax=docker/dockerfile:1
# Playwright's official image (Ubuntu 24.04, Python 3.12, Chromium and its system libraries).
# Keep the tag in step with the playwright version in uv.lock. On Docker Desktop's WSL2 VM,
# Playwright's Node.js driver crashed ~1 start in 10 on python:3.12-slim and ~1 in 200 on this.
ARG PLAYWRIGHT_VERSION=1.64.0
FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble AS base

COPY --from=ghcr.io/astral-sh/uv:0.6.6 /uv /uvx /usr/local/bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Runtime image: product dependencies only
FROM base AS runtime
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
# Chromium for the accessibility auditor (plus the system libraries it needs)
RUN playwright install --with-deps chromium
COPY src ./src
COPY config ./config
COPY vendor ./vendor
RUN uv sync --frozen --no-dev
ENTRYPOINT ["agentready"]
CMD ["--help"]

# Dev image: adds pytest, ruff and import-linter
FROM base AS dev
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project
RUN playwright install --with-deps chromium
COPY . .
RUN uv sync --frozen
CMD ["pytest"]
