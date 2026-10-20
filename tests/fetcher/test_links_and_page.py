import asyncio

import httpx
import pytest
from pytest_httpserver import HTTPServer

from agentready.fetcher import FetchError, extract_links, fetch_page, is_document_url

from .conftest import UA, FakeClock, make_config, requested_paths

PAGE = "https://agri.telangana.gov.in/schemes/index.html"


def urls(html: str, page: str = PAGE) -> list[str]:
    return [link.url for link in extract_links(html.encode(), page)]


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ('<a href="list.html">x</a>', ["https://agri.telangana.gov.in/schemes/list.html"]),
        ('<a href="/gos/1.pdf">x</a>', ["https://agri.telangana.gov.in/gos/1.pdf"]),
        ('<a href="../a.html">x</a>', ["https://agri.telangana.gov.in/a.html"]),
        ('<a href="//transport.telangana.gov.in/">x</a>', ["https://transport.telangana.gov.in/"]),
        (
            '<base href="https://agri.telangana.gov.in/docs/"><a href="x.pdf">x</a>',
            ["https://agri.telangana.gov.in/docs/x.pdf"],
        ),
        (
            '<frameset><frame src="menu.html"><frame src="main.html"></frameset>',
            [
                "https://agri.telangana.gov.in/schemes/menu.html",
                "https://agri.telangana.gov.in/schemes/main.html",
            ],
        ),
        (
            '<iframe src="/embed/notices.html"></iframe>',
            ["https://agri.telangana.gov.in/embed/notices.html"],
        ),
        (
            '<meta http-equiv="Refresh" content="0; URL=\'/home.html\'">',
            ["https://agri.telangana.gov.in/home.html"],
        ),
        (
            '<map><area href="/district.html"></map>',
            ["https://agri.telangana.gov.in/district.html"],
        ),
        (
            '<a href="mailto:a@b">m</a><a href="javascript:void(0)">j</a><a href="#top">t</a>'
            '<a href="">e</a><a>no href</a>',
            ["https://agri.telangana.gov.in/schemes/index.html"],
        ),  # only "#top" (this page)
        (
            '<a href="a.html">A</a><a href="a.html#x">A again</a>',
            ["https://agri.telangana.gov.in/schemes/a.html"],
        ),
    ],
)
def test_extract_links(html: str, expected: list[str]) -> None:
    assert urls(html) == expected


def test_link_text_prefers_the_first_non_empty_text() -> None:
    links = extract_links(
        b'<a href="go.pdf"><img src="pdf.gif"></a><a href="go.pdf"> G.O.Ms.No. <b>12</b> </a>',
        PAGE,
    )
    assert [(link.url.rsplit("/", 1)[1], link.text) for link in links] == [
        ("go.pdf", "G.O.Ms.No. 12")
    ]


def test_link_text_falls_back_to_title() -> None:
    links = extract_links(b'<a href="go.pdf" title="Download GO"></a>', PAGE)
    assert links[0].text == "Download GO"


def test_telugu_urls_are_percent_encoded() -> None:
    [link] = extract_links('<a href="/పత్రాలు/go.pdf">GO</a>'.encode(), PAGE)
    assert (
        link.url
        == "https://agri.telangana.gov.in/%E0%B0%AA%E0%B0%A4%E0%B1%8D%E0%B0%B0%E0%B0%BE%E0%B0%B2%E0%B1%81/go.pdf"
    )


@pytest.mark.parametrize(
    ("url", "is_doc"),
    [
        ("https://x/a.pdf", True),
        ("https://x/a.PDF", True),
        ("https://x/a.docx", True),
        ("https://x/a.xls", True),
        ("https://x/a.html", False),
        ("https://x/a.jpg", False),
        ("https://x/download.php?id=1", False),
    ],
)
def test_is_document_url(url: str, is_doc: bool) -> None:
    assert is_document_url(url) is is_doc


# --- fetch_page (used by `sites propose`) ----------------------------------------


def run_fetch(url: str, **kwargs: object) -> bytes:
    return asyncio.run(
        fetch_page(url, make_config(), user_agent=UA, clock=FakeClock(), **kwargs)  # type: ignore[arg-type]
    )


def test_fetch_page_checks_robots_then_fetches(httpserver: HTTPServer) -> None:
    httpserver.expect_request("/robots.txt").respond_with_data("", content_type="text/plain")
    httpserver.expect_request("/dir/").respond_with_data(
        "<a href=x>x</a>", content_type="text/html"
    )
    assert run_fetch(httpserver.url_for("/dir/")) == b"<a href=x>x</a>"
    assert requested_paths(httpserver) == ["/robots.txt", "/dir/"]


def test_fetch_page_refuses_when_robots_disallows(httpserver: HTTPServer) -> None:
    httpserver.expect_request("/robots.txt").respond_with_data(
        "User-agent: *\nDisallow: /dir/\n", content_type="text/plain"
    )
    with pytest.raises(FetchError, match=r"robots.txt disallows"):
        run_fetch(httpserver.url_for("/dir/"))
    assert requested_paths(httpserver) == ["/robots.txt"]


def test_fetch_page_refuses_outside_window() -> None:
    clock = FakeClock()
    clock.current = clock.current.replace(hour=8)  # 13:30 IST
    with pytest.raises(FetchError, match="outside the crawl window"):
        asyncio.run(
            fetch_page("https://x.telangana.gov.in/", make_config(), user_agent=UA, clock=clock)
        )


def test_fetch_page_reports_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404 if request.url.path == "/robots.txt" else 500)

    with pytest.raises(FetchError, match="http 500 after 3 attempts"):
        run_fetch("https://x.telangana.gov.in/dir/", transport=httpx.MockTransport(handler))
