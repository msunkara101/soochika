import pytest

from agentready.crawl_policy import ALLOW_ALL, DISALLOW_ALL, parse_robots, robots_from_response

UA = "agentready-bot/0.1.0 (+https://example.org/bot)"
SITE = "https://agri.telangana.gov.in"

BLOCKS_US = """\
User-agent: *
Allow: /

User-agent: agentready-bot
Disallow: /
"""

BLOCKS_EVERYONE = """\
User-agent: *
Disallow: /
"""

BLOCKS_AI_ONLY = """\
User-agent: GPTBot
User-agent: ClaudeBot
Disallow: /

User-agent: *
Disallow: /admin/
"""

WILDCARDS = """\
User-agent: *
Disallow: /*.php$
Disallow: /private*/
Disallow: /search?
Allow: /private-docs/public/
"""

PRECEDENCE = """\
User-agent: *
Disallow: /docs/
Allow: /docs/
Disallow: /gos/
Allow: /gos/2026/
"""

OURS_OVER_STAR = """\
User-agent: *
Disallow: /

User-agent: AgentReady-Bot
Disallow: /tenders/
"""

MERGED_GROUPS = """\
User-agent: agentready-bot
Disallow: /a/

User-agent: other
Disallow: /

User-agent: agentready-bot
Disallow: /b/
"""

COMMENTS_AND_JUNK = """\
﻿# Telangana portal robots
User-agent: * # everyone
Disallow: /tmp/   # temp files
this line is junk
Disallow:
Noindex: /ignored/
"""

TELUGU = """\
User-agent: *
Disallow: /పత్రాలు/
"""


@pytest.mark.parametrize(
    ("robots", "path", "allowed"),
    [
        # A robots.txt that blocks us specifically, while allowing everyone else.
        (BLOCKS_US, "/", False),
        (BLOCKS_US, "/notices/1.pdf", False),
        (BLOCKS_EVERYONE, "/", False),
        (BLOCKS_EVERYONE, "/index.html", False),
        # Blocking AI agents by name does not block us; their rules don't apply to us.
        (BLOCKS_AI_ONLY, "/notices/", True),
        (BLOCKS_AI_ONLY, "/admin/login", False),
        # Wildcards and end anchors.
        (WILDCARDS, "/index.php", False),
        (WILDCARDS, "/index.php?id=3", True),  # $ anchors the end of path+query
        (WILDCARDS, "/private/x", False),
        (WILDCARDS, "/private-files/x", False),
        (WILDCARDS, "/private-docs/public/a.pdf", True),  # longer Allow wins
        (WILDCARDS, "/search?q=go", False),
        (WILDCARDS, "/search", True),
        # Longest match wins; on a tie, Allow wins.
        (PRECEDENCE, "/docs/a.pdf", True),
        (PRECEDENCE, "/gos/2025/x.pdf", False),
        (PRECEDENCE, "/gos/2026/x.pdf", True),
        # Our own group (matched case-insensitively) replaces the * group.
        (OURS_OVER_STAR, "/", True),
        (OURS_OVER_STAR, "/tenders/2026", False),
        # Repeated groups for our agent are merged.
        (MERGED_GROUPS, "/a/1", False),
        (MERGED_GROUPS, "/b/1", False),
        (MERGED_GROUPS, "/c/1", True),
        # Comments, BOM, empty Disallow and unknown lines are tolerated.
        (COMMENTS_AND_JUNK, "/tmp/x", False),
        (COMMENTS_AND_JUNK, "/ignored/x", True),
        (COMMENTS_AND_JUNK, "/", True),
        # Non-ASCII paths match their percent-encoded form.
        (TELUGU, "/%E0%B0%AA%E0%B0%A4%E0%B1%8D%E0%B0%B0%E0%B0%BE%E0%B0%B2%E0%B1%81/go.pdf", False),
        # Empty file, or no matching group: everything allowed.
        ("", "/anything", True),
        ("User-agent: GPTBot\nDisallow: /\n", "/anything", True),
        # robots.txt itself is always fetchable.
        (BLOCKS_US, "/robots.txt", True),
    ],
)
def test_can_fetch(robots: str, path: str, allowed: bool) -> None:
    assert parse_robots(robots, UA).can_fetch(SITE + path) is allowed


def test_rules_before_any_user_agent_are_ignored() -> None:
    rules = parse_robots("Disallow: /\nUser-agent: *\nDisallow: /x/\n", UA)
    assert rules.can_fetch(SITE + "/")
    assert not rules.can_fetch(SITE + "/x/1")


@pytest.mark.parametrize(
    ("robots", "delay"),
    [
        ("User-agent: *\nCrawl-delay: 10\n", 10.0),
        ("User-agent: *\nCrawl-delay: 2.5\n", 2.5),
        ("User-agent: *\nCrawl-delay: soon\n", None),
        ("User-agent: *\nCrawl-delay: -4\n", None),
        ("User-agent: *\nCrawl-delay: 30\n\nUser-agent: agentready-bot\nCrawl-delay: 8\n", 8.0),
        ("User-agent: *\nDisallow:\n", None),
    ],
)
def test_crawl_delay(robots: str, delay: float | None) -> None:
    assert parse_robots(robots, UA).crawl_delay == delay


def test_sitemaps_are_collected_from_anywhere() -> None:
    robots = (
        "Sitemap: https://agri.telangana.gov.in/sitemap.xml\n"
        "User-agent: *\nDisallow:\n"
        "Sitemap: https://agri.telangana.gov.in/sitemap-docs.xml\n"
    )
    assert parse_robots(robots, UA).sitemaps == (
        "https://agri.telangana.gov.in/sitemap.xml",
        "https://agri.telangana.gov.in/sitemap-docs.xml",
    )


def test_bytes_input_and_size_limit() -> None:
    padding = b"# " + b"x" * (600 * 1024) + b"\n"
    rules = parse_robots(b"User-agent: *\nDisallow: /a/\n" + padding + b"Disallow: /b/\n", UA)
    assert not rules.can_fetch(SITE + "/a/")
    assert rules.can_fetch(SITE + "/b/")  # beyond 500 KiB: ignored


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (404, ALLOW_ALL),
        (403, ALLOW_ALL),
        (410, ALLOW_ALL),
        (500, DISALLOW_ALL),
        (503, DISALLOW_ALL),
        (None, DISALLOW_ALL),  # DNS failure, timeout, TLS error
        (304, DISALLOW_ALL),
    ],
)
def test_robots_from_status(status: int | None, expected: object) -> None:
    assert robots_from_response(status, b"User-agent: *\nDisallow:\n", UA) == expected


def test_robots_from_ok_response_parses_body() -> None:
    rules = robots_from_response(200, BLOCKS_US.encode(), UA)
    assert not rules.can_fetch(SITE + "/")


def test_disallow_all_still_allows_robots_txt() -> None:
    assert not DISALLOW_ALL.can_fetch(SITE + "/")
    assert DISALLOW_ALL.can_fetch(SITE + "/robots.txt")
