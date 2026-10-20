import itertools
from datetime import timedelta

from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from agentready.core import CrawlStatus
from agentready.fetcher import StopReason
from agentready.store import list_links

from .conftest import UA, Env, html, make_config, requested_paths

PDF = b"%PDF-1.4\n" + b"0" * 2000


def serve_site(httpserver: HTTPServer, robots: str = "User-agent: *\nDisallow:\n") -> None:
    httpserver.expect_request("/robots.txt").respond_with_data(robots, content_type="text/plain")
    httpserver.expect_request("/").respond_with_data(
        html(
            "/a.html",
            "/docs/go-12.pdf",
            "/logo.png",
            "mailto:info@telangana.gov.in",
            "http://other.example/elsewhere.pdf",
            "/a.html#section",
        ),
        content_type="text/html; charset=utf-8",
    )
    httpserver.expect_request("/a.html").respond_with_data(
        html("/", "/c.html"), content_type="text/html"
    )
    httpserver.expect_request("/c.html").respond_with_data(html(), content_type="text/html")
    httpserver.expect_request("/docs/go-12.pdf").respond_with_data(
        PDF, content_type="application/pdf"
    )


# --- discovery and storage ------------------------------------------------------


def test_crawl_fetches_in_scope_pages_and_documents(env: Env, httpserver: HTTPServer) -> None:
    serve_site(httpserver)
    report = env.crawl()

    assert sorted(requested_paths(httpserver)) == [
        "/",
        "/a.html",
        "/c.html",
        "/docs/go-12.pdf",
        "/robots.txt",
    ]
    assert report.run.status is CrawlStatus.FINISHED
    assert report.run.stop_reason == StopReason.COMPLETED
    assert report.run.pages_fetched == 4  # robots.txt is not counted
    assert report.run.error_count == 0
    assert report.stored_new == 5  # 4 pages + robots.txt

    pdf = env.by_path()["/docs/go-12.pdf"]
    assert pdf.status_code == 200
    assert pdf.content_type == "application/pdf"
    assert pdf.crawl_run_id == report.run.id
    assert pdf.sha256 is not None
    assert env.raw.get(pdf.sha256) == PDF


def test_links_are_recorded_including_out_of_scope_documents(
    env: Env, httpserver: HTTPServer
) -> None:
    serve_site(httpserver)
    report = env.crawl()
    assert report.run.id is not None
    home = env.by_path()["/"]
    links = {
        str(link.url): link
        for link in list_links(env.conn, report.run.id, from_resource_id=home.id)
    }

    base = httpserver.url_for("/")
    assert set(links) == {
        f"{base}a.html",
        f"{base}docs/go-12.pdf",
        f"{base}logo.png",
        "http://other.example/elsewhere.pdf",
    }  # mailto dropped; the #section duplicate of a.html merged
    assert links["http://other.example/elsewhere.pdf"].is_document
    assert not links["http://other.example/elsewhere.pdf"].in_scope
    assert links[f"{base}docs/go-12.pdf"].is_document
    assert links[f"{base}a.html"].in_scope
    assert not links[f"{base}logo.png"].in_scope  # unwanted file type
    assert len(list_links(env.conn, report.run.id, documents_only=True)) == 2


def test_user_agent_identifies_the_bot(env: Env, httpserver: HTTPServer) -> None:
    serve_site(httpserver)
    env.crawl()
    agents = {request.headers.get("User-Agent") for request, _ in httpserver.log}
    assert agents == {UA}


# --- politeness -----------------------------------------------------------------


def test_delay_between_requests_is_respected(env: Env, httpserver: HTTPServer) -> None:
    serve_site(httpserver)
    env.crawl()
    times = [r.fetched_at for r in env.resources()]
    gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(times)]
    assert len(gaps) == 4
    assert all(gap >= 5 for gap in gaps), gaps


def test_robots_crawl_delay_is_honoured_when_longer(env: Env, httpserver: HTTPServer) -> None:
    serve_site(httpserver, robots="User-agent: *\nCrawl-delay: 20\n")
    env.crawl()
    times = [r.fetched_at for r in env.resources()]
    gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(times)]
    assert all(gap >= 20 for gap in gaps), gaps


def test_robots_disallow_is_obeyed(env: Env, httpserver: HTTPServer) -> None:
    serve_site(httpserver, robots="User-agent: *\nDisallow: /docs/\nDisallow: /c.html\n")
    report = env.crawl()
    paths = requested_paths(httpserver)
    assert "/docs/go-12.pdf" not in paths
    assert "/c.html" not in paths
    assert report.skipped["robots_disallowed"] == 2


def test_robots_that_blocks_us_stops_everything(env: Env, httpserver: HTTPServer) -> None:
    serve_site(
        httpserver, robots="User-agent: *\nAllow: /\n\nUser-agent: agentready-bot\nDisallow: /\n"
    )
    report = env.crawl()
    assert requested_paths(httpserver) == ["/robots.txt"]
    assert report.run.pages_fetched == 0
    assert report.skipped["robots_disallowed"] == 1


def test_robots_server_error_means_disallow_all(env: Env, httpserver: HTTPServer) -> None:
    httpserver.expect_request("/robots.txt").respond_with_data("down", status=503)
    report = env.crawl()
    assert set(requested_paths(httpserver)) == {"/robots.txt"}  # retried, nothing else
    assert report.robots_status == 503
    assert report.run.pages_fetched == 0


def test_missing_robots_means_allow_all(env: Env, httpserver: HTTPServer) -> None:
    httpserver.expect_request("/robots.txt").respond_with_data("not found", status=404)
    httpserver.expect_request("/").respond_with_data(html(), content_type="text/html")
    report = env.crawl()
    assert report.robots_status == 404
    assert report.run.pages_fetched == 1
    assert env.by_path()["/robots.txt"].error == "http 404"


def test_outside_crawl_window_sends_no_requests(env: Env, httpserver: HTTPServer) -> None:
    serve_site(httpserver)
    env.clock.current = env.clock.current.replace(hour=8)  # 13:30 IST
    report = env.crawl()
    assert requested_paths(httpserver) == []
    assert report.run.stop_reason == StopReason.OUTSIDE_CRAWL_WINDOW


def test_crawl_stops_when_the_window_closes(env: Env, httpserver: HTTPServer) -> None:
    serve_site(httpserver, robots="User-agent: *\nCrawl-delay: 3600\n")
    env.clock.current = env.clock.current.replace(hour=23, minute=30)  # 05:00 IST
    report = env.crawl()
    # robots.txt, then "/" an hour later at 06:00 IST: the window has closed.
    assert report.run.stop_reason == StopReason.OUTSIDE_CRAWL_WINDOW
    assert "/a.html" not in requested_paths(httpserver)


def test_page_cap_stops_the_run(env: Env, httpserver: HTTPServer) -> None:
    serve_site(httpserver)
    report = env.crawl(max_pages=2)
    assert report.run.pages_fetched == 2
    assert report.run.stop_reason == StopReason.PAGE_CAP_REACHED


def test_depth_limit_still_fetches_documents_one_hop_deeper(
    env: Env, httpserver: HTTPServer
) -> None:
    httpserver.expect_request("/robots.txt").respond_with_data("", content_type="text/plain")
    httpserver.expect_request("/").respond_with_data(html("/a.html"), content_type="text/html")
    httpserver.expect_request("/a.html").respond_with_data(
        html("/b.html", "/doc.pdf"), content_type="text/html"
    )
    httpserver.expect_request("/doc.pdf").respond_with_data(PDF, content_type="application/pdf")
    env.crawl(make_config(max_depth=1))
    paths = requested_paths(httpserver)
    assert "/doc.pdf" in paths
    assert "/b.html" not in paths


# --- conditional requests -------------------------------------------------------


def test_unchanged_files_return_304_and_are_not_stored_again(
    env: Env, httpserver: HTTPServer
) -> None:
    def home(request: Request) -> Response:
        if request.headers.get("If-None-Match") == '"v1"':
            return Response(status=304, headers={"ETag": '"v1"'})
        return Response(html("/a.html"), content_type="text/html", headers={"ETag": '"v1"'})

    def page_a(request: Request) -> Response:
        stamp = "Tue, 20 Oct 2026 10:00:00 GMT"
        if request.headers.get("If-Modified-Since") == stamp:
            return Response(status=304)
        return Response(html(), content_type="text/html", headers={"Last-Modified": stamp})

    httpserver.expect_request("/robots.txt").respond_with_data("", content_type="text/plain")
    httpserver.expect_request("/").respond_with_handler(home)
    httpserver.expect_request("/a.html").respond_with_handler(page_a)

    first = env.crawl()
    files_after_first = env.stored_files()
    env.clock.current += timedelta(minutes=10)
    second = env.crawl()

    assert env.stored_files() == files_after_first  # nothing new written
    assert second.stored_new == 0
    assert second.not_modified == 2

    before, after = env.by_path(first.run.id), env.by_path(second.run.id)
    for path in ("/", "/a.html"):
        assert after[path].status_code == 304
        assert after[path].sha256 == before[path].sha256
        assert after[path].raw_path == before[path].raw_path
        assert after[path].content_type == before[path].content_type
    # Links still come from the stored copy, so /a.html was reached in the second run.
    assert second.run.pages_fetched == 2


def test_changed_file_is_stored_as_new_content(env: Env, httpserver: HTTPServer) -> None:
    versions = iter([b"<html>v1</html>", b"<html>v2</html>"])
    httpserver.expect_request("/robots.txt").respond_with_data("", content_type="text/plain")
    httpserver.expect_request("/").respond_with_handler(
        lambda _r: Response(next(versions), content_type="text/html", headers={"ETag": '"x"'})
    )
    env.crawl()
    second = env.crawl()
    assert second.stored_new == 1
    assert second.not_modified == 0


# --- errors are recorded, never fatal --------------------------------------------


def test_http_errors_are_recorded_and_retried(env: Env, httpserver: HTTPServer) -> None:
    httpserver.expect_request("/robots.txt").respond_with_data("", content_type="text/plain")
    httpserver.expect_request("/").respond_with_data(
        html("/missing.html", "/broken.html", "/ok.html"), content_type="text/html"
    )
    httpserver.expect_request("/missing.html").respond_with_data("no", status=404)
    httpserver.expect_request("/broken.html").respond_with_data("oops", status=500)
    httpserver.expect_request("/ok.html").respond_with_data(html(), content_type="text/html")

    report = env.crawl()
    rows = env.by_path()
    assert rows["/missing.html"].status_code == 404
    assert rows["/missing.html"].error == "http 404"
    assert rows["/missing.html"].sha256 is None
    assert rows["/broken.html"].status_code == 500
    assert rows["/broken.html"].error == "http 500 after 3 attempts"
    assert requested_paths(httpserver).count("/broken.html") == 3
    assert requested_paths(httpserver).count("/missing.html") == 1  # 4xx: no retry
    assert rows["/ok.html"].status_code == 200  # the run carried on
    assert report.run.error_count == 2
    assert report.run.status is CrawlStatus.FINISHED
    assert 10 in env.clock.sleeps and 20 in env.clock.sleeps  # exponential backoff


def test_retry_after_is_honoured(env: Env, httpserver: HTTPServer) -> None:
    calls = iter([Response("busy", status=503, headers={"Retry-After": "120"})])
    httpserver.expect_request("/robots.txt").respond_with_data("", content_type="text/plain")
    httpserver.expect_request("/").respond_with_handler(
        lambda _r: next(calls, Response(html(), content_type="text/html"))
    )
    env.crawl()
    assert 120 in env.clock.sleeps
    assert env.by_path()["/"].status_code == 200


def test_oversized_file_is_not_stored(env: Env, httpserver: HTTPServer) -> None:
    httpserver.expect_request("/robots.txt").respond_with_data("", content_type="text/plain")
    httpserver.expect_request("/").respond_with_data(html("/big.pdf"), content_type="text/html")
    httpserver.expect_request("/big.pdf").respond_with_data(
        b"%PDF" + b"0" * 20_000, content_type="application/pdf"
    )
    env.crawl(make_config(max_file_mb=0.01))  # about 10 KB
    big = env.by_path()["/big.pdf"]
    assert big.error is not None and big.error.startswith("file too large")
    assert big.sha256 is None


def test_unwanted_content_type_is_not_stored(env: Env, httpserver: HTTPServer) -> None:
    httpserver.expect_request("/robots.txt").respond_with_data("", content_type="text/plain")
    httpserver.expect_request("/").respond_with_data(html("/banner"), content_type="text/html")
    httpserver.expect_request("/banner").respond_with_data(b"\x89PNG", content_type="image/png")
    env.crawl()
    banner = env.by_path()["/banner"]
    assert banner.error == "content type not kept: image/png"
    assert banner.sha256 is None


# --- redirects ------------------------------------------------------------------


def test_same_site_redirect_is_followed_and_final_url_recorded(
    env: Env, httpserver: HTTPServer
) -> None:
    httpserver.expect_request("/robots.txt").respond_with_data("", content_type="text/plain")
    httpserver.expect_request("/").respond_with_data(html("/old"), content_type="text/html")
    httpserver.expect_request("/old").respond_with_data(
        "", status=301, headers={"Location": "/new/"}
    )
    httpserver.expect_request("/new/").respond_with_data(html(), content_type="text/html")
    env.crawl()
    old = env.by_path()["/old"]
    assert old.status_code == 200
    assert str(old.final_url) == httpserver.url_for("/new/")


def test_off_site_redirect_is_not_followed(env: Env, httpserver: HTTPServer) -> None:
    httpserver.expect_request("/robots.txt").respond_with_data("", content_type="text/plain")
    httpserver.expect_request("/").respond_with_data(html("/out"), content_type="text/html")
    httpserver.expect_request("/out").respond_with_data(
        "", status=302, headers={"Location": "https://elsewhere.example/landing"}
    )
    env.crawl()
    out = env.by_path()["/out"]
    assert out.status_code == 302
    assert str(out.final_url) == "https://elsewhere.example/landing"
    assert out.error is not None and out.error.startswith("redirect not followed: out of scope")


def test_redirect_into_disallowed_path_is_not_followed(env: Env, httpserver: HTTPServer) -> None:
    httpserver.expect_request("/robots.txt").respond_with_data(
        "User-agent: *\nDisallow: /private/\n", content_type="text/plain"
    )
    httpserver.expect_request("/").respond_with_data(html("/go"), content_type="text/html")
    httpserver.expect_request("/go").respond_with_data(
        "", status=302, headers={"Location": "/private/x"}
    )
    env.crawl()
    assert "/private/x" not in requested_paths(httpserver)
    assert env.by_path()["/go"].error == "redirect not followed: disallowed by robots.txt"
