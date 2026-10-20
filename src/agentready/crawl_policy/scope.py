"""Which URLs belong to a site's crawl. Pure: no network, no DNS."""

import posixpath
import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from agentready.core import CrawlConfig

SESSION_PARAMS = frozenset(
    {"jsessionid", "phpsessid", "sid", "sessionid", "session_id", "aspsessionid"}
)
"""Query parameters that only carry a session; dropped so one page has one URL."""

_PATH_SESSION = re.compile(r";jsessionid=[^/?#]*", re.IGNORECASE)
_DEFAULT_PORTS = {"http": 80, "https": 443}
# Characters left as-is in paths; everything else (spaces, Telugu, Urdu) is percent-encoded,
# matching how pydantic's HttpUrl stores URLs. "%" keeps existing escapes intact.
_PATH_SAFE = "/%;:@!$&'()*+,=-._~"

# Extensions we can rule out before fetching (anything not listed is decided by Content-Type).
EXTENSION_TYPES = {
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".css": "text/css",
    ".js": "text/javascript",
    ".zip": "application/zip",
    ".rar": "application/vnd.rar",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".avi": "video/x-msvideo",
    ".exe": "application/octet-stream",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
}


class ScopeReason(StrEnum):
    NOT_HTTP = "not_http"
    OTHER_HOST = "other_host"
    URL_TOO_LONG = "url_too_long"
    TOO_MANY_QUERY_PARAMS = "too_many_query_params"
    PATH_TOO_DEEP = "path_too_deep"
    REPEATED_SEGMENTS = "repeated_segments"
    UNWANTED_FILE_TYPE = "unwanted_file_type"


@dataclass(frozen=True)
class ScopeResult:
    url: str | None
    """The normalised URL (None when the link is not http(s))."""
    reason: ScopeReason | None = None

    @property
    def in_scope(self) -> bool:
        return self.reason is None


def normalize_url(url: str) -> str | None:
    """Canonical form used for de-duplication and storage, or None for non-http(s) links.

    Lowercases scheme and host, drops default ports, fragments and session ids,
    resolves ``.`` and ``..`` segments, sorts query parameters and percent-encodes
    non-ASCII characters, so the result is unchanged by pydantic's ``HttpUrl``.
    """
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None

    host = parts.hostname.lower().rstrip(".")
    netloc = host if port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{port}"

    path = _PATH_SESSION.sub("", parts.path) or "/"
    trailing = path.endswith("/")
    path = posixpath.normpath(quote(path, safe=_PATH_SAFE))
    if path.startswith("//"):  # normpath keeps a leading "//"
        path = "/" + path.lstrip("/")
    if trailing and path != "/":
        path += "/"

    params = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in SESSION_PARAMS
    ]
    query = urlencode(sorted(params), quote_via=quote)
    return urlunsplit((scheme, netloc, path, query, ""))


def same_site(host: str, site_host: str) -> bool:
    """Hosts match, ignoring case, a trailing dot and a leading ``www.``."""
    return _bare(host) == _bare(site_host)


def check_scope(url: str, base_url: str, config: CrawlConfig) -> ScopeResult:
    """Decide whether ``url`` (absolute) belongs to the crawl of the site at ``base_url``.

    In scope means: http(s), the same host as the site (``www.`` and http/https
    differences ignored), and not a crawler trap or an unwanted file type.
    """
    normalised = normalize_url(url)
    if normalised is None:
        return ScopeResult(None, ScopeReason.NOT_HTTP)
    parts = urlsplit(normalised)
    if not same_site(parts.hostname or "", urlsplit(base_url).hostname or ""):
        return ScopeResult(normalised, ScopeReason.OTHER_HOST)
    if len(normalised) > config.max_url_length:
        return ScopeResult(normalised, ScopeReason.URL_TOO_LONG)
    if len(parse_qsl(parts.query, keep_blank_values=True)) > config.max_query_params:
        return ScopeResult(normalised, ScopeReason.TOO_MANY_QUERY_PARAMS)

    segments = [s for s in parts.path.split("/") if s]
    if len(segments) > config.max_path_depth:
        return ScopeResult(normalised, ScopeReason.PATH_TOO_DEEP)
    if segments and max(Counter(segments).values()) > config.max_segment_repeats:
        return ScopeResult(normalised, ScopeReason.REPEATED_SEGMENTS)

    guessed = type_from_extension(normalised)
    if guessed is not None and guessed not in config.allowed_content_types:
        return ScopeResult(normalised, ScopeReason.UNWANTED_FILE_TYPE)
    return ScopeResult(normalised)


def type_from_extension(url: str) -> str | None:
    """Media type implied by the URL's file extension, from a fixed table (not the OS)."""
    _, ext = posixpath.splitext(urlsplit(url).path.lower())
    return EXTENSION_TYPES.get(ext)


def _bare(host: str) -> str:
    return host.lower().rstrip(".").removeprefix("www.")
