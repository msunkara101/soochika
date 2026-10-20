import pytest

from agentready.core import CrawlConfig
from agentready.crawl_policy import ScopeReason, check_scope, normalize_url, same_site

BASE = "https://agri.telangana.gov.in/"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("HTTPS://Agri.Telangana.GOV.in/Notices/", "https://agri.telangana.gov.in/Notices/"),
        ("https://agri.telangana.gov.in", "https://agri.telangana.gov.in/"),
        ("https://agri.telangana.gov.in:443/a", "https://agri.telangana.gov.in/a"),
        ("http://agri.telangana.gov.in:80/a", "http://agri.telangana.gov.in/a"),
        ("http://agri.telangana.gov.in:8080/a", "http://agri.telangana.gov.in:8080/a"),
        ("https://agri.telangana.gov.in/a#section-2", "https://agri.telangana.gov.in/a"),
        ("https://agri.telangana.gov.in/a/./b/../c", "https://agri.telangana.gov.in/a/c"),
        ("https://agri.telangana.gov.in/a/b/", "https://agri.telangana.gov.in/a/b/"),
        ("https://agri.telangana.gov.in/x?b=2&a=1", "https://agri.telangana.gov.in/x?a=1&b=2"),
        (
            "https://agri.telangana.gov.in/x?id=5&PHPSESSID=abc",
            "https://agri.telangana.gov.in/x?id=5",
        ),
        (
            "https://agri.telangana.gov.in/view.jsp;jsessionid=A1B2?id=5",
            "https://agri.telangana.gov.in/view.jsp?id=5",
        ),
        ("mailto:agri@telangana.gov.in", None),
        ("javascript:void(0)", None),
        ("tel:+914023456789", None),
        ("data:text/html,hi", None),
        ("ftp://agri.telangana.gov.in/a", None),
        ("/relative/path", None),
        ("https://agri.telangana.gov.in:99999/", None),
    ],
)
def test_normalize_url(url: str, expected: str | None) -> None:
    assert normalize_url(url) == expected


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        # In scope
        ("https://agri.telangana.gov.in/notices/", None),
        ("http://agri.telangana.gov.in/notices/", None),  # http/https mix
        ("https://www.agri.telangana.gov.in/", None),  # www. ignored
        ("https://agri.telangana.gov.in/gos/G.O.Ms.No.12.pdf", None),
        ("https://agri.telangana.gov.in/page.php?id=12&lang=te", None),
        ("https://agri.telangana.gov.in/page.aspx", None),  # unknown ext: decided by type
        ("https://agri.telangana.gov.in/a#top", None),
        # Not web links
        ("mailto:agri@telangana.gov.in", ScopeReason.NOT_HTTP),
        ("javascript:window.print()", ScopeReason.NOT_HTTP),
        # Other hosts, including other Telangana sites: each site is crawled on its own.
        ("https://transport.telangana.gov.in/", ScopeReason.OTHER_HOST),
        ("https://telangana.gov.in/", ScopeReason.OTHER_HOST),
        ("https://agri.telangana.gov.in.evil.example/", ScopeReason.OTHER_HOST),
        ("https://www.india.gov.in/", ScopeReason.OTHER_HOST),
        # Query-string explosions and other crawler traps
        (
            "https://agri.telangana.gov.in/cal?y=2026&m=10&d=20&view=day",
            ScopeReason.TOO_MANY_QUERY_PARAMS,
        ),
        ("https://agri.telangana.gov.in/" + "x" * 600, ScopeReason.URL_TOO_LONG),
        ("https://agri.telangana.gov.in/" + "a/" * 13, ScopeReason.PATH_TOO_DEEP),
        ("https://agri.telangana.gov.in/a/b/a/b/a/b/", ScopeReason.REPEATED_SEGMENTS),
        # File types we never want
        ("https://agri.telangana.gov.in/banner.JPG", ScopeReason.UNWANTED_FILE_TYPE),
        ("https://agri.telangana.gov.in/static/site.css", ScopeReason.UNWANTED_FILE_TYPE),
        ("https://agri.telangana.gov.in/downloads/all.zip", ScopeReason.UNWANTED_FILE_TYPE),
    ],
)
def test_check_scope(url: str, reason: ScopeReason | None, config: CrawlConfig) -> None:
    result = check_scope(url, BASE, config)
    assert result.reason == reason
    assert result.in_scope is (reason is None)


def test_session_params_do_not_count_towards_the_query_limit(config: CrawlConfig) -> None:
    url = "https://agri.telangana.gov.in/p?a=1&b=2&c=3&jsessionid=xyz"
    assert check_scope(url, BASE, config).in_scope


def test_limits_come_from_config(config: CrawlConfig) -> None:
    url = "https://agri.telangana.gov.in/cal?y=2026&m=10&d=20&view=day"
    loose = config.model_copy(update={"max_query_params": 5})
    assert check_scope(url, BASE, loose).in_scope


def test_scope_returns_normalised_url(config: CrawlConfig) -> None:
    result = check_scope("HTTPS://AGRI.telangana.gov.in/a/../b#x", BASE, config)
    assert result.url == "https://agri.telangana.gov.in/b"


@pytest.mark.parametrize(
    ("host", "site_host", "same"),
    [
        ("agri.telangana.gov.in", "agri.telangana.gov.in", True),
        ("WWW.agri.telangana.gov.in", "agri.telangana.gov.in", True),
        ("agri.telangana.gov.in.", "www.agri.telangana.gov.in", True),
        ("transport.telangana.gov.in", "agri.telangana.gov.in", False),
        ("sub.agri.telangana.gov.in", "agri.telangana.gov.in", False),
    ],
)
def test_same_site(host: str, site_host: str, same: bool) -> None:
    assert same_site(host, site_host) is same
