"""Load and validate the human-reviewed site list (``config/sites.yaml``)."""

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agentready.core import Site


class SiteListError(ValueError):
    """``sites.yaml`` is invalid. ``problems`` holds one readable line per error."""

    def __init__(self, path: Path | str, problems: list[str]) -> None:
        self.path = Path(path)
        self.problems = problems
        lines = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"{path} has {len(problems)} problem(s):\n{lines}")


def load_sites(path: Path) -> list[Site]:
    """Read ``path`` and return its sites, or raise ``SiteListError`` listing every problem."""
    if not path.is_file():
        raise SiteListError(path, ["file not found"])
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SiteListError(path, [f"invalid YAML: {exc}"]) from exc
    return parse_sites(content, source=path)


def parse_sites(content: Any, *, source: Path | str = "sites.yaml") -> list[Site]:
    """Validate already-parsed YAML content: ``{"sites": [ {...}, ... ]}``."""
    if content is None:
        content = {"sites": []}
    if not isinstance(content, dict) or set(content) != {"sites"}:
        raise SiteListError(source, ["top level must be a mapping with a single 'sites' key"])
    entries = content["sites"] or []
    if not isinstance(entries, list):
        raise SiteListError(source, ["'sites' must be a list"])

    problems: list[str] = []
    sites: list[Site] = []
    for index, entry in enumerate(entries):
        label = _label(index, entry)
        if not isinstance(entry, dict):
            problems.append(f"{label}: must be a mapping")
            continue
        try:
            sites.append(Site.model_validate(entry))
        except ValidationError as exc:
            problems.extend(f"{label}: {_describe(error)}" for error in exc.errors())

    problems.extend(_duplicates(sites))
    if problems:
        raise SiteListError(source, problems)
    return sites


def site_host(site: Site) -> str:
    """Lowercased host without a leading ``www.``, used to spot the same site twice."""
    host = (site.base_url.host or "").lower()
    return host.removeprefix("www.")


def _duplicates(sites: Iterable[Site]) -> list[str]:
    problems: list[str] = []
    seen_ids: set[str] = set()
    seen_hosts: dict[str, str] = {}
    for site in sites:
        if site.id in seen_ids:
            problems.append(f"duplicate id '{site.id}'")
        seen_ids.add(site.id)
        host = site_host(site)
        if host in seen_hosts and seen_hosts[host] != site.id:
            problems.append(f"'{site.id}' and '{seen_hosts[host]}' have the same host {host}")
        seen_hosts.setdefault(host, site.id)
    return problems


def _label(index: int, entry: Any) -> str:
    if isinstance(entry, dict) and isinstance(entry.get("id"), str):
        return f"sites[{index}] (id '{entry['id']}')"
    return f"sites[{index}]"


def _describe(error: Any) -> str:
    field = ".".join(str(part) for part in error["loc"]) or "entry"
    value = error.get("input")
    shown = (
        f" (got {value!r})" if error["type"] != "missing" and not isinstance(value, dict) else ""
    )
    return f"{field}: {error['msg']}{shown}"
