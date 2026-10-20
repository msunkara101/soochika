"""Agent-ready Telangana: cited, dated text from public government websites."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agentready")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0"
