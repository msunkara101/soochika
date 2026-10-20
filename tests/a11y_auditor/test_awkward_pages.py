"""Pages found on real department sites that broke the first version of the auditor."""

import time

from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from ..fetcher.conftest import make_config
from .conftest import AuditEnv, audit_config

PAGE = (
    '<!DOCTYPE html><html lang="en"><head><title>{title}</title>{head}</head>'
    "<body><main><h1>{title}</h1><img src=x.png></main>{body}</body></html>"
)


def page(title: str, head: str = "", body: str = "") -> str:
    return PAGE.format(title=title, head=head, body=body)


def serve(httpserver: HTTPServer) -> None:
    httpserver.expect_request("/robots.txt").respond_with_data("", content_type="text/plain")
    httpserver.expect_request("/").respond_with_data(
        page(
            "Home",
            body='<a href="/leaves.html">a</a><a href="/amd.html">b</a><a href="/slow.html">c</a>'
            '<a href="/no-eval.html">d</a>',
        ),
        content_type="text/html",
    )
    # Replaces window.eval and window.axe (seen on pred.telangana.gov.in/assets.php).
    httpserver.expect_request("/no-eval.html").respond_with_data(
        page(
            "No eval",
            head="<script>window.eval = function () { throw new SyntaxError('nope'); };"
            "window.Function = function () { throw new SyntaxError('nope'); };</script>",
        ),
        content_type="text/html",
    )
    # Navigates away as soon as it loads (seen on schooledu.telangana.gov.in).
    httpserver.expect_request("/leaves.html").respond_with_data(
        page("Leaves", body="<script>location.href = '/login.html';</script>"),
        content_type="text/html",
    )
    # A RequireJS-style loader on the page.
    httpserver.expect_request("/amd.html").respond_with_data(
        page(
            "AMD",
            head="<script>window.define = function () {}; window.define.amd = {};"
            "window.module = {exports: {}};</script>",
        ),
        content_type="text/html",
    )
    # A script at the end of the body that takes longer than the load budget.
    httpserver.expect_request("/slow.html").respond_with_data(
        page("Slow", body='<script src="/slow.js"></script>'), content_type="text/html"
    )

    def slow(_request: Request) -> Response:
        time.sleep(7)
        return Response("/* late */", content_type="text/javascript")

    httpserver.expect_request("/slow.js").respond_with_handler(slow)


def test_awkward_pages_are_still_audited(env: AuditEnv, httpserver: HTTPServer) -> None:
    serve(httpserver)
    env.crawl_then_clear(httpserver)
    report = env.audit(
        crawl=make_config(request_timeout_seconds=30),
        audit=audit_config(page_timeout_seconds=0.5, max_requests_per_page=1),
    )
    pages = {p.url.path: p for p in report.pages}
    assert set(pages) == {"/", "/leaves.html", "/amd.html", "/slow.html", "/no-eval.html"}
    assert all(p.ok for p in pages.values()), {k: p.error for k, p in pages.items()}

    # Every page has the same known failure (image without alt), so axe really ran.
    failing = {f.url.path for f in report.findings if f.check_id == "image-alt"}
    assert failing == set(pages)

    assert report.blocked["navigation"] == 1
    assert pages["/slow.html"].error is not None
    assert pages["/slow.html"].error.startswith("load event timed out")
    assert pages["/"].error is None
