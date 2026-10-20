import importlib

import pytest
from typer.testing import CliRunner

from agentready import __version__
from agentready.cli import app

MODULES = [
    "core",
    "store",
    "crawl_policy",
    "extract_html",
    "extract_pdf",
    "ocr",
    "site_directory",
    "fetcher",
    "normalizer",
    "change_detector",
    "a11y_auditor",
    "agent_auditor",
    "index",
    "scorecard",
    "fixkit",
    "query_api",
    "mcp_server",
    "web_app",
    "cli",
]


def test_version_command_prints_version() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
    assert __version__ != "0.0.0"


@pytest.mark.parametrize("name", MODULES)
def test_module_package_imports(name: str) -> None:
    importlib.import_module(f"agentready.{name}")
