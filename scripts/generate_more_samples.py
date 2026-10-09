from __future__ import annotations

import io
import zipfile
from pathlib import Path

import shapefile
from pyproj import CRS, Transformer
from pyproj.enums import WktVersion

ROOT = Path(__file__).resolve().parent.parent / "samples"
REAL = ROOT / "realistic"
EDGE = ROOT / "edge_cases"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def esri(epsg: int) -> bytes:
    return CRS.from_epsg(epsg).to_wkt(WktVersion.WKT1_ESRI).encode()


def shp_parts(build, *, encoding: str = "utf-8") -> dict[str, bytes]:
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, encoding=encoding)
    build(w)
    w.close()
    return {".shp": shp.getvalue(), ".shx": shx.getvalue(), ".dbf": dbf.getvalue()}


def write_zip(path: Path, members: dict[str, bytes], method=zipfile.ZIP_DEFLATED) -> None:
    with zipfile.ZipFile(path, "w", method) as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def layer(stem: str, parts: dict[str, bytes], prj: bytes | None = None, cpg: bytes | None = b"UTF-8"):
    out = {f"{stem}{ext}": data for ext, data in parts.items()}
    if prj is not None:
        out[f"{stem}.prj"] = prj
    if cpg is not None:
        out[f"{stem}.cpg"] = cpg
    return out


def ring(coords):
    c = list(coords)
    if c[0] != c[-1]:
        c.append(c[0])
    return " ".join(f"{x},{y},0" for x, y in c)


def kml(name: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">
<Document><name>{name}</name>
{body}
</Document></kml>
"""


def placemark_polygon(name: str, coords, data: dict | None = None, holes=()) -> str:
    ext = ""
    if data:
        ext = (
            "<ExtendedData>"
            + "".join(f'<Data name="{k}"><value>{v}</value></Data>' for k, v in data.items())
            + "</ExtendedData>"
        )
    inner = "".join(
        f"<innerBoundaryIs><LinearRing><coordinates>{ring(h)}</coordinates></LinearRing></innerBoundaryIs>"
        for h in holes
    )
    return (
        f"<Placemark><name>{name}</name>{ext}<Polygon><outerBoundaryIs><LinearRing>"
        f"<coordinates>{ring(coords)}</coordinates></LinearRing></outerBoundaryIs>{inner}</Polygon></Placemark>"
    )


def placemark_line(name: str, coords, data: dict | None = None) -> str:
    ext = ""
    if data:
        ext = (
            "<ExtendedData>"
            + "".join(f'<Data name="{k}"><value>{v}</value></Data>' for k, v in data.items())
            + "</ExtendedData>"
        )
    pts = " ".join(f"{x},{y},0" for x, y in coords)
    return f"<Placemark><name>{name}</name>{ext}<LineString><coordinates>{pts}</coordinates></LineString></Placemark>"


# --------------------------------------------------------------------------- #
# realistic
# --------------------------------------------------------------------------- #
def iron_ore_mine_kmz() -> None:
    """Open-cast mine near Ballari, Karnataka, as a KMZ (what Google Earth exports)."""
    lon0, lat0 = 76.920, 15.150
    d = 0.001  # ~108 m

    def box(x, y, w, h):
        return [
            (lon0 + x * d, lat0 + y * d),
            (lon0 + (x + w) * d, lat0 + y * d),
            (lon0 + (x + w) * d, lat0 + (y + h) * d),
            (lon0 + x * d, lat0 + (y + h) * d),
        ]

    pit = [
        (lon0 + 2 * d, lat0 + 1 * d),
        (lon0 + 9 * d, lat0 + 0.5 * d),
        (lon0 + 11 * d, lat0 + 5 * d),
        (lon0 + 8 * d, lat0 + 9 * d),
        (lon0 + 3 * d, lat0 + 8 * d),
        (lon0 + 1 * d, lat0 + 4 * d),
    ]
    sump = box(5, 4, 1.2, 1.0)  # water sump inside the pit -> hole

    track = "".join(
        f"<when>2026-09-20T06:{m:02d}:00Z</when><gx:coord>{lon0 + x * d} {lat0 + y * d} 140</gx:coord>"
        for m, (x, y) in enumerate(
            [
                (0, -1),
                (12, -1),
                (12, 1),
                (0, 1),
                (0, 3),
                (12, 3),
                (12, 5),
                (0, 5),
                (0, 7),
                (12, 7),
                (12, 9),
                (0, 9),
            ]
        )
    )
    body = f"""
<Folder><name>Mine lease</name>
  {placemark_polygon("Lease boundary ML-2291", box(-2, -2, 18, 14), {"lease_no": "ML-2291", "mineral": "Iron ore", "valid_till": "2041-03-31"})}
</Folder>
<Folder><name>Pit and dumps</name>
  {placemark_polygon("Main pit (Sep 2026 survey)", pit, {"bench_count": 7, "depth_m": 64}, holes=[sump])}
  {placemark_polygon("Waste dump WD-1", box(13, 0, 2.5, 4), {"material": "Overburden"})}
  {placemark_polygon("ROM stockpile", box(13, 6, 1.5, 1.5), {"material": "Run-of-mine ore", "grade_fe_pct": 58.4})}
  <Placemark><name>Fines stockpiles</name><MultiGeometry>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>{ring(box(-1, 9, 0.6, 0.6))}</coordinates></LinearRing></outerBoundaryIs></Polygon>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>{ring(box(0, 9, 0.6, 0.6))}</coordinates></LinearRing></outerBoundaryIs></Polygon>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>{ring(box(1, 9, 0.6, 0.6))}</coordinates></LinearRing></outerBoundaryIs></Polygon>
  </MultiGeometry></Placemark>
</Folder>
<Folder><name>Haulage</name>
  {placemark_line("Haul road HR-1", [(lon0 + 6 * d, lat0 + 1 * d), (lon0 + 10 * d, lat0 - 1 * d), (lon0 + 14 * d, lat0 + 2 * d)], {"width_m": 25})}
  {placemark_line("Haul road HR-2", [(lon0 + 9 * d, lat0 + 7 * d), (lon0 + 13.5 * d, lat0 + 6.5 * d)], {"width_m": 20})}
</Folder>
<Folder><name>Drone flights</name>
  <Placemark><name>Lawnmower survey 2026-09-20</name>
    <ExtendedData><Data name="drone"><value>Quadcopter, 20 MP RGB</value></Data><Data name="gsd_cm"><value>2.6</value></Data></ExtendedData>
    <gx:Track>{track}</gx:Track></Placemark>
</Folder>
<Folder><name>Ground control</name>
  <Placemark><name>GCP-01</name><Point><coordinates>{lon0},{lat0},612.4</coordinates></Point></Placemark>
  <Placemark><name>GCP-02</name><Point><coordinates>{lon0 + 12 * d},{lat0 + 10 * d},618.9</coordinates></Point></Placemark>
  <Placemark><name>Weighbridge</name><Model><Link><href>weighbridge.dae</href></Link></Model></Placemark>
</Folder>"""
    data = kml("Ballari iron ore mine (illustrative)", body).encode()
    write_zip(
        REAL / "ballari_iron_ore_mine.kmz",
        {"doc.kml": data, "files/README.txt": b"Textures would live here."},
    )


def solar_park_utm42n() -> None:
    """Solar park near Bhadla, Rajasthan - shapefiles in UTM 42N (a different zone)."""
    epsg = 32642
    e0, n0 = 787_400.0, 3_049_600.0

    def blocks(w):
        w.field("BLOCK", "C", 10)
        w.field("CAPACITY", "N", 8, 2)  # MWp
        w.field("COMMISSION", "D")
        for i in range(4):
            for j in range(3):
                x, y = e0 + i * 520, n0 + j * 420
                # 500 m x 400 m = 20 ha per block; shapefile exterior rings are clockwise
                w.poly([[(x, y), (x, y + 400), (x + 500, y + 400), (x + 500, y), (x, y)]])
                w.record(f"B{i + 1}{chr(65 + j)}", 10.0, "20251115")

    def cables(w):
        w.field("CIRCUIT", "C", 20)
        w.field("VOLTAGE", "C", 10)
        w.line([[(e0 - 200, n0 + 200), (e0 + 2100, n0 + 200)]])
        w.record("Feeder 1", "33 kV")
        w.line([[(e0 - 200, n0 + 620), (e0 + 2100, n0 + 620)]])
        w.record("Feeder 2", "33 kV")
        w.line([[(e0 - 200, n0 + 200), (e0 - 200, n0 + 1500), (e0 - 1200, n0 + 1500)]])
        w.record("Evacuation line", "220 kV")

    def inverters(w):
        w.field("ID", "C", 10)
        for i in range(4):
            w.point(e0 + i * 520 + 250, n0 + 610)
            w.record(f"INV-{i + 1}")

    members = {}
    members |= layer("bhadla_solar/pv_blocks", shp_parts(blocks), esri(epsg))
    members |= layer("bhadla_solar/cables", shp_parts(cables), esri(epsg))
    members |= layer("bhadla_solar/inverters", shp_parts(inverters), esri(epsg))
    write_zip(REAL / "bhadla_solar_park_utm42n.zip", members)


def india_lcc_parcels() -> None:
    """Parcels in India's national Lambert Conformal Conic CRS (EPSG:7755), not UTM."""
    to_lcc = Transformer.from_crs(4326, 7755, always_xy=True)
    e0, n0 = to_lcc.transform(76.92, 15.15)

    def parcels(w):
        w.field("SURVEY_NO", "C", 12)
        w.field("VILLAGE", "C", 30)
        w.field("OWNER", "C", 40)
        sizes = [(200, 150), (120, 300), (250, 250)]
        x = e0
        for k, (wd, ht) in enumerate(sizes):
            w.poly([[(x, n0), (x, n0 + ht), (x + wd, n0 + ht), (x + wd, n0), (x, n0)]])
            w.record(f"SY-{101 + k}", "Sandur", ["Ramesh K", "Lakshmi S", "Govt. land"][k])
            x += wd + 30

    write_zip(REAL / "sandur_parcels_india_lcc.zip", layer("sandur_parcels", shp_parts(parcels), esri(7755)))


def transmission_corridor_kml() -> None:
    """A long line crossing three UTM zones -> Azimuthal Equidistant fallback."""
    route = [(72.58, 23.02), (75.86, 22.72), (79.08, 21.15), (81.63, 21.25), (85.32, 23.34), (88.36, 22.57)]
    body = placemark_line("HVDC corridor (Ahmedabad → Kolkata, illustrative)", route, {"voltage": "±800 kV"})
    body += placemark_polygon(
        "Right-of-way survey strip (1 km wide, 2° long)",
        [(78.0, 21.0), (80.0, 21.0), (80.0, 21.009), (78.0, 21.009)],
    )
    (REAL / "hvdc_transmission_corridor.kml").write_text(kml("Transmission corridor", body), encoding="utf-8")


# --------------------------------------------------------------------------- #
# edge cases
# --------------------------------------------------------------------------- #
def edge_cases() -> None:
    sq = [(77.59, 12.97), (77.591, 12.97), (77.591, 12.971), (77.59, 12.971)]

    # 1. invalid / awkward geometries in one KML: processed, statuses per feature
    body = "\n".join(
        [
            placemark_polygon("Valid square", sq),
            placemark_polygon(
                "Bow-tie (self-intersecting)",
                [(77.60, 12.97), (77.601, 12.971), (77.601, 12.97), (77.60, 12.971)],
            ),
            "<Placemark><name>Ring with 2 points</name><Polygon><outerBoundaryIs><LinearRing>"
            "<coordinates>77.6,12.9 77.61,12.91</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>",
            "<Placemark><name>Empty coordinates</name><LineString><coordinates> </coordinates></LineString></Placemark>",
            "<Placemark><name>Spaces after commas</name><LineString><coordinates>"
            "77.59, 12.97, 0   77.60, 12.97, 0</coordinates></LineString></Placemark>",
            "<Placemark><name>Mixed collection</name><MultiGeometry>"
            f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{ring(sq)}</coordinates></LinearRing></outerBoundaryIs></Polygon>"
            "<LineString><coordinates>77.59,12.97 77.59,12.98</coordinates></LineString>"
            "<Point><coordinates>77.595,12.975</coordinates></Point></MultiGeometry></Placemark>",
            "<Placemark><name>3D model</name><Model/></Placemark>",
            "<Placemark><name>No geometry at all</name><description>Just a note</description></Placemark>",
        ]
    )
    (EDGE / "messy_geometries.kml").write_text(kml("Messy geometries", body), encoding="utf-8")

    # 2. XXE attack - must be rejected, never read /etc/passwd
    (EDGE / "xxe_attack.kml").write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE kml [<!ENTITY secret SYSTEM "file:///etc/passwd">]>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>&secret;</name></Document></kml>\n',
        encoding="utf-8",
    )

    # 3. HTML error page saved as .kml
    (EDGE / "html_saved_as.kml").write_text(
        "<!DOCTYPE html><html><body><h1>404 Not Found</h1></body></html>", encoding="utf-8"
    )

    # 4. a text file renamed to .zip
    (EDGE / "not_really_a.zip").write_text("I am not a zip archive.", encoding="utf-8")

    # 5. zip with no .shp (only .dbf and .prj)
    def one_point(w):
        w.field("N", "C", 5)
        w.point(77.59, 12.97)
        w.record("x")

    parts = shp_parts(one_point)
    write_zip(EDGE / "zip_without_shp.zip", {"layer.dbf": parts[".dbf"], "layer.prj": esri(4326)})

    # 6. projected coordinates but no .prj -> UNKNOWN_CRS (refuse to guess)
    def utm_square(w):
        w.field("N", "C", 5)
        w.poly(
            [[(790000, 1435000), (790000, 1435100), (790100, 1435100), (790100, 1435000), (790000, 1435000)]]
        )
        w.record("x")

    write_zip(EDGE / "projected_without_prj.zip", layer("parcel", shp_parts(utm_square), prj=None))

    # 7. zip bomb: 40 MB of zeros compresses ~1000:1
    write_zip(EDGE / "zip_bomb.zip", {"bomb.shp": b"\0" * (40 * 1024 * 1024)})

    # 8. MultiPatch (3D) shapefile with a normal polygon next to it
    def mixed_3d(w):
        w.field("NAME", "C", 30)
        w.multipatch(
            [[(77.59, 12.97, 0), (77.591, 12.97, 0), (77.59, 12.971, 0), (77.591, 12.971, 20)]],
            partTypes=[shapefile.TRIANGLE_STRIP],
        )
        w.record("Building (3D mesh)")

    write_zip(EDGE / "multipatch_3d.zip", layer("buildings", shp_parts(mixed_3d), esri(4326)))

    # 9. a bare .shp - must ask for the sidecars
    (EDGE / "bare_layer.shp").write_bytes(parts[".shp"])

    # 10. cp1252 attributes + .cpg, plus macOS junk entries in the zip
    def accents(w):
        w.field("NAME", "C", 40)
        w.poly([[(2.35, 48.85), (2.35, 48.86), (2.36, 48.86), (2.36, 48.85), (2.35, 48.85)]])
        w.record("Jardin des Plantes – café")

    members = layer("paris", shp_parts(accents, encoding="cp1252"), esri(4326), cpg=b"1252")
    members["__MACOSX/._paris.shp"] = b"\0\5\26\7junk"
    members[".DS_Store"] = b"junk"
    write_zip(EDGE / "encoding_and_macos_junk.zip", members)

    # 11. empty file
    (EDGE / "empty.kml").write_bytes(b"")

    # 12. KMZ saved with a .kml extension (Google Earth users do this)
    write_zip(
        EDGE / "kmz_named_as.kml", {"doc.kml": kml("Renamed KMZ", placemark_polygon("Plot", sq)).encode()}
    )


def main() -> None:
    REAL.mkdir(parents=True, exist_ok=True)
    EDGE.mkdir(parents=True, exist_ok=True)
    iron_ore_mine_kmz()
    solar_park_utm42n()
    india_lcc_parcels()
    transmission_corridor_kml()
    edge_cases()
    for folder in (REAL, EDGE):
        for p in sorted(folder.iterdir()):
            print(f"wrote {p.relative_to(ROOT.parent)} ({p.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
