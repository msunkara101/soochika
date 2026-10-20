"""One question the fetcher asks before every request: may I fetch this URL now?"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from agentready.core import CrawlConfig
from agentready.crawl_policy.limits import in_crawl_window, page_cap_reached
from agentready.crawl_policy.robots import RobotsRules
from agentready.crawl_policy.scope import ScopeReason, check_scope


class Verdict(StrEnum):
    ALLOWED = "allowed"
    OUT_OF_SCOPE = "out_of_scope"
    ROBOTS_DISALLOWED = "robots_disallowed"
    OUTSIDE_CRAWL_WINDOW = "outside_crawl_window"
    PAGE_CAP_REACHED = "page_cap_reached"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    url: str | None
    """The normalised URL to request (None when the link is not http(s))."""
    scope_reason: ScopeReason | None = None

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOWED


def should_fetch(
    url: str,
    *,
    base_url: str,
    robots: RobotsRules,
    pages_fetched: int,
    cap: int,
    now: datetime,
    config: CrawlConfig,
) -> Decision:
    """Check scope, robots.txt, the crawl window and the page cap, in that order.

    The per-host delay is not part of this answer: the fetcher waits
    ``seconds_until_allowed`` after a ``Decision`` says yes.
    """
    scope = check_scope(url, base_url, config)
    if not scope.in_scope:
        return Decision(Verdict.OUT_OF_SCOPE, scope.url, scope.reason)
    assert scope.url is not None
    if not robots.can_fetch(scope.url):
        return Decision(Verdict.ROBOTS_DISALLOWED, scope.url)
    if not in_crawl_window(now, config.crawl_window):
        return Decision(Verdict.OUTSIDE_CRAWL_WINDOW, scope.url)
    if page_cap_reached(pages_fetched, cap):
        return Decision(Verdict.PAGE_CAP_REACHED, scope.url)
    return Decision(Verdict.ALLOWED, scope.url)
