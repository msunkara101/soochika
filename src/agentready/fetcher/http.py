"""One polite HTTP client per crawl: per-host delay, retries, redirects checked hop by hop.

TLS verification is always on. Certificate problems come back as errors to record,
never as a reason to retry without verification.
"""

import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlsplit

import httpx

from agentready.core import CrawlConfig
from agentready.crawl_policy import (
    RobotsRules,
    file_too_large,
    host_delay,
    max_file_bytes,
    normalize_url,
    robots_from_response,
    seconds_until_allowed,
)
from agentready.fetcher.clock import Clock

REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
MAX_RETRY_AFTER_SECONDS = 600


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int | None = None
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    body: bytes | None = None
    """Only set for a 2xx response whose content we keep."""
    error: str | None = None

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304


class _Retry(Exception):
    def __init__(
        self, error: str, retry_after: float | None = None, status_code: int | None = None
    ) -> None:
        self.error = error
        self.retry_after = retry_after
        self.status_code = status_code


class PoliteClient:
    """Sends GET requests no faster than the host's delay allows."""

    def __init__(
        self,
        config: CrawlConfig,
        *,
        user_agent: str,
        clock: Clock,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.clock = clock
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            timeout=config.request_timeout_seconds,
            follow_redirects=False,
            verify=True,
            transport=transport,
        )
        self._last_request: dict[str, datetime] = {}
        self._delays: dict[str, float] = {}
        self.request_times: list[tuple[str, datetime]] = []
        """(url, time) of every request sent, in order. Used for logging and tests."""

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "PoliteClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    def set_robots(self, url: str, robots: RobotsRules) -> None:
        """Apply robots.txt's Crawl-delay (if longer than ours) to the host of ``url``."""
        self._delays[_host_key(url)] = host_delay(self.config, robots)

    async def fetch_robots(self, site_url: str) -> tuple[RobotsRules, FetchResult]:
        """Fetch ``/robots.txt`` for the host of ``site_url`` and turn it into rules."""
        parts = urlsplit(site_url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        result = await self.fetch(robots_url, keep_body=lambda _type, _url: True)
        rules = robots_from_response(result.status_code, result.body or b"", self._user_agent_token)
        self.set_robots(site_url, rules)
        return rules, result

    async def fetch(
        self,
        url: str,
        *,
        keep_body: Callable[[str | None, str], bool],
        conditional: dict[str, str] | None = None,
        may_follow: Callable[[str], str | None] | None = None,
    ) -> FetchResult:
        """GET ``url``, following redirects that ``may_follow`` allows (it returns a refusal
        reason, or None to follow). Retries 5xx responses and timeouts with backoff.
        """
        current = url
        redirects = 0
        attempt = 0
        while True:
            try:
                outcome = await self._attempt(url, current, keep_body, conditional or {})
            except _Retry as retry:
                if attempt >= self.config.max_retries:
                    return FetchResult(
                        url,
                        current,
                        retry.status_code,
                        error=f"{retry.error} after {attempt + 1} attempts",
                    )
                attempt += 1
                await self._backoff(attempt, retry.retry_after)
                continue
            if isinstance(outcome, FetchResult):
                return outcome

            status, location = outcome  # a redirect
            target = normalize_url(urljoin(current, location))
            if target is None:
                return FetchResult(
                    url, current, status, error=f"redirect to unsupported URL {location!r}"
                )
            if redirects >= self.config.max_redirects:
                return FetchResult(url, target, status, error="too many redirects")
            refusal = may_follow(target) if may_follow else None
            if refusal:
                return FetchResult(url, target, status, error=f"redirect not followed: {refusal}")
            current = target
            redirects += 1

    async def _attempt(
        self,
        url: str,
        current: str,
        keep_body: Callable[[str | None, str], bool],
        conditional: dict[str, str],
    ) -> FetchResult | tuple[int, str]:
        await self._wait_turn(current)
        request = self._client.build_request("GET", current, headers=conditional)
        try:
            response = await self._client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise _Retry("timeout") from exc
        except httpx.HTTPError as exc:
            return FetchResult(url, current, error=describe_error(exc))

        try:
            status = response.status_code
            location = response.headers.get("location")
            if status in REDIRECT_CODES and location:
                return status, location
            if status >= 500:
                raise _Retry(
                    f"http {status}", _retry_after(response.headers.get("retry-after")), status
                )

            content_type = response.headers.get("content-type")
            meta = {
                "status_code": status,
                "content_type": content_type,
                "etag": response.headers.get("etag"),
                "last_modified": response.headers.get("last-modified"),
            }
            if status == 304:
                return FetchResult(url, current, **meta)
            if status >= 300:
                return FetchResult(url, current, **meta, error=f"http {status}")
            if not keep_body(content_type, current):
                return FetchResult(
                    url, current, **meta, error=f"content type not kept: {content_type}"
                )

            limit = max_file_bytes(self.config)
            declared = _int_or_none(response.headers.get("content-length"))
            if file_too_large(declared, self.config):
                return FetchResult(
                    url, current, **meta, error=f"file too large: {declared} bytes (limit {limit})"
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if file_too_large(len(body), self.config):
                    return FetchResult(
                        url, current, **meta, error=f"file too large: over {limit} bytes"
                    )
            return FetchResult(url, current, **meta, body=bytes(body))
        except httpx.TimeoutException as exc:
            raise _Retry("timeout") from exc
        except httpx.HTTPError as exc:
            return FetchResult(url, current, response.status_code, error=describe_error(exc))
        finally:
            await response.aclose()

    async def _wait_turn(self, url: str) -> None:
        host = _host_key(url)
        delay = self._delays.get(host, host_delay(self.config))
        await self.clock.sleep(
            seconds_until_allowed(self._last_request.get(host), self.clock.now(), delay)
        )
        now = self.clock.now()
        self._last_request[host] = now
        self.request_times.append((url, now))

    async def _backoff(self, attempt: int, retry_after: float | None) -> None:
        wait = self.config.retry_backoff_seconds * 2 ** (attempt - 1)
        if retry_after is not None:
            wait = max(wait, retry_after)
        await self.clock.sleep(wait)

    @property
    def _user_agent_token(self) -> str:
        return str(self._client.headers["User-Agent"])


def describe_error(exc: BaseException) -> str:
    """A short, stable description: ``tls_error: ...``, ``connect_error: ...`` and so on."""
    for cause in _chain(exc):
        if isinstance(cause, ssl.SSLError):
            return f"tls_error: {cause}"
    if isinstance(exc, httpx.ConnectError):
        return f"connect_error: {exc}"
    if isinstance(exc, httpx.TooManyRedirects):
        return "too many redirects"
    return f"{type(exc).__name__}: {exc}"


def _chain(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def _host_key(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".").removeprefix("www.")


def _retry_after(value: str | None) -> float | None:
    seconds = _int_or_none(value)
    if seconds is None or seconds < 0:
        return None
    return float(min(seconds, MAX_RETRY_AFTER_SECONDS))


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
