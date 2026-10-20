"""Network failures a local test server can't produce: TLS errors, timeouts, streamed bodies."""

import ssl
from pathlib import Path

import httpx
import pytest

from agentready.fetcher import describe_error

from .conftest import Env, html, make_config

BASE = "https://agri.telangana.gov.in/"


def tls_failure(request: httpx.Request) -> httpx.Response:
    cause = ssl.SSLCertVerificationError(1, "certificate verify failed: certificate has expired")
    raise httpx.ConnectError("TLS handshake failed", request=request) from cause


@pytest.fixture
def env(tmp_path: Path):
    environment = Env(tmp_path, BASE)
    yield environment
    environment.conn.close()


def test_tls_error_is_recorded_not_bypassed(env: Env) -> None:
    report = env.crawl(transport=httpx.MockTransport(tls_failure))
    robots = env.by_path()["/robots.txt"]
    assert robots.error is not None
    assert robots.error.startswith("tls_error: ")
    assert "certificate verify failed" in robots.error
    assert robots.status_code is None
    # robots.txt unreachable: treated as "disallow all", so nothing else is requested.
    assert report.run.pages_fetched == 0
    assert report.run.stop_reason == "completed"


def test_timeouts_are_retried_then_recorded(env: Env) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ReadTimeout("read timed out", request=request)

    report = env.crawl(transport=httpx.MockTransport(handler))
    home = env.by_path()["/"]
    assert home.error == "timeout after 3 attempts"
    assert calls.count("/") == 3
    assert report.run.error_count == 1


def test_connection_refused_is_recorded(env: Env) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ConnectError("connection refused", request=request)

    env.crawl(transport=httpx.MockTransport(handler))
    assert env.by_path()["/"].error == "connect_error: connection refused"


def test_streamed_body_without_length_is_capped(env: Env) -> None:
    async def chunks():
        for _ in range(100):
            yield b"0" * 1024

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/":
            return httpx.Response(200, html=html("/huge.pdf"))
        return httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=chunks())

    env.crawl(make_config(max_file_mb=0.05), transport=httpx.MockTransport(handler))
    huge = env.by_path()["/huge.pdf"]
    assert huge.error is not None and huge.error.startswith("file too large: over")
    assert huge.sha256 is None


def test_octet_stream_pdf_is_kept_by_extension(env: Env) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/":
            return httpx.Response(200, html=html("/GO-12.PDF"))
        return httpx.Response(
            200, headers={"Content-Type": "application/octet-stream"}, content=b"%PDF-1.4"
        )

    env.crawl(transport=httpx.MockTransport(handler))
    pdf = env.by_path()["/GO-12.PDF"]
    assert pdf.error is None
    assert pdf.sha256 is not None


def test_describe_error_finds_ssl_cause_anywhere_in_the_chain() -> None:
    request = httpx.Request("GET", BASE)
    try:
        tls_failure(request)
    except httpx.ConnectError as exc:
        assert describe_error(exc).startswith("tls_error: ")
    assert describe_error(httpx.ConnectError("refused", request=request)).startswith(
        "connect_error"
    )
