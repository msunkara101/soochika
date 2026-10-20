from pathlib import Path

import pytest

from agentready.core import Language, SiteKind
from agentready.site_directory import SiteListError, load_sites

REPO_SITES = Path(__file__).resolve().parents[2] / "config" / "sites.yaml"

GOOD = """\
sites:
  - id: agri
    name: Department of Agriculture
    department: Agriculture and Co-operation
    base_url: https://agri.telangana.gov.in/
    kind: department
    languages: [en, te]
  - id: hyderabad
    name: Hyderabad District
    department: Revenue
    base_url: https://hyderabad.telangana.gov.in/
    kind: district
    languages: [te, en, ur]
    active: false
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sites.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def problems(tmp_path: Path, text: str) -> list[str]:
    with pytest.raises(SiteListError) as info:
        load_sites(write(tmp_path, text))
    return info.value.problems


def test_loads_valid_sites(tmp_path: Path) -> None:
    sites = load_sites(write(tmp_path, GOOD))
    assert [s.id for s in sites] == ["agri", "hyderabad"]
    assert sites[1].kind is SiteKind.DISTRICT
    assert sites[1].languages == [Language.TE, Language.EN, Language.UR]
    assert sites[1].active is False


def test_repository_sites_file_is_valid() -> None:
    sites = load_sites(REPO_SITES)
    assert len(sites) >= 5
    assert all(s.kind is SiteKind.DEPARTMENT for s in sites[:5])


def test_empty_file_means_no_sites(tmp_path: Path) -> None:
    assert load_sites(write(tmp_path, "")) == []
    assert load_sites(write(tmp_path, "sites:\n")) == []


def test_duplicate_ids_are_reported(tmp_path: Path) -> None:
    text = GOOD.replace("id: hyderabad", "id: agri").replace("hyderabad.telangana", "hyd.telangana")
    assert problems(tmp_path, text) == ["duplicate id 'agri'"]


def test_duplicate_hosts_are_reported(tmp_path: Path) -> None:
    text = GOOD.replace("https://hyderabad.telangana.gov.in/", "http://www.agri.telangana.gov.in/x")
    assert problems(tmp_path, text) == [
        "'hyderabad' and 'agri' have the same host agri.telangana.gov.in"
    ]


@pytest.mark.parametrize(
    "bad_url",
    ["agri.telangana.gov.in", "ftp://agri.telangana.gov.in/", "https://", "not a url"],
)
def test_malformed_urls_name_the_site_and_field(tmp_path: Path, bad_url: str) -> None:
    found = problems(tmp_path, GOOD.replace("https://agri.telangana.gov.in/", bad_url))
    assert len(found) == 1
    assert found[0].startswith("sites[0] (id 'agri'): base_url: ")
    assert repr(bad_url) in found[0]


def test_every_problem_is_reported_at_once(tmp_path: Path) -> None:
    text = (
        GOOD.replace("kind: department", "kind: ministry")
        .replace("languages: [te, en, ur]", "languages: [te, fr]")
        .replace("    name: Hyderabad District\n", "")
    )
    found = problems(tmp_path, text)
    assert any(p.startswith("sites[0] (id 'agri'): kind: ") for p in found)
    assert any(p.startswith("sites[1] (id 'hyderabad'): languages.1: ") for p in found)
    assert "sites[1] (id 'hyderabad'): name: Field required" in found


def test_error_message_lists_problems(tmp_path: Path) -> None:
    path = write(tmp_path, GOOD.replace("id: agri", "id: Agri Dept"))
    with pytest.raises(SiteListError, match=r"has 1 problem\(s\):\n  - sites\[0\]"):
        load_sites(path)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("- id: agri\n", "top level must be a mapping with a single 'sites' key"),
        ("sites: {}\nextra: 1\n", "top level must be a mapping with a single 'sites' key"),
        ("sites: agri\n", "'sites' must be a list"),
        ("sites:\n  - agri\n", "sites[0]: must be a mapping"),
        ("sites: [unclosed\n", "invalid YAML"),
    ],
)
def test_bad_structure(tmp_path: Path, text: str, expected: str) -> None:
    found = problems(tmp_path, text)
    assert found[0].startswith(expected)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SiteListError, match="file not found"):
        load_sites(tmp_path / "nope.yaml")
