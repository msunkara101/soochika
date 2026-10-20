from datetime import UTC, datetime
from pathlib import Path

from agentready.core import Site, SiteKind
from agentready.site_directory import load_sites, propose_sites, write_proposals

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "site_directory"
PAGE = (FIXTURE / "state_web_directory.html").read_bytes()
PAGE_URL = "https://www.telangana.gov.in/state-web-directory/"

AGRI = Site(
    id="agri",
    name="Department of Agriculture",
    department="Agriculture and Co-operation",
    base_url="https://agri.telangana.gov.in/",
    kind="department",
    languages=["en", "te"],
)


def by_id(sites: list[Site]) -> dict[str, Site]:
    return {s.id: s for s in sites}


def test_proposes_one_candidate_per_new_host() -> None:
    candidates = propose_sites(PAGE, PAGE_URL, existing=[AGRI])
    assert [s.id for s in candidates] == [
        "ccla",
        "health",
        "cdma",
        "wdcw",
        "hyderabad",
        "e-panchayat",
    ]


def test_skips_existing_portal_out_of_scope_and_non_web_links() -> None:
    hosts = {s.base_url.host for s in propose_sites(PAGE, PAGE_URL, existing=[AGRI])}
    assert "agri.telangana.gov.in" not in hosts  # already in sites.yaml
    assert "www.telangana.gov.in" not in hosts  # the directory page's own host
    assert "rwss.telangana.nic.in" not in hosts  # different domain
    assert "www.india.gov.in" not in hosts
    assert not any("example.com" in h for h in hosts)


def test_existing_sites_are_proposed_when_not_known() -> None:
    assert "agri" in by_id(propose_sites(PAGE, PAGE_URL))


def test_base_url_is_site_root_and_keeps_scheme() -> None:
    sites = by_id(propose_sites(PAGE, PAGE_URL))
    assert str(sites["ccla"].base_url) == "https://ccla.telangana.gov.in/"  # deep link trimmed
    assert str(sites["health"].base_url) == "http://www.health.telangana.gov.in/"
    assert str(sites["cdma"].base_url) == "https://cdma.telangana.gov.in/"  # protocol-relative


def test_names_come_from_link_text_or_title() -> None:
    sites = by_id(propose_sites(PAGE, PAGE_URL))
    assert sites["ccla"].name == "Chief Commissioner of Land Administration"  # first link wins
    assert sites["health"].name == "Health Department"  # empty text, title attribute
    assert sites["cdma"].name == "Commissioner & Director of Municipal Administration"
    assert sites["wdcw"].name == "మహిళా అభివృద్ధి మరియు శిశు సంక్షేమ శాఖ"


def test_kind_guess() -> None:
    sites = by_id(propose_sites(PAGE, PAGE_URL))
    assert sites["hyderabad"].kind is SiteKind.DISTRICT
    assert sites["ccla"].kind is SiteKind.DEPARTMENT


def test_ids_do_not_clash_with_existing_ids() -> None:
    existing = AGRI.model_copy(update={"id": "ccla"})
    sites = propose_sites(PAGE, PAGE_URL, existing=[existing])
    assert "ccla-2" in by_id(sites)


def test_extra_domain_suffixes() -> None:
    suffixes = ["telangana.gov.in", "telangana.nic.in"]
    sites = by_id(propose_sites(PAGE, PAGE_URL, domain_suffixes=suffixes))
    assert str(sites["rwss"].base_url) == "https://rwss.telangana.nic.in/"
    assert "india" not in str([s.base_url for s in sites.values()])


def test_relative_links_without_base_tag() -> None:
    html = '<a href="https://x.telangana.gov.in/">X</a><a href="/about">About</a>'
    sites = propose_sites(html, "https://www.telangana.gov.in/dir/")
    assert [s.id for s in sites] == ["x"]


def test_write_proposals_is_loadable_as_a_site_list(tmp_path: Path) -> None:
    candidates = propose_sites(PAGE, PAGE_URL, existing=[AGRI])
    out = tmp_path / "data" / "proposed_sites.yaml"
    write_proposals(out, candidates, source_url=PAGE_URL, proposed_at=datetime.now(UTC))
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Candidate sites for review. NOT used by the crawler.")
    assert f"# Proposed from: {PAGE_URL}" in text
    assert "మహిళా" in text  # written as UTF-8, not escaped
    assert load_sites(out) == candidates
