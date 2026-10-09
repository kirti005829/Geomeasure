import pytest

from tests.conftest import esri_wkt, kml_doc, kml_polygon, make_shapefile_zip

BENGALURU_BLOCK = [(77.59, 12.97), (77.59, 12.98), (77.60, 12.98), (77.60, 12.97)]

KML = kml_doc(
    kml_polygon(BENGALURU_BLOCK, name="Block")
    + "<Placemark><name>Road</name><LineString><coordinates>77.59,12.97 77.60,12.97</coordinates></LineString></Placemark>"
    + "<Placemark><name>GCP</name><Point><coordinates>77.595,12.975</coordinates></Point></Placemark>"
    + "<Placemark><name>Tower</name><Model/></Placemark>"
)


def utm_square_zip() -> bytes:
    def build(w):
        w.field("PARCEL", "C", 10)
        e, n = 790_000, 1_435_000
        w.poly([[(e, n), (e, n + 100), (e + 100, n + 100), (e + 100, n), (e, n)]])  # 100 m x 100 m
        w.record("P1")

    return make_shapefile_zip(build, prj=esri_wkt(32643))


def test_upload_returns_spec_fields(upload):
    body = upload("survey.kml", KML)
    for key in ("id", "filename", "feature_count", "crs", "status"):
        assert key in body
    assert body["filename"] == "survey.kml"
    assert body["feature_count"] == 4
    assert body["crs"] == "EPSG:4326"
    assert body["status"] == "COMPLETED"
    assert body["geometry_types"] == {"Polygon": 1, "LineString": 1, "Point": 1, "Model": 1}


def test_get_file_info(client, upload):
    file_id = upload("survey.kml", KML)["id"]
    r = client.get(f"/api/files/{file_id}/")
    assert r.status_code == 200
    assert r.json()["links"]["measurements"].endswith(f"/api/files/{file_id}/measurements/")
    created = r.json()["created_at"]
    assert created.endswith("Z") or created.endswith("+00:00")  # timezone-aware UTC


def test_measurements(client, upload):
    file_id = upload("survey.kml", KML)["id"]
    body = client.get(f"/api/files/{file_id}/measurements/").json()
    items = {i["name"]: i for i in body["items"]}

    block = items["Block"]
    assert block["status"] == "MEASURED"
    assert 1.1e6 < block["area"]["m2"] < 1.3e6
    assert block["area"]["hectares"] == pytest.approx(block["area"]["m2"] / 10_000, rel=1e-6)
    assert block["method"]["projected_crs"] == "EPSG:32643"
    assert abs(block["method"]["difference_pct"]) < 0.2

    assert items["Road"]["length"]["m"] > 1000
    assert items["Road"]["area"] is None
    assert items["GCP"]["status"] == "NOT_APPLICABLE"
    assert items["Tower"]["status"] == "UNSUPPORTED"

    summary = body["summary"]
    assert summary["by_status"] == {"MEASURED": 2, "NOT_APPLICABLE": 1, "UNSUPPORTED": 1}
    assert summary["total_area"]["m2"] == pytest.approx(block["area"]["m2"], rel=1e-6)


def test_projected_shapefile_is_reprojected_correctly(client, upload):
    """A 100 m x 100 m square drawn in UTM 43N must measure 10,000 m^2."""
    body = upload("parcels.zip", utm_square_zip())
    assert body["crs"] == "EPSG:32643"
    item = client.get(f"/api/files/{body['id']}/measurements/").json()["items"][0]
    assert item["area"]["m2"] == pytest.approx(10_000, abs=0.01)
    assert item["perimeter"]["m"] == pytest.approx(400, abs=0.001)
    # true ground area differs from UTM grid area by the scale factor (~0.13% here)
    assert item["method"]["geodesic"]["area_m2"] == pytest.approx(10_000, rel=3e-3)


def test_geodesic_method_switches_primary_values(client, upload):
    file_id = upload("survey.kml", KML)["id"]
    url = f"/api/files/{file_id}/measurements/?geometry_type=Polygon"
    projected = client.get(url).json()["items"][0]
    geodesic = client.get(url + "&method=geodesic").json()["items"][0]
    assert geodesic["area"]["m2"] == pytest.approx(projected["method"]["geodesic"]["area_m2"], abs=1e-3)
    assert geodesic["area"]["m2"] != projected["area"]["m2"]


def test_measurement_filters_and_pagination(client, upload):
    file_id = upload("survey.kml", KML)["id"]
    base = f"/api/files/{file_id}/measurements/"
    assert client.get(base + "?status=measured").json()["total"] == 2
    assert client.get(base + "?min_area_m2=1").json()["total"] == 1
    page = client.get(base + "?limit=1&offset=1").json()
    assert page["total"] == 4 and len(page["items"]) == 1 and page["items"][0]["index"] == 1
    assert client.get(base + "?limit=0").status_code == 422


def test_features_endpoint_exposes_geometry_crs_properties(client, upload):
    file_id = upload("survey.kml", KML)["id"]
    body = client.get(f"/api/files/{file_id}/features/?geometry_type=Polygon").json()
    f = body["items"][0]
    assert f["geometry"]["type"] == "Polygon"
    assert f["crs"] == "EPSG:4326"
    assert f["properties"]["name"] == "Block"


def test_geojson_export(client, upload):
    file_id = upload("survey.kml", KML)["id"]
    r = client.get(f"/api/files/{file_id}/geojson/")
    assert r.headers["content-type"].startswith("application/geo+json")
    fc = r.json()
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 4
    assert fc["features"][0]["properties"]["_area_m2"] > 0


def test_unprocessable_content_is_stored_as_failed(client, upload):
    def build(w):
        w.field("N", "C", 2)
        w.point(1, 1)
        w.record("x")

    body = upload("dbf_only.zip", make_shapefile_zip(build, include=(".dbf",)), expected=422)
    assert body["status"] == "FAILED"
    assert body["error_code"] == "INVALID_SHAPEFILE"
    assert client.get(f"/api/files/{body['id']}/").json()["status"] == "FAILED"
    r = client.get(f"/api/files/{body['id']}/measurements/")
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFLICT"


def test_unknown_crs_fails_cleanly(upload):
    def build(w):
        w.field("N", "C", 2)
        w.poly([[(790000, 1435000), (790000, 1435100), (790100, 1435100), (790000, 1435000)]])
        w.record("x")

    body = upload("noprj.zip", make_shapefile_zip(build), expected=422)
    assert body["error_code"] == "UNKNOWN_CRS"


@pytest.mark.parametrize(
    ("name", "data", "status", "code"),
    [
        ("notes.txt", b"hello", 415, "UNSUPPORTED_FILE_TYPE"),
        ("layer.shp", b"\0" * 100, 415, "UNSUPPORTED_FILE_TYPE"),
        ("empty.kml", b"", 400, "EMPTY_FILE"),
    ],
)
def test_rejected_uploads(client, name, data, status, code):
    r = client.post("/api/files/", files={"file": (name, data)})
    assert r.status_code == status
    assert r.json()["error"]["code"] == code


def test_upload_size_limit(client):
    big = b"<kml>" + b" " * (3 * 1024 * 1024) + b"</kml>"  # test settings: 2 MB limit
    r = client.post("/api/files/", files={"file": ("big.kml", big)})
    assert r.status_code == 413


def test_path_in_filename_is_sanitised(upload):
    assert upload("C:\\Users\\me\\..\\survey.kml", KML)["filename"] == "survey.kml"


def test_not_found_and_delete(client, upload):
    assert client.get("/api/files/doesnotexist/").status_code == 404
    file_id = upload("survey.kml", KML)["id"]
    assert client.delete(f"/api/files/{file_id}/").status_code == 204
    assert client.get(f"/api/files/{file_id}/").status_code == 404


def test_list_files(client, upload):
    upload("a.kml", KML)
    upload("b.zip", utm_square_zip())
    body = client.get("/api/files/?limit=1").json()
    assert body["total"] == 2 and len(body["items"]) == 1


def test_health_and_root_redirect(client):
    assert client.get("/health").json()["status"] == "ok"
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/docs"


def test_feature_names_fall_back_to_text_attributes(client, upload):
    def build(w):
        w.field("CIRCUIT", "C", 20)
        w.line([[(77.59, 12.97), (77.60, 12.97)]])
        w.record("Feeder 1")

    file_id = upload("cables.zip", make_shapefile_zip(build, prj=esri_wkt(4326)))["id"]
    item = client.get(f"/api/files/{file_id}/measurements/").json()["items"][0]
    assert item["name"] == "Feeder 1"


def test_every_sample_file_behaves_as_documented(client):
    """Guards samples/EXPECTED.md: each sample's outcome (HTTP status + code/statuses)."""
    from pathlib import Path

    samples = Path(__file__).resolve().parent.parent / "samples"
    expected = {
        "edge_cases/bare_layer.shp": (415, "UNSUPPORTED_FILE_TYPE"),
        "edge_cases/empty.kml": (400, "EMPTY_FILE"),
        "edge_cases/html_saved_as.kml": (422, "INVALID_KML"),
        "edge_cases/not_really_a.zip": (422, "INVALID_ARCHIVE"),
        "edge_cases/projected_without_prj.zip": (422, "UNKNOWN_CRS"),
        "edge_cases/xxe_attack.kml": (422, "INVALID_KML"),
        "edge_cases/zip_bomb.zip": (422, "UNSAFE_ARCHIVE"),
        "edge_cases/zip_without_shp.zip": (422, "INVALID_SHAPEFILE"),
        "edge_cases/messy_geometries.kml": (
            201,
            {"MEASURED": 4, "ERROR": 2, "UNSUPPORTED": 1, "NO_GEOMETRY": 1},
        ),
        "edge_cases/multipatch_3d.zip": (201, {"UNSUPPORTED": 1}),
        "edge_cases/kmz_named_as.kml": (201, {"MEASURED": 1}),
        "edge_cases/encoding_and_macos_junk.zip": (201, {"MEASURED": 1}),
        "realistic/ballari_iron_ore_mine.kmz": (201, {"MEASURED": 8, "NOT_APPLICABLE": 2, "UNSUPPORTED": 1}),
        "realistic/bhadla_solar_park_utm42n.zip": (201, {"MEASURED": 15, "NOT_APPLICABLE": 4}),
        "realistic/sandur_parcels_india_lcc.zip": (201, {"MEASURED": 3}),
        "realistic/hvdc_transmission_corridor.kml": (201, {"MEASURED": 2}),
    }
    for rel, (status, outcome) in expected.items():
        path = samples / rel
        r = client.post("/api/files/", files={"file": (path.name, path.read_bytes())})
        assert r.status_code == status, (rel, r.text)
        body = r.json()
        if isinstance(outcome, dict):
            summary = client.get(f"/api/files/{body['id']}/measurements/").json()["summary"]
            assert summary["by_status"] == outcome, rel
        else:
            code = body["error"]["code"] if isinstance(body.get("error"), dict) else body["error_code"]
            assert code == outcome, rel
