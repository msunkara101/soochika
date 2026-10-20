"""Command-line entry point (`agentready`). Wires commands together; no logic of its own."""

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from agentready import __version__
from agentready.a11y_auditor import AuditError, audit_site
from agentready.core import CrawlConfig, Settings, Site
from agentready.fetcher import FetchError, crawl_site, fetch_page, without_crawl_window
from agentready.site_directory import (
    DEFAULT_DOMAIN_SUFFIXES,
    SiteListError,
    load_sites,
    propose_sites,
    sync_sites,
    write_proposals,
)
from agentready.store import RawStore, applied_versions, connect, migrate

app = typer.Typer(help="Agent-ready Telangana pipeline.", no_args_is_help=True)
db_app = typer.Typer(help="Database commands.", no_args_is_help=True)
sites_app = typer.Typer(help="The site list (config/sites.yaml).", no_args_is_help=True)
app.add_typer(db_app, name="db")
audit_app = typer.Typer(help="Audit sites from the latest crawl.", no_args_is_help=True)
app.add_typer(sites_app, name="sites")
app.add_typer(audit_app, name="audit")


def _settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        typer.echo(f"Invalid configuration:\n{exc}", err=True)
        raise typer.Exit(code=2) from exc


def _sites(settings: Settings) -> list[Site]:
    try:
        return load_sites(settings.sites_file)
    except SiteListError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc


PLACEHOLDER_CONTACT_HOSTS = frozenset({"example.org", "example.com", "example.net"})

IgnoreWindow = Annotated[
    bool,
    typer.Option(
        "--ignore-window",
        help="Run outside the crawl window (manual checks only). Delays still apply.",
    ),
]


def _crawl_config(settings: Settings, ignore_window: bool) -> CrawlConfig:
    """The crawl config for a live run. Refuses a placeholder contact URL."""
    config = settings.crawl
    if (config.contact_url.host or "").removeprefix("www.") in PLACEHOLDER_CONTACT_HOSTS:
        typer.echo(
            f"contact_url in {settings.config_dir / 'crawl.yaml'} is a placeholder "
            f"({config.contact_url}). Set a real page about the bot before crawling.",
            err=True,
        )
        raise typer.Exit(code=2)
    if ignore_window:
        typer.echo("Ignoring the crawl window. The per-host delay still applies.", err=True)
        return without_crawl_window(config)
    return config


@app.callback()
def main() -> None:
    """Agent-ready Telangana pipeline."""
    # Windows consoles often can't show Telugu or other characters found in page text:
    # print a replacement character instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


@app.command()
def version() -> None:
    """Print the agentready version."""
    typer.echo(__version__)


# --- db -------------------------------------------------------------------------


@db_app.command("init")
def db_init() -> None:
    """Create the database (if needed) and apply pending migrations."""
    settings = _settings()
    conn = connect(settings.db_path)
    try:
        applied = migrate(conn)
        current = applied_versions(conn)
    finally:
        conn.close()
    if applied:
        typer.echo(f"Applied migrations {', '.join(f'{v:04d}' for v in applied)}.")
    else:
        typer.echo("Database already up to date.")
    typer.echo(f"Schema version {current[-1]:04d} at {settings.db_path}")


# --- sites ----------------------------------------------------------------------


@sites_app.command("list")
def sites_list() -> None:
    """Validate config/sites.yaml and list its sites."""
    sites = _sites(_settings())
    width = max((len(s.id) for s in sites), default=2)
    for site in sites:
        languages = ",".join(site.languages)
        status = "" if site.active else "  (inactive)"
        typer.echo(f"{site.id:<{width}}  {site.kind:<10}  {languages:<8}  {site.base_url}{status}")
    typer.echo(f"{len(sites)} site(s), {sum(s.active for s in sites)} active.")


@sites_app.command("propose")
def sites_propose(
    url: Annotated[str, typer.Argument(help="URL of the directory page.")],
    html: Annotated[
        Path | None,
        typer.Option(
            "--html",
            exists=True,
            dir_okay=False,
            help="Use a copy of the page saved from a browser instead of fetching it.",
        ),
    ] = None,
    domain: Annotated[
        list[str] | None,
        typer.Option("--domain", help="Accept hosts under this domain. Repeatable."),
    ] = None,
    ignore_window: IgnoreWindow = False,
) -> None:
    """Propose candidate sites from a directory page, for review. Never edits sites.yaml."""
    settings = _settings()
    existing = _sites(settings)
    if html is not None:
        page = html.read_bytes()
    else:
        config = _crawl_config(settings, ignore_window)
        try:
            page = asyncio.run(
                fetch_page(url, config, user_agent=config.user_agent_header(__version__))
            )
        except FetchError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    candidates = propose_sites(
        page, url, existing=existing, domain_suffixes=domain or DEFAULT_DOMAIN_SUFFIXES
    )
    out = settings.proposed_sites_file
    write_proposals(out, candidates, source_url=url, proposed_at=datetime.now(UTC))
    typer.echo(f"Proposed {len(candidates)} new site(s); review them in {out}")


@sites_app.command("sync")
def sites_sync() -> None:
    """Copy config/sites.yaml into the database; sites removed from the file become inactive."""
    settings = _settings()
    sites = _sites(settings)
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        result = sync_sites(conn, sites)
    finally:
        conn.close()
    for label, ids in [
        ("added", result.added),
        ("updated", result.updated),
        ("unchanged", result.unchanged),
        ("deactivated", result.deactivated),
    ]:
        typer.echo(f"{label:<12} {len(ids):>3}  {', '.join(ids)}".rstrip())


# --- crawl ----------------------------------------------------------------------


@app.command()
def crawl(
    site: Annotated[str | None, typer.Option("--site", help="Site id from sites.yaml.")] = None,
    all_sites: Annotated[bool, typer.Option("--all", help="Crawl every active site.")] = False,
    max_pages: Annotated[
        int | None,
        typer.Option("--max-pages", min=1, help="Lower the per-site page cap for this run."),
    ] = None,
    ignore_window: IgnoreWindow = False,
) -> None:
    """Crawl one site (--site) or every active site (--all), politely."""
    if (site is None) == (not all_sites):
        typer.echo("Give exactly one of --site <id> or --all.", err=True)
        raise typer.Exit(code=2)
    settings = _settings()
    sites = _sites(settings)
    if site is not None:
        chosen = [s for s in sites if s.id == site]
        if not chosen:
            typer.echo(f"Unknown site id {site!r}. See `agentready sites list`.", err=True)
            raise typer.Exit(code=2)
    else:
        chosen = [s for s in sites if s.active]
    config = _crawl_config(settings, ignore_window)
    user_agent = config.user_agent_header(__version__)

    conn = connect(settings.db_path)
    try:
        migrate(conn)
        sync_sites(conn, sites)
        raw = RawStore(settings.data_dir)
        for target in chosen:
            typer.echo(f"Crawling {target.id} ({target.base_url}) as {user_agent}")
            report = asyncio.run(
                crawl_site(conn, raw, target, config, user_agent=user_agent, max_pages=max_pages)
            )
            run = report.run
            skipped = ", ".join(f"{k} {v}" for k, v in sorted(report.skipped.items())) or "none"
            typer.echo(
                f"  {run.stop_reason}: {run.pages_fetched} fetched, {run.error_count} errors, "
                f"{report.stored_new} new files, {report.not_modified} unchanged (304), "
                f"{report.links_found} links ({report.documents_found} to documents); "
                f"robots.txt status {report.robots_status}; skipped: {skipped}"
            )
    finally:
        conn.close()


# --- audit ----------------------------------------------------------------------


@audit_app.command("a11y")
def audit_a11y(
    site: Annotated[str, typer.Option("--site", help="Site id from sites.yaml.")],
    pages: Annotated[
        int | None,
        typer.Option("--pages", min=1, help="Pages to audit (default from config/audit.yaml)."),
    ] = None,
    ignore_window: IgnoreWindow = False,
) -> None:
    """WCAG 2.1 A/AA audit (axe-core) of the home page and most-linked pages."""
    settings = _settings()
    target = next((s for s in _sites(settings) if s.id == site), None)
    if target is None:
        typer.echo(f"Unknown site id {site!r}. See `agentready sites list`.", err=True)
        raise typer.Exit(code=2)
    config = _crawl_config(settings, ignore_window)
    user_agent = config.user_agent_header(__version__)

    conn = connect(settings.db_path)
    try:
        migrate(conn)
        try:
            report = asyncio.run(
                audit_site(
                    conn,
                    RawStore(settings.data_dir),
                    target,
                    config,
                    settings.audit,
                    user_agent=user_agent,
                    pages=pages,
                )
            )
        except AuditError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    finally:
        conn.close()

    typer.echo(
        f"Audited {len(report.pages)} page(s) of {site} from crawl run {report.crawl_run_id}:"
    )
    for page in report.pages:
        status = f"{page.violations} rule(s) failed" if page.ok else f"NOT AUDITED: {page.error}"
        typer.echo(
            f"  {status:<22} {page.url}  "
            f"[{page.requests_made} fetched, {page.requests_blocked} blocked]"
        )
    by_rule: dict[str, int] = {}
    for finding in report.findings:
        by_rule[finding.check_id] = by_rule.get(finding.check_id, 0) + 1
    for rule, count in sorted(by_rule.items(), key=lambda item: (-item[1], item[0])):
        typer.echo(f"  {count:>3} page(s): {rule}")
    blocked = ", ".join(f"{k} {v}" for k, v in sorted(report.blocked.items())) or "none"
    typer.echo(f"Not loaded: {blocked}")
