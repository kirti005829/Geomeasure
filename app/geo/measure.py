"""Measurement engine.

For every feature two independent measurements are produced:

* **Projected (planar)** - the geometry is projected into the local metric CRS
  picked by :func:`app.geo.crs.select_projection` and measured with planar
  maths. This is the primary value and follows the brief's requirement.
* **Geodesic (ellipsoidal)** - computed directly on the WGS84 ellipsoid with
  Karney's algorithms (``pyproj.Geod``, the same maths GeographicLib uses).
  It needs no projection at all and is the "ground truth" the projected value
  is checked against.

The relative difference between the two is returned with every feature.
Inside a UTM zone the scale factor k ranges from 0.9996 on the central
meridian to ~1.001 at the zone edge, so lengths differ by up to ~0.1 % and
areas (k²) by up to ~0.2 %. Anything much larger points to a CRS problem in
the input (e.g. a wrong .prj) - something a single number could never show.

Callers who need survey-grade "true ground" values can ask the API for the
geodesic numbers as the primary result (``?method=geodesic``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import shapely
from pyproj import Geod
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.validation import explain_validity

from app.geo.crs import WGS84, select_projection, transform_geometry

GEOD = Geod(ellps="WGS84")


class MeasurementStatus(str, Enum):
    MEASURED = "MEASURED"  # area and/or length computed
    NOT_APPLICABLE = "NOT_APPLICABLE"  # points: nothing to measure
    UNSUPPORTED = "UNSUPPORTED"  # geometry type we cannot measure (e.g. MultiPatch)
    NO_GEOMETRY = "NO_GEOMETRY"  # null/empty geometry
    ERROR = "ERROR"  # geometry present but measurement failed


@dataclass
class MeasurementResult:
    status: MeasurementStatus
    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    geodesic_area_m2: float | None = None
    geodesic_perimeter_m: float | None = None
    geodesic_length_m: float | None = None
    projected_crs: str | None = None
    projected_crs_name: str | None = None
    projection_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def difference_pct(self) -> float | None:
        """Projected vs geodesic, relative to geodesic, in percent."""
        pairs = [(self.area_m2, self.geodesic_area_m2), (self.length_m, self.geodesic_length_m)]
        for planar, geodesic in pairs:
            if planar is not None and geodesic:
                return (planar - geodesic) / geodesic * 100.0
        return None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _parts(geom: BaseGeometry) -> list[BaseGeometry]:
    if hasattr(geom, "geoms"):
        out: list[BaseGeometry] = []
        for g in geom.geoms:
            out.extend(_parts(g))
        return out
    return [geom]


def _split(geom: BaseGeometry) -> tuple[MultiPolygon | None, MultiLineString | None, int]:
    """Split any geometry into its polygonal and lineal components."""
    polys, lines, points = [], [], 0
    for part in _parts(geom):
        if part.is_empty:
            continue
        if isinstance(part, Polygon):
            polys.append(part)
        elif isinstance(part, LineString):  # includes LinearRing
            lines.append(LineString(part.coords))
        else:
            points += 1
    return (
        MultiPolygon(polys) if polys else None,
        MultiLineString(lines) if lines else None,
        points,
    )


def _geodesic_area_perimeter(polys: MultiPolygon) -> tuple[float, float]:
    # pyproj sums rings with sign by orientation; orient() guarantees the
    # exterior is counter-clockwise and holes clockwise so holes subtract.
    area = sum(abs(GEOD.geometry_area_perimeter(orient(p, sign=1.0))[0]) for p in polys.geoms)
    # geometry_area_perimeter only reports the exterior ring's perimeter;
    # measure every ring (holes included) to match the planar definition.
    perimeter = GEOD.geometry_length(polys.boundary)
    return area, perimeter


def _clean(geom: BaseGeometry, notes: list[str]) -> BaseGeometry:
    if shapely.has_z(geom):
        geom = shapely.force_2d(geom)
        notes.append("Z values ignored; measurements are 2D (planimetric).")
    if not geom.is_valid:
        reason = explain_validity(geom)
        geom = shapely.make_valid(geom)
        notes.append(f"Invalid geometry repaired before measuring ({reason}).")
    return geom


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def measure_geometry(
    geom_wgs84: BaseGeometry | None,
    unsupported_type: str | None = None,
    geometry_error: str | None = None,
) -> MeasurementResult:
    if geometry_error:
        return MeasurementResult(MeasurementStatus.ERROR, notes=[geometry_error])
    if geom_wgs84 is None:
        if unsupported_type:
            return MeasurementResult(
                MeasurementStatus.UNSUPPORTED,
                notes=[f"Geometry type '{unsupported_type}' is not supported for measurement."],
            )
        return MeasurementResult(MeasurementStatus.NO_GEOMETRY, notes=["Feature has no geometry."])
    if geom_wgs84.is_empty:
        return MeasurementResult(MeasurementStatus.NO_GEOMETRY, notes=["Geometry is empty."])

    notes: list[str] = []
    geom = _clean(geom_wgs84, notes)
    polys, lines, n_points = _split(geom)

    if polys is None and lines is None:
        if n_points:
            return MeasurementResult(
                MeasurementStatus.NOT_APPLICABLE,
                notes=notes + ["Point geometries have no area or length."],
            )
        return MeasurementResult(MeasurementStatus.NO_GEOMETRY, notes=notes + ["Nothing measurable."])

    if polys is not None and lines is not None:
        notes.append("Mixed collection: area from polygon parts, length from line parts.")
    if n_points and (polys is not None or lines is not None):
        notes.append(f"{n_points} point part(s) ignored.")

    # Equal-area fallback for polygons, equidistant fallback for pure lines.
    choice = select_projection(geom, prefer_equal_area=polys is not None)
    result = MeasurementResult(
        MeasurementStatus.MEASURED,
        projected_crs=choice.label,
        projected_crs_name=choice.name,
        projection_reason=choice.reason,
        notes=notes,
    )

    if polys is not None:
        projected = transform_geometry(polys, WGS84, choice.crs)
        result.area_m2 = projected.area
        result.perimeter_m = projected.length  # exterior + interior rings
        result.geodesic_area_m2, result.geodesic_perimeter_m = _geodesic_area_perimeter(polys)
    if lines is not None:
        projected = transform_geometry(lines, WGS84, choice.crs)
        result.length_m = projected.length
        result.geodesic_length_m = GEOD.geometry_length(lines)

    return result
