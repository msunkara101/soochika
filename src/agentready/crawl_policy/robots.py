"""robots.txt rules for our user agent (RFC 9309), as pure functions.

Differences from ``urllib.robotparser``: supports ``*`` and ``$`` wildcards, uses
longest-match precedence (``Allow`` wins a tie), merges repeated groups for the same
agent, and turns the HTTP status of the robots.txt fetch into a rule set.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

MAX_ROBOTS_BYTES = 500 * 1024
"""RFC 9309: crawlers must parse at least 500 KiB; we ignore anything beyond that."""


@dataclass(frozen=True)
class Rule:
    allow: bool
    pattern: str
    _regex: re.Pattern[str] = field(repr=False, compare=False)

    @classmethod
    def build(cls, allow: bool, pattern: str) -> "Rule":
        return cls(allow, pattern, _compile(pattern))

    def matches(self, path: str) -> bool:
        return self._regex.match(path) is not None


@dataclass(frozen=True)
class RobotsRules:
    """The rules that apply to one user agent on one host."""

    rules: tuple[Rule, ...] = ()
    crawl_delay: float | None = None
    sitemaps: tuple[str, ...] = ()
    disallow_all: bool = False
    """Set when robots.txt could not be fetched because of a server error (RFC 9309 2.3.1.4)."""

    def can_fetch(self, url: str) -> bool:
        path = _path_of(url)
        if path == "/robots.txt":
            return True
        if self.disallow_all:
            return False
        best: Rule | None = None
        for rule in self.rules:
            if not rule.matches(path):
                continue
            if (
                best is None
                or len(rule.pattern) > len(best.pattern)
                or (len(rule.pattern) == len(best.pattern) and rule.allow and not best.allow)
            ):
                best = rule
        return True if best is None else best.allow


ALLOW_ALL = RobotsRules()
DISALLOW_ALL = RobotsRules(disallow_all=True)


def parse_robots(content: bytes | str, user_agent: str) -> RobotsRules:
    """Parse robots.txt and return the rules for ``user_agent`` (its product token).

    Groups naming our token win over ``*``; with neither, everything is allowed.
    """
    if isinstance(content, bytes):
        content = content[:MAX_ROBOTS_BYTES].decode("utf-8", errors="replace")
    content = content.removeprefix("﻿")
    token = _product_token(user_agent)

    groups: list[tuple[set[str], list[tuple[str, str]]]] = []
    sitemaps: list[str] = []
    agents: set[str] = set()
    lines: list[tuple[str, str]] = []
    in_rules = False

    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "sitemap":
            if value:
                sitemaps.append(value)
        elif key == "user-agent":
            if in_rules:  # a user-agent line after rules starts a new group
                groups.append((agents, lines))
                agents, lines, in_rules = set(), [], False
            agents.add(_product_token(value))
        elif key in ("allow", "disallow", "crawl-delay") and agents:
            lines.append((key, value))
            in_rules = True
    if agents:
        groups.append((agents, lines))

    ours = [lines for names, lines in groups if token in names]
    chosen = ours or [lines for names, lines in groups if "*" in names]

    rules: list[Rule] = []
    crawl_delay: float | None = None
    for group_lines in chosen:
        for key, value in group_lines:
            if key == "crawl-delay":
                crawl_delay = _max_delay(crawl_delay, value)
            elif value:  # an empty Disallow means "allow everything": no rule
                rules.append(Rule.build(key == "allow", _normalise(value)))
    return RobotsRules(tuple(rules), crawl_delay, tuple(sitemaps))


def robots_from_response(status_code: int | None, content: bytes, user_agent: str) -> RobotsRules:
    """Rules from the robots.txt fetch result (after redirects were followed).

    - 2xx: parse the file.
    - 4xx (including 404): no robots.txt, so everything is allowed.
    - 5xx, or no response at all (``None``): assume everything is disallowed.
    """
    if status_code is None or status_code >= 500:
        return DISALLOW_ALL
    if 200 <= status_code < 300:
        return parse_robots(content, user_agent)
    if 400 <= status_code < 500:
        return ALLOW_ALL
    return DISALLOW_ALL  # 1xx or an unfollowed 3xx: be safe


def _product_token(user_agent: str) -> str:
    """``agentready-bot/0.1 (+https://...)`` -> ``agentready-bot``."""
    return user_agent.strip().split("/", 1)[0].split(None, 1)[0].lower() if user_agent else ""


def _path_of(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return _normalise(path)


def _normalise(path: str) -> str:
    # Compare decoded forms, so "/పత్రాలు" in robots.txt matches "/%E0%B0%AA..." in a URL.
    return unquote(path)


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(piece) for piece in body.split("*"))
    return re.compile(regex + ("$" if anchored else ""), re.DOTALL)


def _max_delay(current: float | None, value: str) -> float | None:
    try:
        delay = float(value)
    except ValueError:
        return current
    if delay < 0:
        return current
    return delay if current is None else max(current, delay)
