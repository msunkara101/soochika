import asyncio
import itertools
from datetime import UTC, datetime

import pytest

from agentready.a11y_auditor import (
    Block,
    PageBudget,
    RequestGate,
    axe_options,
    findings_from_axe,
    severity_for,
)
from agentready.core import Severity
from agentready.crawl_policy import parse_robots

from ..fetcher.conftest import FakeClock, make_config
from .conftest import audit_config

SITE = "https://agri.telangana.gov.in/"


def gate(
    robots: str = "User-agent: *\nDisallow: /private/\n", clock: FakeClock | None = None
) -> RequestGate:
    return RequestGate(
        site_url=SITE,
        robots=parse_robots(robots, "agentready-bot"),
        crawl=make_config(),
        audit=audit_config(),
        clock=clock or FakeClock(),
    )


@pytest.mark.parametrize(
    ("url", "resource_type", "expected"),
    [
        (
            "https://agri.telangana.gov.in/css/site.css",
            "stylesheet",
            "https://agri.telangana.gov.in/css/site.css",
        ),
        (
            "https://www.agri.telangana.gov.in/js/app.js",
            "script",
            "https://www.agri.telangana.gov.in/js/app.js",
        ),
        (
            "https://agri.telangana.gov.in/api/notices",
            "xhr",
            "https://agri.telangana.gov.in/api/notices",
        ),
        ("https://agri.telangana.gov.in/logo.png", "image", Block.RESOURCE_TYPE),
        ("https://agri.telangana.gov.in/font.woff2", "font", Block.RESOURCE_TYPE),
        ("https://agri.telangana.gov.in/v.mp4", "media", Block.RESOURCE_TYPE),
        ("https://fonts.googleapis.com/css?family=Noto", "stylesheet", Block.OTHER_HOST),
        ("https://transport.telangana.gov.in/a.js", "script", Block.OTHER_HOST),
        ("https://agri.telangana.gov.in/private/a.css", "stylesheet", Block.ROBOTS),
    ],
)
def test_check(url: str, resource_type: str, expected: object) -> None:
    assert gate().check(url, resource_type, PageBudget(cap=30)) == expected


def test_page_cap_blocks_new_requests_but_not_cached_ones() -> None:
    g = gate()
    budget = PageBudget(cap=1, made=1)
    url = "https://agri.telangana.gov.in/a.css"
    assert g.check(url, "stylesheet", budget) is Block.PAGE_CAP
    from agentready.a11y_auditor.gate import Cached

    g.cache[url] = Cached(200, {}, b"")
    assert g.check(url, "stylesheet", budget) == url


def test_requests_are_spaced_from_the_end_of_the_previous_one() -> None:
    clock = FakeClock()
    g = gate(clock=clock)

    async def fetch_three() -> None:
        for name in ("a.css", "b.css", "c.js"):
            async with await g.turn(SITE + name):
                clock.current = clock.current.replace(second=clock.current.second)  # no-op
                await clock.sleep(2)  # the download takes 2 s

    asyncio.run(fetch_three())
    starts = [t for _, t in g.request_times]
    gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(starts)]
    assert gaps == [7.0, 7.0]  # 2 s download + 5 s rest


def test_robots_crawl_delay_is_used() -> None:
    g = gate("User-agent: *\nCrawl-delay: 30\n")
    assert g.delay == 30


def test_concurrent_requests_are_serialised() -> None:
    clock = FakeClock()
    g = gate(clock=clock)

    async def one(name: str) -> None:
        async with await g.turn(SITE + name):
            await clock.sleep(1)

    async def many() -> None:
        await asyncio.gather(*(one(f"{n}.css") for n in range(4)))

    asyncio.run(many())
    starts = [t for _, t in g.request_times]
    assert all((b - a).total_seconds() >= 6 for a, b in itertools.pairwise(starts))


# --- axe results -> findings ------------------------------------------------------

AXE_RESULT = {
    "violations": [
        {
            "id": "image-alt",
            "impact": "critical",
            "help": "Images must have alternative text",
            "helpUrl": "https://dequeuniversity.com/rules/axe/4.10/image-alt",
            "tags": ["cat.text-alternatives", "wcag2a", "wcag111", "section508"],
            "nodes": [
                {"target": [f"img:nth-child({n})"], "html": f'<img src="{n}.jpg">'}
                for n in range(5)
            ],
        },
        {
            "id": "color-contrast",
            "impact": "serious",
            "help": "Elements must meet minimum color contrast ratio thresholds",
            "helpUrl": "https://dequeuniversity.com/rules/axe/4.10/color-contrast",
            "tags": ["wcag2aa", "wcag143"],
            "nodes": [{"target": ["p"], "html": "<p>" + "x" * 1000 + "</p>"}],
        },
    ]
}


def test_findings_from_axe() -> None:
    findings = findings_from_axe(
        AXE_RESULT, site_id="agri", url=SITE, audited_at=datetime(2026, 10, 20, tzinfo=UTC)
    )
    assert [f.check_id for f in findings] == ["image-alt", "color-contrast"]
    image = findings[0]
    assert image.severity is Severity.CRITICAL
    import json

    evidence = json.loads(image.evidence or "{}")
    assert evidence["nodes"] == 5
    assert len(evidence["examples"]) == 3
    assert evidence["wcag"] == ["wcag2a", "wcag111"]
    long_html = json.loads(findings[1].evidence or "{}")["examples"][0]["html"]
    assert len(long_html) == 300


@pytest.mark.parametrize(
    ("impact", "severity"),
    [
        ("critical", Severity.CRITICAL),
        ("serious", Severity.HIGH),
        ("moderate", Severity.MEDIUM),
        ("minor", Severity.LOW),
        (None, Severity.MEDIUM),
        ("unknown", Severity.MEDIUM),
    ],
)
def test_severity_for(impact: str | None, severity: Severity) -> None:
    assert severity_for(impact) is severity


def test_axe_options_limit_to_wcag_tags() -> None:
    assert axe_options(["wcag2a", "wcag21aa"]) == {
        "runOnly": {"type": "tag", "values": ["wcag2a", "wcag21aa"]},
        "resultTypes": ["violations"],
    }
