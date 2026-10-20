"""robots.txt rules, per-host rate limits and page caps (pure logic, no network)."""

from agentready.crawl_policy.decision import Decision, Verdict, should_fetch
from agentready.crawl_policy.limits import (
    content_type_allowed,
    file_too_large,
    host_delay,
    in_crawl_window,
    max_file_bytes,
    media_type,
    page_cap,
    page_cap_reached,
    seconds_until_allowed,
)
from agentready.crawl_policy.robots import (
    ALLOW_ALL,
    DISALLOW_ALL,
    RobotsRules,
    parse_robots,
    robots_from_response,
)
from agentready.crawl_policy.scope import (
    ScopeReason,
    ScopeResult,
    check_scope,
    normalize_url,
    same_site,
    type_from_extension,
)

__all__ = [
    "ALLOW_ALL",
    "DISALLOW_ALL",
    "Decision",
    "RobotsRules",
    "ScopeReason",
    "ScopeResult",
    "Verdict",
    "check_scope",
    "content_type_allowed",
    "file_too_large",
    "host_delay",
    "in_crawl_window",
    "max_file_bytes",
    "media_type",
    "normalize_url",
    "page_cap",
    "page_cap_reached",
    "parse_robots",
    "robots_from_response",
    "same_site",
    "seconds_until_allowed",
    "should_fetch",
    "type_from_extension",
]
