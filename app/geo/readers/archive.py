"""Defensive ZIP handling.

Uploaded archives are untrusted input. Rather than extracting to disk (and
having to defend against zip-slip path traversal), members are read straight
into memory after the archive passes a few cheap sanity checks:

* the declared uncompressed size of all members stays under a limit
  (protects against "zip bombs" such as 42.zip),
* the per-member compression ratio is plausible,
* the number of entries is bounded.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.geo.exceptions import InvalidArchiveError, UnsafeArchiveError

# Real shapefiles compress well, but nothing legitimate compresses 1000:1.
MAX_COMPRESSION_RATIO = 1000


@dataclass(frozen=True)
class ArchiveLimits:
    max_uncompressed_bytes: int = 500 * 1024 * 1024
    max_entries: int = 1000


def _is_junk(name: str) -> bool:
    """Skip OS metadata that commonly sneaks into user-made zips."""
    path = PurePosixPath(name)
    return (
        name.endswith("/")
        or path.parts[0] == "__MACOSX"
        or path.name.startswith("._")
        or path.name in {".DS_Store", "Thumbs.db"}
    )


class SafeZip:
    """Read-only, in-memory view over a validated ZIP archive."""

    def __init__(self, data: bytes, limits: ArchiveLimits | None = None):
        limits = limits or ArchiveLimits()
        try:
            self._zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise InvalidArchiveError("File is not a valid ZIP archive.") from exc

        infos = self._zf.infolist()
        if len(infos) > limits.max_entries:
            raise UnsafeArchiveError(f"Archive has {len(infos)} entries; the limit is {limits.max_entries}.")

        total = 0
        for info in infos:
            total += info.file_size
            if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                raise UnsafeArchiveError(f"Entry '{info.filename}' has a suspicious compression ratio.")
        if total > limits.max_uncompressed_bytes:
            raise UnsafeArchiveError(
                "Archive expands beyond the allowed uncompressed size "
                f"({limits.max_uncompressed_bytes // (1024 * 1024)} MB)."
            )

        # lower-cased path -> real member name (shapefile extensions vary in case)
        self._members = {
            info.filename.lower(): info.filename for info in infos if not _is_junk(info.filename)
        }

    def names(self) -> list[str]:
        """Lower-cased member names, junk removed, sorted for determinism."""
        return sorted(self._members)

    def read(self, lower_name: str) -> bytes | None:
        real = self._members.get(lower_name)
        if real is None:
            return None
        try:
            return self._zf.read(real)
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:  # CRC errors, encryption
            raise InvalidArchiveError(f"Could not read '{real}' from archive: {exc}") from exc

    def find(self, extension: str) -> list[str]:
        return [n for n in self.names() if n.endswith(extension)]
