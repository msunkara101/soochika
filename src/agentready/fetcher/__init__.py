"""Polite downloading, conditional GET, link discovery and raw file storage."""

from agentready.fetcher.clock import Clock, SystemClock
from agentready.fetcher.crawl import (
    CrawlReport,
    StopReason,
    crawl_site,
    is_document_url,
    without_crawl_window,
)
from agentready.fetcher.http import FetchResult, PoliteClient, describe_error
from agentready.fetcher.links import FoundLink, extract_links
from agentready.fetcher.page import FetchError, fetch_page

__all__ = [
    "Clock",
    "CrawlReport",
    "FetchError",
    "FetchResult",
    "FoundLink",
    "PoliteClient",
    "StopReason",
    "SystemClock",
    "crawl_site",
    "describe_error",
    "extract_links",
    "fetch_page",
    "is_document_url",
    "without_crawl_window",
]
