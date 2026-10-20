"""Fetch one page politely, outside a crawl (for example a directory page for `sites propose`)."""

import httpx

from agentready.core import CrawlConfig
from agentready.crawl_policy import in_crawl_window, media_type, same_site
from agentready.fetcher.clock import Clock, SystemClock
from agentready.fetcher.http import PoliteClient
from agentready.fetcher.links import HTML_TYPES


class FetchError(RuntimeError):
    pass


async def fetch_page(
    url: str,
    config: CrawlConfig,
    *,
    user_agent: str,
    clock: Clock | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bytes:
    """Return the HTML at ``url`` after checking the crawl window and robots.txt.

    Two requests at most (robots.txt, then the page), with the usual per-host delay.
    Redirects are followed only within the same host. Nothing is stored.
    """
    clock = clock or SystemClock()
    if not in_crawl_window(clock.now(), config.crawl_window):
        raise FetchError("outside the crawl window (see crawl_window in config/crawl.yaml)")

    async with PoliteClient(
        config, user_agent=user_agent, clock=clock, transport=transport
    ) as client:
        robots, _ = await client.fetch_robots(url)
        if not robots.can_fetch(url):
            raise FetchError(f"robots.txt disallows {url}")

        def may_follow(target: str) -> str | None:
            host, origin = httpx.URL(target).host, httpx.URL(url).host
            if not same_site(host, origin):
                return f"redirect leaves {origin}"
            return None if robots.can_fetch(target) else "disallowed by robots.txt"

        result = await client.fetch(
            url,
            keep_body=lambda content_type, _u: media_type(content_type) in HTML_TYPES,
            may_follow=may_follow,
        )
    if result.error or result.body is None:
        raise FetchError(f"could not fetch {url}: {result.error or 'empty response'}")
    return result.body
