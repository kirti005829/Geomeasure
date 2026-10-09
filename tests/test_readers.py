import io
import zipfile

import pytest

from app.geo.crs import crs_label, resolve_layer_crs
from app.geo.exceptions import (
    InvalidKMLError,
    InvalidShapefileError,
    UnknownCRSError,
    UnsafeArchiveError,
    UnsupportedFileTypeError,
)
from app.geo.models import CRSSource
from app.geo.readers import ArchiveLimits, FileKind, detect_kind, read_geofile
from app.geo.readers.archive import SafeZip
from tests.conftest import esri_wkt, kml_doc, kml_polygon, make_shapefile_zip


# --------------------------------------------------------------------------- #
# Format detection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("name", "kind"),
    [("a.zip", FileKind.SHAPEFILE), ("A.KML", FileKind.KML), ("b.kmz", FileKind.KMZ)],
)
def test_detect_kind(name, kind):
    assert detect_kind(name) is kind


@pytest.mark.parametrize("name", ["a.geojson", "a.shp", "noext"])
def test_detect_kind_rejects(name):
    with pytest.raises(UnsupportedFileTypeError):
        detect_kind(name)


# --------------------------------------------------------------------------- #
# KML
# --------------------------------------------------------------------------- #
def test_kml_reads_layers_properties_and_geometries():
    data = kml_doc(
        """
        <Folder><name>Parcels</name>
          <Placemark id="p1"><name>A</name>
            <ExtendedData><Data name="owner"><value>Asha</value></Data></ExtendedData>
            <Polygon><outerBoundaryIs><LinearRing><coordinates>0,0 0,1 1,1 0,0</coordinates></LinearRing></outerBoundaryIs></Polygon>
          </Placemark>
          <Folder><name>Nested</name>
            <Placemark><name>Road</name><LineString><coordinates>0,0 1,1</coordinates></LineString></Placemark>
          </Folder>
        </Folder>
        <Placemark><name>Top</name><Point><coordinates>5,5,10</coordinates></Point></Placemark>
        """
    )
    result = read_geofile(FileKind.KML, data)
    layers = {layer.name: layer for layer in result.layers}
    assert set(layers) == {"Parcels", "Nested", "test"}
    poly = layers["Parcels"].features[0]
    assert poly.source_id == "p1"
    assert poly.properties == {"name": "A", "owner": "Asha"}
    assert poly.geometry.geom_type == "Polygon"
    assert layers["Nested"].features[0].geometry.geom_type == "LineString"
    assert layers["test"].features[0].geometry.has_z is False  # altitude dropped
    assert all(layer.crs_source is CRSSource.KML_SPEC for layer in result.layers)
    assert crs_label(result.layers[0].crs) == "EPSG:4326"


def test_kml_tolerates_spaces_after_commas():
    data = kml_doc(
        "<Placemark><LineString><coordinates>77.5, 12.9, 0   77.6 ,13.0</coordinates></LineString></Placemark>"
    )
    geom = read_geofile(FileKind.KML, data).layers[0].features[0].geometry
    assert list(geom.coords) == [(77.5, 12.9), (77.6, 13.0)]


def test_kml_polygon_with_hole():
    data = kml_doc(kml_polygon([(0, 0), (0, 3), (3, 3), (3, 0)], holes=[[(1, 1), (1, 2), (2, 2), (2, 1)]]))
    geom = read_geofile(FileKind.KML, data).layers[0].features[0].geometry
    assert len(geom.interiors) == 1


def test_kml_gx_track_becomes_linestring():
    data = kml_doc(
        "<Placemark><gx:Track><when>t1</when><gx:coord>77.0 12.0 100</gx:coord>"
        "<when>t2</when><gx:coord>77.1 12.1 100</gx:coord></gx:Track></Placemark>"
    )
    geom = read_geofile(FileKind.KML, data).layers[0].features[0].geometry
    assert geom.geom_type == "LineString" and len(geom.coords) == 2


def test_kml_multigeometry_collapses_to_multi_type():
    data = kml_doc(
        "<Placemark><MultiGeometry>"
        "<LineString><coordinates>0,0 1,1</coordinates></LineString>"
        "<LineString><coordinates>2,2 3,3</coordinates></LineString>"
        "</MultiGeometry></Placemark>"
    )
    assert read_geofile(FileKind.KML, data).layers[0].features[0].geometry.geom_type == "MultiLineString"


def test_kml_unsupported_and_missing_geometry_are_kept():
    data = kml_doc(
        "<Placemark><name>Tower</name><Model><Link><href>t.dae</href></Link></Model></Placemark>"
        "<Placemark><name>Nothing</name></Placemark>"
        "<Placemark><name>Broken</name><Polygon><outerBoundaryIs><LinearRing><coordinates>0,0 1,1</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
    )
    model, empty, broken = read_geofile(FileKind.KML, data).layers[0].features
    assert model.geometry is None and model.unsupported_type == "Model"
    assert empty.geometry is None and empty.unsupported_type is None
    assert broken.geometry is None and broken.geometry_error  # malformed ring reported, not raised
    assert broken.declared_type == "Polygon" and broken.unsupported_type is None


def test_kml_rejects_xxe():
    evil = b"""<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY x SYSTEM "file:///etc/passwd">]>
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>&x;</name></Document></kml>"""
    with pytest.raises(InvalidKMLError, match="forbidden"):
        read_geofile(FileKind.KML, evil)


@pytest.mark.parametrize("payload", [b"<html><body>404</body></html>", b"not xml at all", b"<kml><unclosed>"])
def test_kml_rejects_non_kml(payload):
    with pytest.raises(InvalidKMLError):
        read_geofile(FileKind.KML, payload)


def test_kmz():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.kml", kml_doc(kml_polygon([(0, 0), (0, 1), (1, 1)])))
        zf.writestr("files/icon.png", b"\x89PNG")
    result = read_geofile(FileKind.KMZ, buf.getvalue())
    assert result.feature_count == 1


# --------------------------------------------------------------------------- #
# Shapefile
# --------------------------------------------------------------------------- #
def _square(w):
    w.field("NAME", "C", 20)
    w.poly([[(0, 0), (0, 100), (100, 100), (100, 0), (0, 0)]])
    w.record("sq")


def test_shapefile_reads_esri_prj_as_epsg():
    data = make_shapefile_zip(_square, prj=esri_wkt(32643))
    layer = read_geofile(FileKind.SHAPEFILE, data).layers[0]
    assert crs_label(layer.crs) == "EPSG:32643"
    assert layer.crs_source is CRSSource.PRJ_FILE
    assert layer.features[0].properties == {"NAME": "sq"}


def test_shapefile_multiple_layers_and_null_shape():
    def with_null(w):
        w.field("ID", "N", 5)
        w.null()
        w.record(1)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for stem, builder in {"a": _square, "sub/b": with_null}.items():
            inner = zipfile.ZipFile(io.BytesIO(make_shapefile_zip(builder, prj=esri_wkt(4326))))
            for n in inner.namelist():
                zf.writestr(f"{stem}{n[len('layer') :]}", inner.read(n))
    result = read_geofile(FileKind.SHAPEFILE, buf.getvalue())
    assert [layer.name for layer in result.layers] == ["a", "b"]
    assert result.layers[1].features[0].geometry is None


def test_shapefile_ignores_macos_junk():
    data = make_shapefile_zip(_square, prj=esri_wkt(4326), extra={"__MACOSX/._layer.shp": b"junk"})
    assert len(read_geofile(FileKind.SHAPEFILE, data).layers) == 1


def test_shapefile_without_shp_or_dbf_is_rejected():
    with pytest.raises(InvalidShapefileError, match="does not contain a .shp"):
        read_geofile(FileKind.SHAPEFILE, make_shapefile_zip(_square, include=(".dbf",)))
    with pytest.raises(InvalidShapefileError, match="missing its .dbf"):
        read_geofile(FileKind.SHAPEFILE, make_shapefile_zip(_square, include=(".shp", ".shx")))


def test_missing_prj_with_lonlat_coords_assumes_wgs84():
    def lonlat(w):
        w.field("N", "C", 5)
        w.poly([[(77.5, 12.9), (77.5, 13.0), (77.6, 13.0), (77.5, 12.9)]])
        w.record("x")

    layer = read_geofile(FileKind.SHAPEFILE, make_shapefile_zip(lonlat)).layers[0]
    warnings: list[str] = []
    assert crs_label(resolve_layer_crs(layer, warnings)) == "EPSG:4326"
    assert layer.crs_source is CRSSource.ASSUMED and warnings


def test_missing_prj_with_projected_coords_is_refused():
    layer = read_geofile(FileKind.SHAPEFILE, make_shapefile_zip(lambda w: _utm_square(w))).layers[0]
    with pytest.raises(UnknownCRSError):
        resolve_layer_crs(layer, [])


def _utm_square(w):
    w.field("N", "C", 5)
    w.poly([[(790000, 1435000), (790000, 1435100), (790100, 1435100), (790000, 1435000)]])
    w.record("x")


def test_dbf_encoding_from_cpg():
    def latin(w):
        w.field("NAME", "C", 20)
        w.point(1, 1)
        w.record("Bogotá Café")

    data = make_shapefile_zip(latin, prj=esri_wkt(4326), extra={"layer.cpg": b"1252"}, encoding="cp1252")
    assert read_geofile(FileKind.SHAPEFILE, data).layers[0].features[0].properties["NAME"] == "Bogotá Café"


# --------------------------------------------------------------------------- #
# Archive safety
# --------------------------------------------------------------------------- #
def _zip_of(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, d in entries.items():
            zf.writestr(n, d)
    return buf.getvalue()


def test_zip_bomb_ratio_is_rejected():
    with pytest.raises(UnsafeArchiveError, match="compression ratio"):
        SafeZip(_zip_of({"bomb.shp": b"\0" * (30 * 1024 * 1024)}))


def test_uncompressed_size_limit():
    data = _zip_of({"a.shp": b"x" * 2048})
    with pytest.raises(UnsafeArchiveError, match="uncompressed"):
        SafeZip(data, ArchiveLimits(max_uncompressed_bytes=1024))


def test_entry_count_limit():
    data = _zip_of({f"f{i}.txt": b"x" for i in range(20)})
    with pytest.raises(UnsafeArchiveError, match="entries"):
        SafeZip(data, ArchiveLimits(max_entries=10))
