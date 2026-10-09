"""Domain errors raised while reading or interpreting geospatial files.

Every error here represents a problem with the *uploaded data*, not a bug in
the service. The API layer turns them into 4xx responses and the processing
pipeline stores their message on the file record so the client can see why a
file was rejected.
"""


class GeoFileError(Exception):
    """Base class for all user-facing geospatial file errors."""

    code = "INVALID_GEOSPATIAL_FILE"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class UnsupportedFileTypeError(GeoFileError):
    code = "UNSUPPORTED_FILE_TYPE"


class InvalidArchiveError(GeoFileError):
    code = "INVALID_ARCHIVE"


class UnsafeArchiveError(GeoFileError):
    """Zip bombs, absurd entry counts and similar hostile archives."""

    code = "UNSAFE_ARCHIVE"


class InvalidShapefileError(GeoFileError):
    code = "INVALID_SHAPEFILE"


class InvalidKMLError(GeoFileError):
    code = "INVALID_KML"


class UnknownCRSError(GeoFileError):
    code = "UNKNOWN_CRS"
