"""Find the links on an HTML page (for crawling only; content extraction is extract_html's job)."""

import re
from dataclasses import dataclass
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from agentready.crawl_policy import normalize_url

HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_REFRESH_URL = re.compile(r"url\s*=\s*['\"]?([^'\";]+)", re.IGNORECASE)


@dataclass(frozen=True)
class FoundLink:
    url: str
    """Absolute and normalised."""
    text: str | None


def extract_links(html: bytes, page_url: str) -> list[FoundLink]:
    """Links from ``<a>``, ``<area>``, ``<frame>``, ``<iframe>`` and meta refresh, in page order.

    Relative links resolve against ``<base href>`` if present. Non-http(s) links are
    dropped, and each URL appears once (with the first non-empty link text).
    """
    tree = LexborHTMLParser(html)
    base = tree.css_first("base[href]")
    base_url = urljoin(page_url, (base.attributes.get("href") or "").strip()) if base else page_url

    found: dict[str, str | None] = {}

    def add(raw: str | None, text: str | None) -> None:
        if not raw or not raw.strip():
            return
        url = normalize_url(urljoin(base_url, raw.strip()))
        if url is None:
            return
        if url not in found or (found[url] is None and text):
            found[url] = text

    for node in tree.css("meta[http-equiv]"):
        if (node.attributes.get("http-equiv") or "").lower() == "refresh":
            match = _REFRESH_URL.search(node.attributes.get("content") or "")
            if match:
                add(match.group(1), None)
    for node in tree.css("a[href], area[href]"):
        text = " ".join(node.text(separator=" ").split()) or node.attributes.get("title") or None
        add(node.attributes.get("href"), text)
    for node in tree.css("frame[src], iframe[src]"):
        add(node.attributes.get("src"), node.attributes.get("title") or None)

    return [FoundLink(url, text) for url, text in found.items()]
