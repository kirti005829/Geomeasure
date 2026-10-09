"""Plain data structures shared by the readers, CRS logic and measurement engine.

They are deliberately framework-free (no FastAPI, no SQLAlchemy) so the whole
``app.geo`` package can be unit-tested and reused, e.g. from a CLI or a worker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pyproj import CRS
from shapely.geometry.base import BaseGeometry


class CRSSource(str, Enum):
    """Where a layer's CRS came from - surfaced to clients for transparency."""

    PRJ_FILE = "prj_file"  # read from the shapefile's .prj sidecar
    KML_SPEC = "kml_spec"  # OGC KML 2.2 mandates WGS84 lon/lat
    ASSUMED = "assumed"  # no CRS declared, inferred from coordinate ranges


@dataclass
class RawFeature:
    """A feature exactly as read from the file, still in its source CRS."""

    index_in_layer: int
    source_id: str | None
    geometry: BaseGeometry | None
    properties: dict[str, Any]
    # Original geometry type name when it could not be turned into a shapely
    # geometry (e.g. "MultiPatch", "Model"). Lets us report *what* was skipped.
    unsupported_type: str | None = None
    # Set when the file *declares* a geometry we can parse in principle, but its
    # coordinates are broken (e.g. a ring with two points). Reported as ERROR.
    declared_type: str | None = None
    geometry_error: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class Layer:
    name: str
    crs: CRS | None
    crs_source: CRSSource | None
    features: list[RawFeature]
    bounds: tuple[float, float, float, float] | None = None


@dataclass
class ReadResult:
    layers: list[Layer]
    warnings: list[str] = field(default_factory=list)

    @property
    def feature_count(self) -> int:
        return sum(len(layer.features) for layer in self.layers)
