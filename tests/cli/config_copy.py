import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER_CONTACT = "https://example.org/agentready-bot"


def copy_repo_config(destination: Path) -> Path:
    """Copy config/ for a CLI test, with the placeholder contact URL the CLI refuses to crawl with.

    Tests must never be able to reach live sites, whatever the real config contains.
    """
    shutil.copytree(ROOT / "config", destination)
    crawl = destination / "crawl.yaml"
    text = crawl.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^contact_url:.*$", f"contact_url: {PLACEHOLDER_CONTACT}", text)
    crawl.write_text(text, encoding="utf-8")
    return destination
