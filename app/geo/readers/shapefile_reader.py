"""ESRI Shapefile reader (zipped).

A "shapefile" is really a bundle of sidecar files sharing one stem:

==========  ========  ==================================================
Extension   Required  Purpose
==========  ========  ==================================================
.shp        yes       geometries
.shx        yes*      offsets index into .shp (we can live without it)
.dbf        yes       attribute table (dBase IV)
.prj        no        CRS as ESRI-flavoured WKT - without it, coordinates
                      are just numbers
.cpg        no        text encoding of the .dbf
==========  ========  ==================================================

A single zip may legitimately carry several shapefiles (e.g. ``parcels.shp``
and ``roads.shp``); each one becomes its own *layer*.

``pyshp`` is used instead of GDAL/Fiona: it is pure Python, installs
everywhere with ``pip`` (including Windows), and the format is simple enough
that we do not need GDAL's 200+ drivers for this service.
"""

from __future__ import annotations

import codecs
import datetime as dt
import decimal
import io
from typing import Any

import shapefile  # pyshp
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.geometry import shape as shapely_shape

from app.geo.exceptions import InvalidShapefileError
from app.geo.models import CRSSource, Layer, RawFeature, ReadResult
from app.geo.readers.archive import SafeZip

# Numeric .cpg values are Windows code-page numbers.
_CPG_ALIASES = {"1252": "cp1252", "1251": "cp1251", "65001": "utf-8", "88591": "latin-1"}


def read_shapefile_zip(archive: SafeZip) -> ReadResult:
    shp_files = archive.find(".shp")
    if not shp_files:
        raise InvalidShapefileError(
            "The ZIP does not contain a .shp file. Zip the .shp together with "
            "its .shx, .dbf and .prj sidecar files."
        )

    result = ReadResult(layers=[])
    for shp_name in shp_files:
        stem = shp_name[: -len(".shp")]
        result.layers.append(_read_layer(archive, stem, result.warnings))
    return result


def _read_layer(archive: SafeZip, stem: str, warnings: list[str]) -> Layer:
    layer_name = stem.rsplit("/", 1)[-1]
    shp = archive.read(f"{stem}.shp")
    shx = archive.read(f"{stem}.shx")
    dbf = archive.read(f"{stem}.dbf")
    prj = archive.read(f"{stem}.prj")
    cpg = archive.read(f"{stem}.cpg")

    if dbf is None:
        raise InvalidShapefileError(f"Layer '{layer_name}' is missing its .dbf attribute file.")
    if shx is None:
        warnings.append(f"Layer '{layer_name}': .shx index missing; read records sequentially.")

    crs = _parse_prj(prj, layer_name, warnings)
    encoding = _encoding_from_cpg(cpg)

    try:
        reader = shapefile.Reader(
            shp=io.BytesIO(shp),
            shx=io.BytesIO(shx) if shx else None,
            dbf=io.BytesIO(dbf),
            encoding=encoding,
            encodingErrors="replace",
        )
    except Exception as exc:  # pyshp raises bare ShapefileException/struct errors
        raise InvalidShapefileError(f"Layer '{layer_name}' could not be opened: {exc}") from exc

    features: list[RawFeature] = []
    try:
        field_names = [f[0] for f in reader.fields[1:]]  # first field is DeletionFlag
        for i, shape_record in enumerate(reader.iterShapeRecords()):
            props = {
                name: _json_safe(value) for name, value in zip(field_names, shape_record.record, strict=False)
            }
            features.append(_to_raw_feature(i, shape_record.shape, props))
        bounds = tuple(reader.bbox) if len(reader) else None
    except InvalidShapefileError:
        raise
    except Exception as exc:
        raise InvalidShapefileError(f"Layer '{layer_name}' is corrupt or truncated: {exc}") from exc

    return Layer(
        name=layer_name,
        crs=crs,
        crs_source=CRSSource.PRJ_FILE if crs is not None else None,
        features=features,
        bounds=bounds,  # type: ignore[arg-type]
    )


def _to_raw_feature(i: int, shp_shape: Any, props: dict[str, Any]) -> RawFeature:
    if shp_shape.shapeType == shapefile.NULL:
        return RawFeature(i, str(i), None, props)

    try:
        geom = shapely_shape(shp_shape.__geo_interface__)
    except Exception:
        # MultiPatch (3D surfaces) and other exotic types have no GeoJSON
        # equivalent. Keep the feature and its attributes, flag the type.
        return RawFeature(
            i,
            str(i),
            None,
            props,
            unsupported_type=getattr(shp_shape, "shapeTypeName", "UNKNOWN"),
        )
    return RawFeature(i, str(i), geom, props)


def _parse_prj(prj: bytes | None, layer_name: str, warnings: list[str]) -> CRS | None:
    if prj is None:
        warnings.append(f"Layer '{layer_name}': no .prj file, CRS must be inferred.")
        return None
    text = prj.decode("utf-8", errors="replace").strip()
    try:
        return CRS.from_wkt(text)
    except CRSError:
        try:
            return CRS.from_user_input(text)
        except CRSError:
            warnings.append(f"Layer '{layer_name}': .prj could not be parsed, CRS must be inferred.")
            return None


def _encoding_from_cpg(cpg: bytes | None) -> str:
    if not cpg:
        return "utf-8"
    raw = cpg.decode("ascii", errors="ignore").strip().lower()
    candidate = _CPG_ALIASES.get(raw, raw)
    try:
        codecs.lookup(candidate)
        return candidate
    except LookupError:
        return "utf-8"


def _json_safe(value: Any) -> Any:
    """dBase values -> JSON-serialisable Python values."""
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value.strip()
    return value
