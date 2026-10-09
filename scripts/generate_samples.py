from __future__ import annotations

import io
import zipfile
from pathlib import Path

import shapefile
from pyproj import CRS
from pyproj.enums import WktVersion

OUT = Path(__file__).resolve().parent.parent / "samples"


# --------------------------------------------------------------------------- #
# KML
# --------------------------------------------------------------------------- #
def _ring(coords: list[tuple[float, float]]) -> str:
    closed = coords + [coords[0]] if coords[0] != coords[-1] else coords
    return " ".join(f"{x},{y},0" for x, y in closed)


SURVEY_KML = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">
<Document>
  <name>Bengaluru site survey</name>
  <description>Illustrative survey for the GeoMeasure API (approximate coordinates).</description>
  <Folder>
    <name>Site boundaries</name>
    <Placemark id="cubbon-park">
      <name>Cubbon Park (approx.)</name>
      <description>Public park boundary digitised from imagery.</description>
      <ExtendedData>
        <Data name="owner"><value>Dept. of Horticulture</value></Data>
        <Data name="survey_date"><value>2026-09-14</value></Data>
      </ExtendedData>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        {_ring([(77.5895, 12.9790), (77.5985, 12.9810), (77.6000, 12.9760), (77.5975, 12.9705), (77.5920, 12.9700), (77.5880, 12.9745)])}
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark>
    <Placemark id="lalbagh">
      <name>Lalbagh Botanical Garden (approx.)</name>
      <ExtendedData><SchemaData schemaUrl="#survey">
        <SimpleData name="category">Botanical garden</SimpleData>
        <SimpleData name="established">1760</SimpleData>
      </SchemaData></ExtendedData>
      <Polygon>
        <outerBoundaryIs><LinearRing><coordinates>
          {_ring([(77.5800, 12.9530), (77.5890, 12.9530), (77.5895, 12.9455), (77.5850, 12.9420), (77.5795, 12.9450)])}
        </coordinates></LinearRing></outerBoundaryIs>
        <innerBoundaryIs><LinearRing><coordinates>
          {_ring([(77.5840, 12.9490), (77.5860, 12.9490), (77.5860, 12.9475), (77.5840, 12.9475)])}
        </coordinates></LinearRing></innerBoundaryIs>
      </Polygon>
    </Placemark>
    <Placemark id="stockpiles">
      <name>Material stockpiles</name>
      <MultiGeometry>
        <Polygon><outerBoundaryIs><LinearRing><coordinates>
          {_ring([(77.6400, 12.9700), (77.6410, 12.9700), (77.6410, 12.9708), (77.6400, 12.9708)])}
        </coordinates></LinearRing></outerBoundaryIs></Polygon>
        <Polygon><outerBoundaryIs><LinearRing><coordinates>
          {_ring([(77.6420, 12.9700), (77.6426, 12.9700), (77.6426, 12.9705), (77.6420, 12.9705)])}
        </coordinates></LinearRing></outerBoundaryIs></Polygon>
      </MultiGeometry>
    </Placemark>
  </Folder>
  <Folder>
    <name>Roads and flight paths</name>
    <Placemark id="mg-road">
      <name>MG Road (approx.)</name>
      <LineString><tessellate>1</tessellate><coordinates>
        77.6010,12.9755,0 77.6075,12.9752,0 77.6130,12.9738,0 77.6190,12.9733,0
      </coordinates></LineString>
    </Placemark>
    <Placemark id="drone-flight-01">
      <name>Drone survey flight 01</name>
      <description>Recorded by the flight controller as a gx:Track.</description>
      <gx:Track>
        <when>2026-09-14T05:30:00Z</when><gx:coord>77.6400 12.9690 120</gx:coord>
        <when>2026-09-14T05:31:00Z</when><gx:coord>77.6440 12.9690 120</gx:coord>
        <when>2026-09-14T05:32:00Z</when><gx:coord>77.6440 12.9698 120</gx:coord>
        <when>2026-09-14T05:33:00Z</when><gx:coord>77.6400 12.9698 120</gx:coord>
        <when>2026-09-14T05:34:00Z</when><gx:coord>77.6400 12.9706 120</gx:coord>
        <when>2026-09-14T05:35:00Z</when><gx:coord>77.6440 12.9706 120</gx:coord>
      </gx:Track>
    </Placemark>
  </Folder>
  <Folder>
    <name>Ground control points</name>
    <Placemark id="gcp-1"><name>GCP-1</name><Point><coordinates>77.6402, 12.9692, 905.2</coordinates></Point></Placemark>
    <Placemark id="gcp-2"><name>GCP-2</name><Point><coordinates>77.6438,12.9704,906.1</coordinates></Point></Placemark>
    <Placemark id="tower-model">
      <name>Telecom tower (3D model)</name>
      <Model><Location><longitude>77.6420</longitude><latitude>12.9702</latitude><altitude>0</altitude></Location>
        <Link><href>tower.dae</href></Link></Model>
    </Placemark>
    <Placemark id="note"><name>Site office (no geometry yet)</name></Placemark>
  </Folder>
</Document>
</kml>
"""

CONTINENTAL_KML = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Continental corridor</name>
  <Placemark><name>Study region (40° x 20° box)</name>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>{_ring([(60, 5), (100, 5), (100, 25), (60, 25)])}</coordinates></LinearRing></outerBoundaryIs></Polygon>
  </Placemark>
  <Placemark><name>Mumbai to Kolkata (straight line)</name>
    <LineString><coordinates>72.8777,19.0760 88.3639,22.5726</coordinates></LineString>
  </Placemark>
  <Placemark><name>High-Arctic area of interest (85-86°N)</name>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>{_ring([(10, 85), (20, 85), (20, 86), (10, 86)])}</coordinates></LinearRing></outerBoundaryIs></Polygon>
  </Placemark>
</Document></kml>
"""


# --------------------------------------------------------------------------- #
# Shapefiles
# --------------------------------------------------------------------------- #
def _shapefile_bytes(build, prj_wkt: str | None) -> dict[str, bytes]:
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf)
    build(w)
    w.close()
    files = {".shp": shp.getvalue(), ".shx": shx.getvalue(), ".dbf": dbf.getvalue(), ".cpg": b"UTF-8"}
    if prj_wkt:
        files[".prj"] = prj_wkt.encode()
    return files


def _zip(path: Path, layers: dict[str, dict[str, bytes]]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for stem, files in layers.items():
            for ext, data in files.items():
                zf.writestr(f"{stem}{ext}", data)


def quarry_utm() -> None:
    esri_wkt = CRS.from_epsg(32643).to_wkt(WktVersion.WKT1_ESRI)
    e0, n0 = 790_000.0, 1_435_000.0  # near Bengaluru in UTM 43N

    def parcels(w: shapefile.Writer) -> None:
        w.field("PARCEL_ID", "C", 20)
        w.field("LANDUSE", "C", 30)
        w.field("SURVEYED", "D")
        w.field("RATE_INR", "N", 12, 2)
        # exactly 100 m x 100 m = 10,000 m2 in the UTM grid
        w.poly([[(e0, n0), (e0, n0 + 100), (e0 + 100, n0 + 100), (e0 + 100, n0), (e0, n0)]])
        w.record("P-001", "Office block", "20260914", 4500.5)
        # 400 m x 250 m pit with a 50 m x 50 m island (hole): 100,000 - 2,500 = 97,500 m2
        x, y = e0 + 300, n0
        w.poly(
            [
                [(x, y), (x, y + 250), (x + 400, y + 250), (x + 400, y), (x, y)],
                [
                    (x + 100, y + 100),
                    (x + 150, y + 100),
                    (x + 150, y + 150),
                    (x + 100, y + 150),
                    (x + 100, y + 100),
                ],
            ]
        )
        w.record("P-002", "Open pit", "20260914", 0)
        w.null()
        w.record("P-003", "Not yet digitised", None, None)

    def haul_roads(w: shapefile.Writer) -> None:
        w.field("ROAD", "C", 30)
        w.field("SURFACE", "C", 20)
        w.line([[(e0, n0 - 50), (e0 + 300, n0 - 50), (e0 + 300, n0 - 450)]])
        w.record("Haul road A", "gravel")
        w.line([[(e0 + 700, n0), (e0 + 700, n0 + 1000)]])
        w.record("Conveyor", "steel")

    _zip(
        OUT / "quarry_site_utm43n.zip",
        {
            "quarry_site/parcels": _shapefile_bytes(parcels, esri_wkt),
            "quarry_site/haul_roads": _shapefile_bytes(haul_roads, esri_wkt),
        },
    )


def gps_no_prj() -> None:
    def build(w: shapefile.Writer) -> None:
        w.field("NAME", "C", 40)
        # shapefile exterior rings are clockwise
        w.poly(
            [
                [
                    (77.5946, 12.9716),
                    (77.5946, 12.9726),
                    (77.5956, 12.9726),
                    (77.5956, 12.9716),
                    (77.5946, 12.9716),
                ]
            ]
        )
        w.record("Field plot (GPS walk)")

    _zip(OUT / "gps_export_no_prj.zip", {"gps_plot": _shapefile_bytes(build, None)})


def main() -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / "bengaluru_site_survey.kml").write_text(SURVEY_KML, encoding="utf-8")
    (OUT / "continental_corridor.kml").write_text(CONTINENTAL_KML, encoding="utf-8")
    quarry_utm()
    gps_no_prj()
    for p in sorted(OUT.iterdir()):
        print(f"wrote {p.relative_to(OUT.parent)} ({p.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
