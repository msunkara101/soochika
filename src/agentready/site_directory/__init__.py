"""Loading and validating config/sites.yaml; proposing new sites for review."""

from agentready.site_directory.loader import SiteListError, load_sites, parse_sites, site_host
from agentready.site_directory.propose import (
    DEFAULT_DOMAIN_SUFFIXES,
    propose_sites,
    write_proposals,
)
from agentready.site_directory.sync import SyncResult, sync_sites

__all__ = [
    "DEFAULT_DOMAIN_SUFFIXES",
    "SiteListError",
    "SyncResult",
    "load_sites",
    "parse_sites",
    "propose_sites",
    "site_host",
    "sync_sites",
    "write_proposals",
]
