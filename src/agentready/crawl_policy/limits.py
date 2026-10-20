"""Rate limit, page and size caps, content types and the crawl window. Pure functions."""

from datetime import datetime
from zoneinfo import ZoneInfo

from agentready.core import CrawlConfig, CrawlWindow
from agentready.crawl_policy.robots import RobotsRules
from agentready.crawl_policy.scope import type_from_extension

GENERIC_TYPES = frozenset(
    {
        "application/octet-stream",
        "binary/octet-stream",
        "application/force-download",
        "application/x-download",
        "application/download",
    }
)
"""Types some government servers send for every file; the URL extension decides instead."""

_MB = 1024 * 1024


# --- rate limit ---------------------------------------------------------------


def host_delay(config: CrawlConfig, robots: RobotsRules | None = None) -> float:
    """Seconds between requests to one host: our minimum, or robots.txt's Crawl-delay if longer."""
    robots_delay = robots.crawl_delay if robots and robots.crawl_delay is not None else 0.0
    return max(config.min_delay_seconds, robots_delay)


def seconds_until_allowed(last_request_at: datetime | None, now: datetime, delay: float) -> float:
    """How long to wait before the next request to a host (0 when it may go now)."""
    if last_request_at is None:
        return 0.0
    elapsed = (now - last_request_at).total_seconds()
    return max(0.0, delay - elapsed)


# --- caps -----------------------------------------------------------------------


def page_cap(config: CrawlConfig, override: int | None = None) -> int:
    """Pages per site per run. ``--max-pages`` can lower the configured cap, never raise it."""
    if override is None:
        return config.max_pages_per_site
    if override < 1:
        raise ValueError("max pages must be at least 1")
    return min(override, config.max_pages_per_site)


def page_cap_reached(pages_fetched: int, cap: int) -> bool:
    return pages_fetched >= cap


def max_file_bytes(config: CrawlConfig) -> int:
    return int(config.max_file_mb * _MB)


def file_too_large(size_bytes: int | None, config: CrawlConfig) -> bool:
    """True when a known size (Content-Length, or bytes read so far) exceeds the cap.

    An unknown size is not too large here; the fetcher must still stop reading at
    ``max_file_bytes``.
    """
    return size_bytes is not None and size_bytes > max_file_bytes(config)


# --- content types --------------------------------------------------------------


def media_type(content_type: str | None) -> str | None:
    """``"Text/HTML; charset=UTF-8"`` -> ``"text/html"``."""
    if not content_type:
        return None
    value = content_type.split(";", 1)[0].strip().lower()
    return value or None


def content_type_allowed(content_type: str | None, url: str, config: CrawlConfig) -> bool:
    """Is a response with this ``Content-Type`` worth keeping?

    A missing or generic type (``application/octet-stream``) falls back to the URL's
    file extension, because many government servers label PDFs that way.
    """
    declared = media_type(content_type)
    if declared is not None and declared not in GENERIC_TYPES:
        return declared in config.allowed_content_types
    guessed = type_from_extension(url)
    return guessed is not None and guessed in config.allowed_content_types


# --- crawl window ---------------------------------------------------------------


def in_crawl_window(now: datetime, window: CrawlWindow) -> bool:
    """Is ``now`` (timezone-aware) inside the window, in the window's own timezone?

    ``start`` after ``end`` means the window crosses midnight (22:00-06:00).
    ``start`` equal to ``end`` means no restriction. ``end`` is exclusive.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(ZoneInfo(window.timezone)).time().replace(tzinfo=None)
    start, end = window.start, window.end
    if start == end:
        return True
    if start < end:
        return start <= local < end
    return local >= start or local < end
