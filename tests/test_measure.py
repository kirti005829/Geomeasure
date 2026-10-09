import math

import pytest
from shapely.geometry import GeometryCollection, LineString, Point, Polygon, box

from app.geo.crs import WGS84, densify_in_source_crs, select_projection, utm_epsg
from app.geo.measure import MeasurementStatus, measure_geometry

# Length of 1° of longitude on the WGS84 equator: a * pi / 180
ONE_DEGREE_EQUATOR_M = 6_378_137.0 * math.pi / 180.0


def test_degree_of_longitude_at_equator():
    r = measure_geometry(LineString([(0, 0), (1, 0)]))
    assert r.status is MeasurementStatus.MEASURED
    assert r.geodesic_length_m == pytest.approx(ONE_DEGREE_EQUATOR_M, rel=1e-9)
    # UTM scale factor is <= ~1.001 inside a zone
    assert r.length_m == pytest.approx(ONE_DEGREE_EQUATOR_M, rel=1e-3)


def test_area_is_in_square_metres_not_square_degrees():
    # ~1.1 km x 1.1 km in Bengaluru; in "square degrees" this would be 0.0001
    r = measure_geometry(box(77.59, 12.97, 77.60, 12.98))
    assert 1.1e6 < r.area_m2 < 1.3e6
    assert r.projected_crs == "EPSG:32643"  # UTM zone 43N
    assert abs(r.difference_pct) < 0.2


def test_projected_and_geodesic_agree_for_small_features():
    r = measure_geometry(box(-0.13, 51.50, -0.12, 51.51))  # London
    assert r.projected_crs == "EPSG:32630"
    assert r.area_m2 == pytest.approx(r.geodesic_area_m2, rel=2e-3)
    assert r.perimeter_m == pytest.approx(r.geodesic_perimeter_m, rel=1e-3)


def test_holes_are_subtracted():
    outer = [(77.59, 12.97), (77.60, 12.97), (77.60, 12.98), (77.59, 12.98)]
    hole = [(77.592, 12.972), (77.594, 12.972), (77.594, 12.974), (77.592, 12.974)]
    solid = measure_geometry(Polygon(outer))
    holed = measure_geometry(Polygon(outer, [hole]))
    hole_only = measure_geometry(Polygon(hole))
    assert holed.area_m2 == pytest.approx(solid.area_m2 - hole_only.area_m2, rel=1e-9)
    assert holed.geodesic_area_m2 == pytest.approx(
        solid.geodesic_area_m2 - hole_only.geodesic_area_m2, rel=1e-6
    )
    # perimeter includes the hole's ring
    assert holed.perimeter_m == pytest.approx(solid.perimeter_m + hole_only.perimeter_m, rel=1e-9)


def test_ring_orientation_does_not_change_area():
    ccw = Polygon([(10, 10), (10.01, 10), (10.01, 10.01), (10, 10.01)])
    cw = Polygon(list(ccw.exterior.coords)[::-1])
    assert measure_geometry(cw).geodesic_area_m2 == pytest.approx(measure_geometry(ccw).geodesic_area_m2)


def test_large_polygon_uses_equal_area_projection_and_matches_geodesic():
    geom = densify_in_source_crs(box(60, 5, 100, 25), WGS84)
    r = measure_geometry(geom)
    assert "+proj=laea" in r.projected_crs
    assert r.area_m2 == pytest.approx(r.geodesic_area_m2, rel=1e-6)


def test_long_line_uses_equidistant_fallback():
    r = measure_geometry(densify_in_source_crs(LineString([(70, 10), (90, 30)]), WGS84))
    assert "+proj=aeqd" in r.projected_crs
    assert r.length_m == pytest.approx(r.geodesic_length_m, rel=1e-4)


def test_polar_feature_avoids_utm():
    choice = select_projection(box(10, 85, 20, 86), prefer_equal_area=True)
    assert "laea" in choice.label


@pytest.mark.parametrize(
    ("lon", "lat", "epsg"),
    [
        (77.59, 12.97, 32643),
        (151.2, -33.9, 32756),
        (-74.0, 40.7, 32618),
        (180.0, 1.0, 32660),
        (-180.0, 1.0, 32601),
    ],
)
def test_utm_zone_selection(lon, lat, epsg):
    assert utm_epsg(lon, lat) == epsg


def test_points_are_not_applicable():
    assert measure_geometry(Point(77.5, 12.9)).status is MeasurementStatus.NOT_APPLICABLE


def test_missing_and_unsupported_geometry_are_reported_not_raised():
    assert measure_geometry(None).status is MeasurementStatus.NO_GEOMETRY
    r = measure_geometry(None, unsupported_type="MultiPatch")
    assert r.status is MeasurementStatus.UNSUPPORTED
    assert "MultiPatch" in r.notes[0]
    assert measure_geometry(Polygon()).status is MeasurementStatus.NO_GEOMETRY


def test_invalid_bowtie_is_repaired():
    bowtie = Polygon([(0, 0), (0.01, 0.01), (0.01, 0), (0, 0.01)])
    r = measure_geometry(bowtie)
    assert r.status is MeasurementStatus.MEASURED
    assert r.area_m2 > 0
    assert any("repaired" in n for n in r.notes)


def test_mixed_collection_measures_both_parts():
    gc = GeometryCollection([box(0, 0, 0.01, 0.01), LineString([(0, 0), (0.01, 0)]), Point(0, 0)])
    r = measure_geometry(gc)
    assert r.area_m2 and r.length_m
    assert any("Mixed" in n for n in r.notes)


def test_z_values_are_ignored_with_a_note():
    r = measure_geometry(LineString([(0, 0, 100), (0.01, 0, 900)]))
    assert r.length_m == pytest.approx(measure_geometry(LineString([(0, 0), (0.01, 0)])).length_m)
    assert any("Z values" in n for n in r.notes)


def test_malformed_geometry_is_an_error_not_unsupported():
    r = measure_geometry(None, geometry_error="Malformed Polygon geometry: too few points")
    assert r.status is MeasurementStatus.ERROR
    assert "too few points" in r.notes[0]
