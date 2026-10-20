import hashlib
from pathlib import Path

import pytest

from agentready.store import RawStore

PDF = b"%PDF-1.4 fake government order"


def stored_files(root: Path) -> list[Path]:
    return [p for p in (root / "raw").rglob("*") if p.is_file()]


def test_put_uses_content_addressed_path(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    stored = store.put(PDF)
    sha = hashlib.sha256(PDF).hexdigest()
    assert stored.sha256 == sha
    assert stored.raw_path == f"raw/{sha[:2]}/{sha}"
    assert stored.size == len(PDF)
    assert stored.created is True
    assert (tmp_path / "raw" / sha[:2] / sha).read_bytes() == PDF


def test_identical_file_is_stored_once(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    first = store.put(PDF)
    second = store.put(PDF)
    assert second.created is False
    assert second.raw_path == first.raw_path
    assert stored_files(tmp_path) == [tmp_path / first.raw_path]


def test_different_files_are_stored_separately(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    store.put(PDF)
    store.put(b"<html>other</html>")
    assert len(stored_files(tmp_path)) == 2


def test_get_and_exists(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    sha = store.put(PDF).sha256
    assert store.exists(sha)
    assert store.get(sha) == PDF
    assert not store.exists("0" * 64)


def test_empty_file_can_be_stored(tmp_path: Path) -> None:
    stored = RawStore(tmp_path).put(b"")
    assert stored.sha256 == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize("bad", ["abc", "A" * 64, "../" + "a" * 61])
def test_rejects_invalid_hashes(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError, match="sha256"):
        RawStore(tmp_path).path(bad)
