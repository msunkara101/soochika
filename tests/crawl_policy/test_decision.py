from datetime import datetime, timedelta, timezone

import pytest

from agentready.core import CrawlConfig
from agentready.crawl_policy import (
    ALLOW_ALL,
    ScopeReason,
    Verdict,
    parse_robots,
    should_fetch,
)

IST = timezone(timedelta(hours=5, minutes=30))
NIGHT = datetime(2026, 10, 20, 23, 0, tzinfo=IST)
AFTERNOON = datetime(2026, 10, 20, 14, 0, tzinfo=IST)
BASE = "https://agri.telangana.gov.in/"
BLOCKS_US = parse_robots("User-agent: agentready-bot\nDisallow: /\n", "agentready-bot")
BLOCKS_ADMIN = parse_robots("User-agent: *\nDisallow: /admin/\n", "agentready-bot")


@pytest.mark.parametrize(
    ("url", "robots", "fetched", "now", "verdict"),
    [
        ("https://agri.telangana.gov.in/notices/", ALLOW_ALL, 0, NIGHT, Verdict.ALLOWED),
        ("https://transport.telangana.gov.in/", ALLOW_ALL, 0, NIGHT, Verdict.OUT_OF_SCOPE),
        ("mailto:a@telangana.gov.in", ALLOW_ALL, 0, NIGHT, Verdict.OUT_OF_SCOPE),
        ("https://agri.telangana.gov.in/", BLOCKS_US, 0, NIGHT, Verdict.ROBOTS_DISALLOWED),
        ("https://agri.telangana.gov.in/admin/", BLOCKS_ADMIN, 0, NIGHT, Verdict.ROBOTS_DISALLOWED),
        ("https://agri.telangana.gov.in/", ALLOW_ALL, 0, AFTERNOON, Verdict.OUTSIDE_CRAWL_WINDOW),
        ("https://agri.telangana.gov.in/", ALLOW_ALL, 50, NIGHT, Verdict.PAGE_CAP_REACHED),
        # Order: scope first, then robots, then window, then cap.
        ("https://other.example/", BLOCKS_US, 50, AFTERNOON, Verdict.OUT_OF_SCOPE),
        ("https://agri.telangana.gov.in/", BLOCKS_US, 50, AFTERNOON, Verdict.ROBOTS_DISALLOWED),
        ("https://agri.telangana.gov.in/", ALLOW_ALL, 50, AFTERNOON, Verdict.OUTSIDE_CRAWL_WINDOW),
    ],
)
def test_should_fetch(
    url: str,
    robots: object,
    fetched: int,
    now: datetime,
    verdict: Verdict,
    config: CrawlConfig,
) -> None:
    decision = should_fetch(
        url,
        base_url=BASE,
        robots=robots,  # type: ignore[arg-type]
        pages_fetched=fetched,
        cap=50,
        now=now,
        config=config,
    )
    assert decision.verdict is verdict
    assert decision.allowed is (verdict is Verdict.ALLOWED)


def test_decision_carries_normalised_url_and_scope_reason(config: CrawlConfig) -> None:
    allowed = should_fetch(
        "HTTPS://agri.telangana.gov.in/a/../notices#top",
        base_url=BASE,
        robots=ALLOW_ALL,
        pages_fetched=0,
        cap=50,
        now=NIGHT,
        config=config,
    )
    assert allowed.url == "https://agri.telangana.gov.in/notices"

    skipped = should_fetch(
        "https://agri.telangana.gov.in/logo.png",
        base_url=BASE,
        robots=ALLOW_ALL,
        pages_fetched=0,
        cap=50,
        now=NIGHT,
        config=config,
    )
    assert skipped.verdict is Verdict.OUT_OF_SCOPE
    assert skipped.scope_reason is ScopeReason.UNWANTED_FILE_TYPE
