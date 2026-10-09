"""Format detection and dispatch.

The extension tells us what the client *claims* the file is; the first bytes
(magic number) tell us what it actually is. Both must agree before a reader
runs, so a renamed ``.exe`` or an HTML error page saved as ``.kml`` is
rejected with a clear message instead of a confusing parser traceback.
"""

from __future__ import annotations

from enum import Enum
from pathlib import PurePath

from app.geo.exceptions import InvalidArchiveError, InvalidKMLError, UnsupportedFileTypeError
from app.geo.models import ReadResult
from app.geo.readers.archive import ArchiveLimits, SafeZip
from app.geo.readers.kml_reader import read_kml_bytes, read_kmz
from app.geo.readers.shapefile_reader import read_shapefile_zip

ZIP_MAGIC = b"PK\x03\x04"


class FileKind(str, Enum):
    SHAPEFILE = "shapefile"
    KML = "kml"
    KMZ = "kmz"


SUPPORTED_EXTENSIONS = {".zip": FileKind.SHAPEFILE, ".kml": FileKind.KML, ".kmz": FileKind.KMZ}


def detect_kind(filename: str) -> FileKind:
    ext = PurePath(filename).suffix.lower()
    if ext == ".shp":
        raise UnsupportedFileTypeError(
            "A bare .shp cannot be processed on its own: its attributes (.dbf), "
            "index (.shx) and CRS (.prj) live in sidecar files. Upload them together as a .zip."
        )
    try:
        return SUPPORTED_EXTENSIONS[ext]
    except KeyError:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise UnsupportedFileTypeError(
            f"Unsupported file type '{ext or '(none)'}'. Allowed: {allowed}."
        ) from None


def read_geofile(kind: FileKind, data: bytes, limits: ArchiveLimits | None = None) -> ReadResult:
    if kind is FileKind.KML:
        if data.startswith(ZIP_MAGIC):
            # Some tools save KMZ with a .kml extension.
            return read_kmz(SafeZip(data, limits))
        head = data[:1024].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        if not head.startswith(b"<"):
            raise InvalidKMLError("File does not look like XML/KML.")
        return read_kml_bytes(data)

    if not data.startswith(ZIP_MAGIC):
        raise InvalidArchiveError(f"A {kind.value} upload must be a ZIP archive.")
    archive = SafeZip(data, limits)
    if kind is FileKind.KMZ:
        return read_kmz(archive)
    # A .zip may also be a renamed KMZ; be forgiving about it.
    if not archive.find(".shp") and archive.find(".kml"):
        return read_kmz(archive)
    return read_shapefile_zip(archive)


__all__ = [
    "ArchiveLimits",
    "FileKind",
    "SUPPORTED_EXTENSIONS",
    "detect_kind",
    "read_geofile",
]
