import pytest

from agentready.core import CrawlConfig


def make_config(**overrides: object) -> CrawlConfig:
    data: dict[str, object] = {
        "contact_url": "https://example.org/bot",
        "allowed_content_types": [
            "text/html",
            "application/xhtml+xml",
            "application/pdf",
            "application/msword",
        ],
        "crawl_window": {"start": "22:00", "end": "06:00", "timezone": "Asia/Kolkata"},
    }
    return CrawlConfig.model_validate(data | overrides)


@pytest.fixture
def config() -> CrawlConfig:
    return make_config()
