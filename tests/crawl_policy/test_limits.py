from datetime import UTC, datetime, time, timedelta, timezone

import pytest

from agentready.core import CrawlConfig, CrawlWindow
from agentready.crawl_policy import (
    content_type_allowed,
    file_too_large,
    host_delay,
    in_crawl_window,
    max_file_bytes,
    media_type,
    page_cap,
    page_cap_reached,
    parse_robots,
    seconds_until_allowed,
)

from .conftest import make_config

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2026, 10, 20, 23, 0, tzinfo=IST)

# --- rate limit ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("robots", "expected"),
    [
        (None, 5.0),
        ("User-agent: *\nCrawl-delay: 2\n", 5.0),  # never below our minimum
        ("User-agent: *\nCrawl-delay: 20\n", 20.0),  # a longer Crawl-delay is honoured
        ("User-agent: *\nDisallow:\n", 5.0),
    ],
)
def test_host_delay(robots: str | None, expected: float, config: CrawlConfig) -> None:
    rules = parse_robots(robots, "agentready-bot") if robots is not None else None
    assert host_delay(config, rules) == expected


@pytest.mark.parametrize(
    ("last", "now", "delay", "wait"),
    [
        (None, T0, 5, 0.0),
        (T0, T0, 5, 5.0),
        (T0, T0 + timedelta(seconds=2), 5, 3.0),
        (T0, T0 + timedelta(seconds=5), 5, 0.0),
        (T0, T0 + timedelta(minutes=1), 5, 0.0),
        (T0, T0 + timedelta(seconds=1), 20, 19.0),
    ],
)
def test_seconds_until_allowed(
    last: datetime | None, now: datetime, delay: float, wait: float
) -> None:
    assert seconds_until_allowed(last, now, delay) == pytest.approx(wait)


def test_min_delay_cannot_be_configured_below_five_seconds() -> None:
    with pytest.raises(ValueError, match="min_delay_seconds"):
        make_config(min_delay_seconds=1)


# --- caps -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "expected"),
    [(None, 500), (50, 50), (500, 500), (10_000, 500)],  # an override can only lower the cap
)
def test_page_cap(override: int | None, expected: int, config: CrawlConfig) -> None:
    assert page_cap(config, override) == expected


def test_page_cap_rejects_zero(config: CrawlConfig) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        page_cap(config, 0)


@pytest.mark.parametrize(("fetched", "reached"), [(0, False), (49, False), (50, True), (51, True)])
def test_page_cap_reached(fetched: int, reached: bool) -> None:
    assert page_cap_reached(fetched, 50) is reached


@pytest.mark.parametrize(
    ("size", "too_large"),
    [
        (None, False),  # unknown size: the fetcher enforces the cap while reading
        (0, False),
        (50 * 1024 * 1024, False),  # exactly the cap is fine
        (50 * 1024 * 1024 + 1, True),  # an oversized file
        (2 * 1024**3, True),
    ],
)
def test_file_too_large(size: int | None, too_large: bool, config: CrawlConfig) -> None:
    assert file_too_large(size, config) is too_large


def test_max_file_bytes_follows_config() -> None:
    assert max_file_bytes(make_config(max_file_mb=0.5)) == 512 * 1024


# --- content types --------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("text/html", "text/html"),
        ("Text/HTML; charset=UTF-8", "text/html"),
        ("  application/pdf ; name=go.pdf", "application/pdf"),
        ("", None),
        (None, None),
        ("; charset=utf-8", None),
    ],
)
def test_media_type(header: str | None, expected: str | None) -> None:
    assert media_type(header) == expected


@pytest.mark.parametrize(
    ("header", "url", "allowed"),
    [
        ("text/html; charset=utf-8", "https://x.telangana.gov.in/", True),
        ("application/pdf", "https://x.telangana.gov.in/download?id=4", True),
        ("application/msword", "https://x.telangana.gov.in/a.doc", True),
        ("image/jpeg", "https://x.telangana.gov.in/a.jpg", False),
        ("image/jpeg", "https://x.telangana.gov.in/a.pdf", False),  # the declared type wins
        ("text/css", "https://x.telangana.gov.in/a.css", False),
        ("application/zip", "https://x.telangana.gov.in/a.zip", False),
        # Generic or missing types fall back to the URL extension.
        ("application/octet-stream", "https://x.telangana.gov.in/go/12.PDF", True),
        ("application/force-download", "https://x.telangana.gov.in/go/12.pdf", True),
        ("application/octet-stream", "https://x.telangana.gov.in/setup.exe", False),
        ("application/octet-stream", "https://x.telangana.gov.in/download.php", False),
        (None, "https://x.telangana.gov.in/a.html", True),
        (None, "https://x.telangana.gov.in/a", False),
    ],
)
def test_content_type_allowed(
    header: str | None, url: str, allowed: bool, config: CrawlConfig
) -> None:
    assert content_type_allowed(header, url, config) is allowed


def test_allowed_types_are_matched_case_insensitively() -> None:
    config = make_config(allowed_content_types=["Application/PDF"])
    assert content_type_allowed("application/pdf", "https://x/a", config)


# --- crawl window ---------------------------------------------------------------

OVERNIGHT = CrawlWindow(start=time(22, 0), end=time(6, 0), timezone="Asia/Kolkata")
DAYTIME = CrawlWindow(start=time(9, 0), end=time(17, 30), timezone="Asia/Kolkata")
ALWAYS = CrawlWindow(start=time(0, 0), end=time(0, 0), timezone="Asia/Kolkata")


def ist(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 20, hour, minute, tzinfo=IST)


@pytest.mark.parametrize(
    ("window", "now", "inside"),
    [
        # Overnight window, 22:00-06:00 IST
        (OVERNIGHT, ist(23), True),
        (OVERNIGHT, ist(22, 0), True),  # start is inclusive
        (OVERNIGHT, ist(2), True),
        (OVERNIGHT, ist(5, 59), True),
        (OVERNIGHT, ist(6, 0), False),  # end is exclusive
        (OVERNIGHT, ist(14), False),  # a crawl outside the window
        (OVERNIGHT, ist(21, 59), False),
        # The window is in IST whatever timezone "now" is in.
        (OVERNIGHT, datetime(2026, 10, 20, 17, 0, tzinfo=UTC), True),  # 22:30 IST
        (OVERNIGHT, datetime(2026, 10, 20, 8, 0, tzinfo=UTC), False),  # 13:30 IST
        # Same-day window
        (DAYTIME, ist(9), True),
        (DAYTIME, ist(17, 29), True),
        (DAYTIME, ist(17, 30), False),
        (DAYTIME, ist(3), False),
        # start == end: no restriction
        (ALWAYS, ist(14), True),
    ],
)
def test_in_crawl_window(window: CrawlWindow, now: datetime, inside: bool) -> None:
    assert in_crawl_window(now, window) is inside


def test_crawl_window_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        in_crawl_window(datetime(2026, 10, 20, 23, 0), OVERNIGHT)
