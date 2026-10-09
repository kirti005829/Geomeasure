"""KML / KMZ reader.

Notes from the OGC KML 2.2 specification that shape this implementation:

* Coordinates are **always** WGS84 longitude, latitude[, altitude]
  (EPSG:4326, lon/lat order). KML has no way to declare another CRS, so the
  CRS is known without guessing.
* Coordinate tuples are separated by whitespace and their components by
  commas. Real-world files often contain ``"77.59, 12.97"`` (comma + space),
  which naive ``split()`` parsing breaks on - we normalise that first.
* Placemarks may be nested arbitrarily deep inside ``Document``/``Folder``
  elements. The closest enclosing folder name becomes the feature's *layer*.
* ``MultiGeometry`` can mix types; ``gx:Track`` (Google extension) is how GPS
  loggers and drone flight apps record paths, so it is read as a LineString.
* Attributes come from ``name``, ``description``, ``ExtendedData/Data`` and
  ``ExtendedData/SchemaData/SimpleData``.

Parsing uses ``defusedxml`` so hostile files (XXE, "billion laughs") are
rejected instead of exhausting memory or reading local files.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any
from xml.etree.ElementTree import Element, ParseError

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException
from pyproj import CRS
from shapely.geometry import (
    GeometryCollection,
    LinearRing,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from app.geo.exceptions import InvalidKMLError
from app.geo.models import CRSSource, Layer, RawFeature, ReadResult
from app.geo.readers.archive import SafeZip

KML_CRS = CRS.from_epsg(4326)
_COMMA_SPACING = re.compile(r"\s*,\s*")
_CONTAINERS = {"Document", "Folder"}
_UNSUPPORTED_GEOMETRIES = {"Model"}  # 3D COLLADA models


class _UnsupportedGeometry(Exception):
    def __init__(self, type_name: str, reason: str = ""):
        super().__init__(reason or type_name)
        self.type_name = type_name


def _local(tag: Any) -> str:
    """'{http://www.opengis.net/kml/2.2}Placemark' -> 'Placemark'."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(el: Element, name: str) -> Element | None:
    return next((c for c in el if _local(c.tag) == name), None)


def _children(el: Element, name: str) -> list[Element]:
    return [c for c in el if _local(c.tag) == name]


def _text(el: Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    return el.text.strip() or None


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
def read_kml_bytes(data: bytes, default_layer: str = "kml") -> ReadResult:
    try:
        root = SafeET.fromstring(data)
    except DefusedXmlException as exc:
        raise InvalidKMLError(
            "KML contains forbidden XML constructs (DTD/entities) and was rejected."
        ) from exc
    except ParseError as exc:
        raise InvalidKMLError(f"File is not well-formed XML: {exc}") from exc

    if _local(root.tag) != "kml":
        raise InvalidKMLError(f"Root element is <{_local(root.tag)}>, expected <kml>.")

    layers: dict[str, Layer] = {}
    for layer_name, placemark in _walk_placemarks(root, default_layer):
        layer = layers.setdefault(layer_name, Layer(layer_name, KML_CRS, CRSSource.KML_SPEC, features=[]))
        layer.features.append(_parse_placemark(placemark, len(layer.features)))

    result = ReadResult(layers=list(layers.values()))
    if not result.layers:
        result.warnings.append("KML contains no Placemark elements.")
    return result


def read_kmz(archive: SafeZip) -> ReadResult:
    """KMZ = zipped KML. Google Earth uses ``doc.kml`` as the root document."""
    kml_names = archive.find(".kml")
    if not kml_names:
        raise InvalidKMLError("KMZ archive does not contain a .kml document.")
    main = "doc.kml" if "doc.kml" in kml_names else sorted(kml_names, key=len)[0]
    data = archive.read(main)
    assert data is not None
    return read_kml_bytes(data, default_layer=main.rsplit("/", 1)[-1][:-4])


# --------------------------------------------------------------------------- #
# Tree walking
# --------------------------------------------------------------------------- #
def _walk_placemarks(root: Element, default_layer: str) -> Iterator[tuple[str, Element]]:
    """Depth-first walk yielding (closest container name, Placemark)."""

    def walk(el: Element, layer: str) -> Iterator[tuple[str, Element]]:
        for child in el:
            tag = _local(child.tag)
            if tag == "Placemark":
                yield layer, child
            elif tag in _CONTAINERS:
                yield from walk(child, _text(_child(child, "name")) or layer)

    yield from walk(root, default_layer)


def _parse_placemark(pm: Element, index: int) -> RawFeature:
    props = _properties(pm)
    source_id = pm.get("id") or str(index)

    geom_el = next((c for c in pm if _local(c.tag) in _GEOMETRY_PARSERS), None)
    if geom_el is None:
        geom_el = next((c for c in pm if _local(c.tag) in _UNSUPPORTED_GEOMETRIES), None)
        if geom_el is not None:
            return RawFeature(index, source_id, None, props, unsupported_type=_local(geom_el.tag))
        return RawFeature(index, source_id, None, props)

    try:
        geometry = _parse_geometry(geom_el)
    except _UnsupportedGeometry as exc:
        return RawFeature(index, source_id, None, props, unsupported_type=exc.type_name)
    except (ValueError, TypeError) as exc:
        return RawFeature(
            index,
            source_id,
            None,
            props,
            declared_type=_local(geom_el.tag),
            geometry_error=f"Malformed {_local(geom_el.tag)} geometry: {exc}",
        )
    return RawFeature(index, source_id, geometry, props)


def _properties(pm: Element) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for key in ("name", "description", "address", "styleUrl"):
        value = _text(_child(pm, key))
        if value is not None:
            props[key] = value

    timestamp = _child(pm, "TimeStamp")
    if timestamp is not None and (when := _text(_child(timestamp, "when"))):
        props["timestamp"] = when

    extended = _child(pm, "ExtendedData")
    if extended is not None:
        for data in _children(extended, "Data"):
            if name := data.get("name"):
                props[name] = _text(_child(data, "value"))
        for schema_data in _children(extended, "SchemaData"):
            for simple in _children(schema_data, "SimpleData"):
                if name := simple.get("name"):
                    props[name] = _text(simple)
    return props


# --------------------------------------------------------------------------- #
# Geometry parsing
# --------------------------------------------------------------------------- #
def _coords(el: Element | None) -> list[tuple[float, float]]:
    """Parse a <coordinates> element into 2D (lon, lat) tuples."""
    raw = _text(el)
    if not raw:
        raise ValueError("empty <coordinates>")
    tuples = []
    for token in _COMMA_SPACING.sub(",", raw).split():
        parts = token.split(",")
        if len(parts) < 2:
            raise ValueError(f"bad coordinate tuple '{token}'")
        tuples.append((float(parts[0]), float(parts[1])))  # altitude intentionally dropped
    return tuples


def _point(el: Element) -> Point:
    return Point(_coords(_child(el, "coordinates"))[0])


def _linestring(el: Element) -> LineString:
    return LineString(_coords(_child(el, "coordinates")))


def _linearring(el: Element) -> LinearRing:
    return LinearRing(_coords(_child(el, "coordinates")))


def _ring_of(boundary: Element | None) -> list[tuple[float, float]]:
    ring = _child(boundary, "LinearRing") if boundary is not None else None
    if ring is None:
        raise ValueError("polygon boundary without <LinearRing>")
    return _coords(_child(ring, "coordinates"))


def _polygon(el: Element) -> Polygon:
    shell = _ring_of(_child(el, "outerBoundaryIs"))
    holes = [_ring_of(b) for b in _children(el, "innerBoundaryIs")]
    return Polygon(shell, holes)


def _track(el: Element) -> LineString:
    """gx:Track stores positions as <gx:coord>lon lat alt</gx:coord>."""
    points = []
    for coord in (c for c in el if _local(c.tag) == "coord"):
        parts = (_text(coord) or "").split()
        if len(parts) >= 2:
            points.append((float(parts[0]), float(parts[1])))
    if len(points) < 2:
        raise _UnsupportedGeometry("Track", "track has fewer than two positions")
    return LineString(points)


def _multitrack(el: Element) -> MultiLineString:
    return MultiLineString([_track(t) for t in el if _local(t.tag) == "Track"])


def _multigeometry(el: Element) -> BaseGeometry:
    parts = [
        _parse_geometry(c)
        for c in el
        if _local(c.tag) in _GEOMETRY_PARSERS or _local(c.tag) in _UNSUPPORTED_GEOMETRIES
    ]
    if not parts:
        raise ValueError("empty MultiGeometry")
    kinds = {p.geom_type for p in parts}
    # Collapse homogeneous collections into proper Multi* types so they are
    # measured (and reported) like their shapefile equivalents.
    if kinds == {"Polygon"}:
        return MultiPolygon(parts)
    if kinds <= {"LineString", "LinearRing"}:
        return MultiLineString([LineString(p.coords) for p in parts])
    if kinds == {"Point"}:
        return MultiPoint(parts)
    return GeometryCollection(parts)


def _parse_geometry(el: Element) -> BaseGeometry:
    tag = _local(el.tag)
    if tag in _UNSUPPORTED_GEOMETRIES:
        raise _UnsupportedGeometry(tag)
    return _GEOMETRY_PARSERS[tag](el)


_GEOMETRY_PARSERS = {
    "Point": _point,
    "LineString": _linestring,
    "LinearRing": _linearring,
    "Polygon": _polygon,
    "MultiGeometry": _multigeometry,
    "Track": _track,
    "MultiTrack": _multitrack,
}
