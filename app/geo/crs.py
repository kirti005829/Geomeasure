"""CRS resolution, reprojection and projected-CRS selection.

Why this module exists
----------------------
Latitude/longitude are *angles*, not lengths. One degree of longitude is
~111.3 km at the equator, ~102.3 km at Bengaluru (12.97°N) and 0 km at the
poles, so ``shapely_polygon.area`` on EPSG:4326 coordinates returns
"square degrees" - a number with no physical meaning.

To measure, every geometry is:

1. transformed from its **source CRS** to WGS84 (EPSG:4326) - this also
   handles datum shifts, e.g. NAD83 or Everest/Indian 1975 inputs;
2. transformed from WGS84 into a **local projected CRS in metres**, chosen
   per feature by :func:`select_projection`.

Projection strategy ("local first")
-----------------------------------
* **UTM** when the feature fits inside a UTM zone's comfort range
  (≤ 6° wide, between 80°S and 84°N). UTM is the de-facto standard for
  survey and drone work, its scale error is ≤ 0.04 % inside a zone, and
  the EPSG code (e.g. ``EPSG:32643`` for Bengaluru) lets an analyst
  reproduce the number in QGIS.
* **Lambert Azimuthal Equal-Area** centred on the feature for polygons that
  are too wide or too close to the poles for UTM. An equal-area projection
  preserves area *by construction*, wherever the feature is.
* **Azimuthal Equidistant** centred on the feature for long lines in the same
  situation - it keeps distances from the centre exact and distortion low
  around it.

Every projected result is cross-checked against an ellipsoidal (geodesic)
computation in :mod:`app.geo.measure`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import shapely
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError
from shapely.geometry.base import BaseGeometry

from app.geo.exceptions import UnknownCRSError
from app.geo.models import CRSSource, Layer

WGS84 = CRS.from_epsg(4326)
UTM_MAX_WIDTH_DEG = 6.0
UTM_LAT_RANGE = (-80.0, 84.0)


# --------------------------------------------------------------------------- #
# Labels & resolution
# --------------------------------------------------------------------------- #
def crs_label(crs: CRS | None) -> str | None:
    """Prefer a short 'EPSG:xxxx' label; fall back to the CRS's own name.

    ``.prj`` files contain ESRI WKT which usually lacks an authority code, so
    ``to_epsg`` with a confidence threshold is used to identify it.
    """
    if crs is None:
        return None
    epsg = crs.to_epsg(min_confidence=70)
    return f"EPSG:{epsg}" if epsg else crs.name


def _looks_geographic(bounds: tuple[float, float, float, float] | None) -> bool:
    if bounds is None:
        return True  # empty layer - nothing to measure, nothing to get wrong
    minx, miny, maxx, maxy = bounds
    return -180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90


def resolve_layer_crs(layer: Layer, warnings: list[str]) -> CRS:
    """Return the layer's CRS, inferring WGS84 only when it is safe to do so.

    A shapefile without a .prj whose coordinates fit inside lon/lat ranges is
    almost always WGS84 (GPS exports, many web tools). If coordinates fall
    outside those ranges they are projected values in an unknown system and
    *any* guess would silently produce wrong measurements - so we refuse.
    """
    if layer.crs is not None:
        return layer.crs
    if _looks_geographic(layer.bounds):
        layer.crs, layer.crs_source = WGS84, CRSSource.ASSUMED
        warnings.append(
            f"Layer '{layer.name}': no CRS declared; coordinates are within lon/lat "
            "ranges so EPSG:4326 (WGS84) was assumed."
        )
        return WGS84
    raise UnknownCRSError(
        f"Layer '{layer.name}' has no CRS (.prj) and its coordinates are not "
        "longitude/latitude, so they cannot be measured reliably. Include the .prj file."
    )


# --------------------------------------------------------------------------- #
# Transformations
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=256)
def _transformer(src_wkt: str, dst_wkt: str) -> Transformer:
    # always_xy=True: keep (x=lon/easting, y=lat/northing) order regardless of
    # the axis order an authority defines (EPSG:4326 is officially lat, lon).
    return Transformer.from_crs(CRS.from_wkt(src_wkt), CRS.from_wkt(dst_wkt), always_xy=True)


def get_transformer(src: CRS, dst: CRS) -> Transformer:
    return _transformer(src.to_wkt(), dst.to_wkt())


def transform_geometry(geom: BaseGeometry, src: CRS, dst: CRS) -> BaseGeometry:
    if src == dst:
        return geom
    transformer = get_transformer(src, dst)

    def _xy(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1], errcheck=True)
        return np.column_stack([x, y])

    # Vectorised: one PROJ call per geometry instead of one per vertex.
    return shapely.transform(geom, _xy)


def to_wgs84(geom: BaseGeometry, src: CRS) -> BaseGeometry:
    return transform_geometry(geom, src, WGS84)


# --------------------------------------------------------------------------- #
# Projection selection
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProjectionChoice:
    crs: CRS
    label: str  # "EPSG:32643" or a PROJ string for custom projections
    name: str  # human readable, e.g. "WGS 84 / UTM zone 43N"
    reason: str


def utm_epsg(lon: float, lat: float) -> int:
    zone = min(int(math.floor((lon + 180.0) / 6.0)) + 1, 60)
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=128)
def _epsg_crs(code: int) -> CRS:
    return CRS.from_epsg(code)


def select_projection(geom_wgs84: BaseGeometry, *, prefer_equal_area: bool) -> ProjectionChoice:
    minx, miny, maxx, maxy = geom_wgs84.bounds
    lon0, lat0 = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    width = maxx - minx

    fits_utm = width <= UTM_MAX_WIDTH_DEG and UTM_LAT_RANGE[0] <= miny and maxy <= UTM_LAT_RANGE[1]
    if fits_utm:
        code = utm_epsg(lon0, lat0)
        crs = _epsg_crs(code)
        return ProjectionChoice(
            crs=crs,
            label=f"EPSG:{code}",
            name=crs.name,
            reason=f"Feature spans {width:.4f}° of longitude, within a single UTM zone.",
        )

    why = "too wide for one UTM zone" if width > UTM_MAX_WIDTH_DEG else "outside UTM latitude limits"
    proj = "laea" if prefer_equal_area else "aeqd"
    proj4 = f"+proj={proj} +lat_0={lat0:.6f} +lon_0={lon0:.6f} +datum=WGS84 +units=m +no_defs"
    try:
        crs = CRS.from_proj4(proj4)
    except CRSError as exc:  # pragma: no cover - parameters are always valid
        raise UnknownCRSError(f"Could not build local projection: {exc}") from exc
    pretty = "Lambert Azimuthal Equal-Area" if prefer_equal_area else "Azimuthal Equidistant"
    return ProjectionChoice(
        crs=crs,
        label=proj4,
        name=f"{pretty} centred on ({lat0:.4f}, {lon0:.4f})",
        reason=f"Feature is {why}; using a projection centred on the feature.",
    )


# --------------------------------------------------------------------------- #
# Densification
# --------------------------------------------------------------------------- #
# A straight edge is only straight in the CRS it was drawn in. A 40°-long
# polygon edge authored in lon/lat follows a parallel; the same two vertices
# joined in a projected CRS (or on the ellipsoid) describe a different curve.
# Densifying in the *source* CRS before reprojecting keeps the shape the
# author intended, so projected and geodesic results measure the same thing.
# Small features (every survey parcel, every drone flight) are unaffected.
MAX_SEGMENT_GEOGRAPHIC_DEG = 0.01  # ~1.1 km
MAX_SEGMENT_PROJECTED_UNITS = 1000.0  # metres (or feet) in the source CRS


def densify_in_source_crs(geom: BaseGeometry, src: CRS) -> BaseGeometry:
    step = MAX_SEGMENT_GEOGRAPHIC_DEG if src.is_geographic else MAX_SEGMENT_PROJECTED_UNITS
    return shapely.segmentize(geom, max_segment_length=step)
