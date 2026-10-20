"""Content-addressed raw file storage: ``<data_dir>/raw/<sha256[:2]>/<sha256>``."""

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class StoredFile:
    sha256: str
    raw_path: str
    """Path relative to the data directory, with forward slashes, e.g. ``raw/ab/ab12...``."""
    size: int
    created: bool
    """False when an identical file was already stored."""


class RawStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)

    def put(self, content: bytes) -> StoredFile:
        """Store ``content`` once. Writing the same bytes again is a no-op."""
        sha256 = hashlib.sha256(content).hexdigest()
        raw_path = self.raw_path_for(sha256)
        target = self.data_dir / raw_path
        if target.exists():
            return StoredFile(sha256, raw_path, len(content), created=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file and rename, so a crash never leaves a partial file.
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(content)
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return StoredFile(sha256, raw_path, len(content), created=True)

    def get(self, sha256: str) -> bytes:
        return self.path(sha256).read_bytes()

    def exists(self, sha256: str) -> bool:
        return self.path(sha256).is_file()

    def path(self, sha256: str) -> Path:
        return self.data_dir / self.raw_path_for(sha256)

    @staticmethod
    def raw_path_for(sha256: str) -> str:
        if not _SHA256.match(sha256):
            raise ValueError(f"not a lowercase sha256 hex digest: {sha256!r}")
        return str(PurePosixPath("raw", sha256[:2], sha256))
