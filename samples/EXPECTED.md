# Sample files and what the API should do with them

Run them all at once (no server needed):

```bash
python scripts/try_samples.py --details
```

Or upload any of them at http://127.0.0.1:8000/docs. `tests/test_api.py::test_every_sample_file_behaves_as_documented` keeps this table honest.

Regenerate with `python scripts/generate_samples.py` and `python scripts/generate_more_samples.py`. Coordinates of real places are approximate and illustrative.

## Basics (`samples/`)

| File | Result | What it shows |
|---|---|---|
| `bengaluru_site_survey.kml` | 201 · 5 measured, 2 points, 1 unsupported, 1 no geometry | folders → layers, hole, MultiGeometry, `gx:Track`, ExtendedData |
| `quarry_site_utm43n.zip` | 201 · exactly 10,000 m², 97,500 m², 700 m, 1,000 m | projected input, known answers |
| `gps_export_no_prj.zip` | 201 · CRS `assumed` + warning | no `.prj` but lon/lat coordinates |
| `continental_corridor.kml` | 201 · LAEA / AEQD projections | too big / too far north for UTM |

## Realistic (`samples/realistic/`)

| File | Result | What it shows |
|---|---|---|
| `ballari_iron_ore_mine.kmz` | 201 · 11 features in 5 layers, ~388 ha total | the kind of file a mining client sends: lease, pit with a water sump (hole), dumps, stockpiles, haul roads, an 8.8 km lawnmower drone flight (`gx:Track`), GCPs, a 3D model |
| `bhadla_solar_park_utm42n.zip` | 201 · 12 × 20 ha blocks = 240 ha, cables 6.9 km | **three layers** (polygons, lines, points) in a **different UTM zone** (42N) |
| `sandur_parcels_india_lcc.zip` | 201 · 3 parcels in EPSG:7755 | India's national Lambert Conformal Conic. A 200 m × 150 m parcel *on the LCC grid* is **30,493 m² on the ground** (+1.6 %): national-scale projections distort locally, which is why we re-project to UTM to measure |
| `hvdc_transmission_corridor.kml` | 201 · 1,747 km line via AEQD; 2°-long strip via UTM 44N | a line crossing three UTM zones vs. a polygon that still fits in one |

## Edge cases (`samples/edge_cases/`)

| File | Result | Why |
|---|---|---|
| `messy_geometries.kml` | 201 · 4 measured, 2 ERROR, 1 unsupported, 1 no geometry | bow-tie repaired, 2-point ring and empty coordinates reported per feature, `"lon, lat"` spacing, mixed collection |
| `multipatch_3d.zip` | 201 · UNSUPPORTED | 3D mesh shapefile; attributes still returned |
| `encoding_and_macos_junk.zip` | 201 · name `Jardin des Plantes – café` | cp1252 `.dbf` + `.cpg`, `__MACOSX/` and `.DS_Store` ignored |
| `kmz_named_as.kml` | 201 | KMZ with the wrong extension is detected by its bytes |
| `bare_layer.shp` | 415 `UNSUPPORTED_FILE_TYPE` | explains the sidecar files are needed |
| `empty.kml` | 400 `EMPTY_FILE` | |
| `html_saved_as.kml` | 422 `INVALID_KML` | an error page saved as `.kml` |
| `not_really_a.zip` | 422 `INVALID_ARCHIVE` | text file renamed to `.zip` |
| `zip_without_shp.zip` | 422 `INVALID_SHAPEFILE` | only `.dbf` + `.prj` inside |
| `projected_without_prj.zip` | 422 `UNKNOWN_CRS` | metres with no CRS: refusing beats guessing |
| `xxe_attack.kml` | 422 `INVALID_KML` | tries to read `/etc/passwd` via an XML entity; blocked |
| `zip_bomb.zip` | 422 `UNSAFE_ARCHIVE` | 41 KB that expands to 40 MB |

The 422s are still stored with `status: FAILED`, so `GET /api/files/{id}/` shows the reason later.
