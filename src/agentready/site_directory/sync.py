"""Mirror the reviewed site list into the database."""

import sqlite3
from dataclasses import dataclass, field

from agentready.core import Site
from agentready.store import list_sites, upsert_site


@dataclass(frozen=True)
class SyncResult:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)
    """In the database but no longer in sites.yaml. Kept (with history) but marked inactive."""


def sync_sites(conn: sqlite3.Connection, sites: list[Site]) -> SyncResult:
    stored = {site.id: site for site in list_sites(conn)}
    result = SyncResult()
    for site in sites:
        previous = stored.get(site.id)
        if previous is None:
            result.added.append(site.id)
        elif previous == site:
            result.unchanged.append(site.id)
            continue
        else:
            result.updated.append(site.id)
        upsert_site(conn, site)

    listed = {site.id for site in sites}
    for site_id, site in stored.items():
        if site_id not in listed and site.active:
            upsert_site(conn, site.model_copy(update={"active": False}))
            result.deactivated.append(site_id)
    return result
