from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Iterator

import pytest
import shapefile
from fastapi.testclient import TestClient
from pyproj import CRS
from pyproj.enums import WktVersion

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        upload_dir=tmp_path / "uploads",
        max_upload_mb=2,
    )


@pytest.fixture
def client(settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def upload(client) -> Callable[[str, bytes], dict]:
    def _upload(name: str, data: bytes, expected: int = 201) -> dict:
        r = client.post("/api/files/", files={"file": (name, data)})
        assert r.status_code == expected, r.text
        return r.json()

    return _upload


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def esri_wkt(epsg: int) -> str:
    """The flavour of WKT that ArcGIS/QGIS actually write into .prj files."""
    return CRS.from_epsg(epsg).to_wkt(WktVersion.WKT1_ESRI)


def make_shapefile_zip(
    build: Callable[[shapefile.Writer], None],
    *,
    prj: str | None = None,
    stem: str = "layer",
    include: tuple[str, ...] = (".shp", ".shx", ".dbf", ".prj"),
    extra: dict[str, bytes] | None = None,
    encoding: str = "utf-8",
) -> bytes:
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, encoding=encoding)
    build(w)
    w.close()
    parts = {".shp": shp.getvalue(), ".shx": shx.getvalue(), ".dbf": dbf.getvalue()}
    if prj is not None:
        parts[".prj"] = prj.encode()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for ext, data in parts.items():
            if ext in include:
                zf.writestr(f"{stem}{ext}", data)
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def kml_doc(placemarks: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">
<Document><name>test</name>{placemarks}</Document></kml>""".encode()


def kml_polygon(coords: list[tuple[float, float]], name: str = "poly", holes=()) -> str:
    def ring(c):
        c = list(c) + [c[0]]
        return " ".join(f"{x},{y}" for x, y in c)

    inner = "".join(
        f"<innerBoundaryIs><LinearRing><coordinates>{ring(h)}</coordinates></LinearRing></innerBoundaryIs>"
        for h in holes
    )
    return (
        f"<Placemark><name>{name}</name><Polygon><outerBoundaryIs><LinearRing>"
        f"<coordinates>{ring(coords)}</coordinates></LinearRing></outerBoundaryIs>{inner}</Polygon></Placemark>"
    )
