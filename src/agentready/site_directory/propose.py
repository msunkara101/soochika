"""Propose candidate sites from a directory page, for a human to review.

Parsing is pure. ``write_proposals`` writes the review file; nothing here touches
``sites.yaml``.
"""

import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import yaml
from selectolax.lexbor import LexborHTMLParser

from agentready.core import Language, Site, SiteKind
from agentready.site_directory.loader import site_host

DEFAULT_DOMAIN_SUFFIXES = ("telangana.gov.in",)
DEFAULT_LANGUAGES = (Language.EN, Language.TE)
_DISTRICT = re.compile(r"\bdistrict\b|జిల్లా", re.IGNORECASE)
_NOT_SLUG = re.compile(r"[^a-z0-9]+")
_MAX_ID = 64


def propose_sites(
    html: bytes | str,
    page_url: str,
    *,
    existing: Iterable[Site] = (),
    domain_suffixes: Iterable[str] = DEFAULT_DOMAIN_SUFFIXES,
) -> list[Site]:
    """Return one candidate ``Site`` per new host linked from the page.

    A link becomes a candidate when it is http(s), its host is under one of
    ``domain_suffixes``, it is not the directory page's own host, and no site in
    ``existing`` already has that host. Names and kinds are guesses from link text.
    """
    tree = LexborHTMLParser(html)
    base_tag = tree.css_first("base[href]")
    base_url = urljoin(page_url, base_tag.attributes.get("href") or "") if base_tag else page_url
    page_host = _bare_host(urlsplit(page_url).hostname or "")
    suffixes = tuple(s.lower().strip(".") for s in domain_suffixes)

    existing = list(existing)
    known_hosts = {site_host(site) for site in existing}
    used_ids = {site.id for site in existing}

    links: dict[str, tuple[str, str]] = {}  # host -> (base_url, best link text)
    for anchor in tree.css("a[href]"):
        href = (anchor.attributes.get("href") or "").strip()
        parts = urlsplit(urljoin(base_url, href))
        if parts.scheme not in ("http", "https") or not parts.hostname:
            continue
        host = _bare_host(parts.hostname)
        if host == page_host or host in known_hosts or not _under(host, suffixes):
            continue
        text = _clean(anchor.text(separator=" ")) or _clean(anchor.attributes.get("title") or "")
        root = f"{parts.scheme}://{parts.netloc.lower()}/"
        if host not in links:
            links[host] = (root, text)
        elif not links[host][1] and text:
            links[host] = (links[host][0], text)

    candidates: list[Site] = []
    for host, (root, text) in links.items():
        site_id = _unique_id(_slug(host, suffixes), used_ids)
        used_ids.add(site_id)
        name = text or host
        candidates.append(
            Site(
                id=site_id,
                name=name,
                department=name,
                base_url=root,
                kind=SiteKind.DISTRICT if _DISTRICT.search(name) else SiteKind.DEPARTMENT,
                languages=list(DEFAULT_LANGUAGES),
            )
        )
    return candidates


def write_proposals(
    path: Path, sites: list[Site], *, source_url: str, proposed_at: datetime
) -> None:
    """Write candidates in ``sites.yaml`` format, so reviewed entries can be copied across."""
    header = (
        "# Candidate sites for review. NOT used by the crawler.\n"
        f"# Proposed from: {source_url}\n"
        f"# Proposed at:   {proposed_at.isoformat(timespec='seconds')}\n"
        "# Names, departments, kinds and languages are guesses from link text: check each\n"
        "# one, then copy the entries you accept into config/sites.yaml.\n\n"
    )
    entries = [site.model_dump(mode="json", exclude={"active"}) for site in sites]
    body = yaml.safe_dump({"sites": entries}, allow_unicode=True, sort_keys=False, width=100)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + body, encoding="utf-8")


def _bare_host(host: str) -> str:
    return host.lower().rstrip(".").removeprefix("www.")


def _under(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


def _slug(host: str, suffixes: tuple[str, ...]) -> str:
    for suffix in sorted(suffixes, key=len, reverse=True):
        if host.endswith("." + suffix):
            host = host.removesuffix("." + suffix)
            break
    return _NOT_SLUG.sub("-", host).strip("-")[:_MAX_ID].strip("-") or "site"


def _unique_id(base: str, used: set[str]) -> str:
    candidate, n = base, 2
    while candidate in used:
        suffix = f"-{n}"
        candidate = base[: _MAX_ID - len(suffix)] + suffix
        n += 1
    return candidate


def _clean(text: str) -> str:
    return " ".join(text.split())
