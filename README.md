# GeoMeasure API

A backend service that accepts a geospatial file (**Shapefile**, **KML** or **KMZ**), extracts its features and returns their **area** and **length** in real-world units.

Built with **Python, FastAPI, SQLAlchemy, Shapely and pyproj** for the Aereo Software Development Intern assignment: *Geospatial File Measurement API*.

---

## Features

- Upload a zipped Shapefile, a `.kml` or a `.kmz`
- Extracts every feature's ID, layer, geometry type, geometry, CRS and attributes
- Area and perimeter for polygons, length for lines; points are reported as not measurable
- Correct CRS handling: geometries are reprojected to a local projected CRS (UTM) before measuring, never measured in degrees
- Every measurement is cross-checked against a geodesic (ellipsoid) calculation
- Results in m², hectares, km² and acres
- Graceful handling of unsupported, empty and broken geometries, with a status and reason per feature
- Protection against zip bombs, XML attacks, fake file types and oversized uploads
- File status tracking (`PENDING → PROCESSING → COMPLETED / FAILED`)
- GeoJSON export that opens directly in QGIS
- Interactive API docs (Swagger UI)
- 66 automated tests, 20 sample files, Docker support and CI on Windows and Linux

---

## Tech stack

| Area | Tools |
|---|---|
| API | FastAPI, Pydantic |
| Database | SQLAlchemy 2.0, SQLite (any SQL database via config) |
| Geospatial | Shapely (geometry), pyproj (CRS and measurement), pyshp (Shapefile), defusedxml (KML) |
| Testing and quality | pytest, ruff, GitHub Actions |
| Deployment | Docker, docker-compose |

---

## Getting started

### Prerequisites
- Python **3.10 or newer** (`python --version`)
- Git

No GDAL installation is needed. Everything installs with `pip` on Windows, macOS and Linux.

### Installation

```bash
git clone https://github.com/<your-username>/geomeasure-api.git
cd geomeasure-api

python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements-dev.txt
```

### Run the server

```bash
uvicorn app.main:app --reload
```

| URL | What it is |
|---|---|
| http://127.0.0.1:8000/docs | interactive API docs (Swagger UI) |
| http://127.0.0.1:8000/health | health check |

### Run with Docker

```bash
docker compose up --build
```

### Run the tests

```bash
pytest
```

### Try all sample files

```bash
python scripts/try_samples.py --details
```

Uploads every file in `samples/` and prints the result. No server needed. Expected results are listed in [`samples/EXPECTED.md`](samples/EXPECTED.md).

### Configuration

Set these as environment variables or in a `.env` file (see `.env.example`):

| Variable | Default |
|---|---|
| `GEO_DATABASE_URL` | `sqlite:///./data/geomeasure.db` |
| `GEO_UPLOAD_DIR` | `./data/uploads` |
| `GEO_MAX_UPLOAD_MB` | `50` |
| `GEO_MAX_UNCOMPRESSED_MB` | `500` |
| `GEO_MAX_ZIP_ENTRIES` | `1000` |

---

## API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/files/` | Upload and process a file |
| `GET` | `/api/files/` | List uploaded files |
| `GET` | `/api/files/{id}/` | File information |
| `GET` | `/api/files/{id}/measurements/` | Measurements per feature, plus a summary |
| `GET` | `/api/files/{id}/features/` | Geometry, CRS and attributes per feature |
| `GET` | `/api/files/{id}/geojson/` | All features as GeoJSON |
| `DELETE` | `/api/files/{id}/` | Delete a file |
| `GET` | `/health` | Health check |

### Upload a file

```bash
curl -F "file=@samples/quarry_site_utm43n.zip" http://127.0.0.1:8000/api/files/
```

Response `201 Created`:

```json
{
  "id": "9cc76a181e3a",
  "filename": "quarry_site_utm43n.zip",
  "file_type": "shapefile",
  "feature_count": 5,
  "crs": "EPSG:32643",
  "status": "COMPLETED",
  "layers": [
    { "name": "haul_roads", "crs": "EPSG:32643", "crs_source": "prj_file", "feature_count": 2 },
    { "name": "parcels", "crs": "EPSG:32643", "crs_source": "prj_file", "feature_count": 3 }
  ],
  "geometry_types": { "LineString": 2, "Polygon": 2, "None": 1 },
  "warnings": [],
  "processing_ms": 72.24,
  "links": {
    "self": "http://127.0.0.1:8000/api/files/9cc76a181e3a/",
    "measurements": "http://127.0.0.1:8000/api/files/9cc76a181e3a/measurements/",
    "features": "http://127.0.0.1:8000/api/files/9cc76a181e3a/features/",
    "geojson": "http://127.0.0.1:8000/api/files/9cc76a181e3a/geojson/"
  }
}
```

### Get file information

```bash
curl http://127.0.0.1:8000/api/files/9cc76a181e3a/
```

Returns the same body as the upload response.

### Get measurements

```bash
curl "http://127.0.0.1:8000/api/files/9cc76a181e3a/measurements/?geometry_type=Polygon"
```

Optional query parameters:

| Parameter | Default | Description |
|---|---|---|
| `method` | `projected` | `projected` (UTM) or `geodesic` (ellipsoid) as the main values |
| `geometry_type` | none | filter, e.g. `Polygon` |
| `status` | none | filter, e.g. `MEASURED`, `ERROR` |
| `min_area_m2` | none | e.g. `10000` for features of 1 hectare or more |
| `limit`, `offset` | `100`, `0` | pagination |

Example item:

```json
{
  "index": 2,
  "layer": "parcels",
  "name": "P-001",
  "geometry_type": "Polygon",
  "status": "MEASURED",
  "area": { "m2": 10000.0, "hectares": 1.0, "km2": 0.01, "acres": 2.471054 },
  "perimeter": { "m": 400.0, "km": 0.4 },
  "length": null,
  "method": {
    "primary": "projected",
    "projected_crs": "EPSG:32643",
    "projected_crs_name": "WGS 84 / UTM zone 43N",
    "projected": { "area_m2": 10000.0, "perimeter_m": 400.0, "length_m": null },
    "geodesic": { "area_m2": 9987.195, "perimeter_m": 399.744, "length_m": null },
    "difference_pct": 0.128218
  },
  "notes": []
}
```

The response also contains a `summary` with totals per geometry type and counts per status.

**Feature statuses**

| Status | Meaning |
|---|---|
| `MEASURED` | area and/or length calculated |
| `NOT_APPLICABLE` | point: nothing to measure |
| `UNSUPPORTED` | e.g. 3D model or MultiPatch; attributes are still returned |
| `NO_GEOMETRY` | feature has no shape |
| `ERROR` | broken shape, e.g. a ring with two points; reason given in `notes` |

### Errors

All errors use the same format:

```json
{ "error": { "code": "UNSUPPORTED_FILE_TYPE", "message": "Unsupported file type '.txt'. Allowed: .kml, .kmz, .zip." } }
```

| Code | HTTP | When |
|---|---|---|
| `EMPTY_FILE` | 400 | empty upload |
| `NOT_FOUND` | 404 | unknown file id |
| `CONFLICT` | 409 | requesting measurements of a failed file |
| `FILE_TOO_LARGE` | 413 | over the upload limit |
| `UNSUPPORTED_FILE_TYPE` | 415 | wrong extension, or a lone `.shp` |
| `INVALID_SHAPEFILE`, `INVALID_KML`, `INVALID_ARCHIVE`, `UNSAFE_ARCHIVE`, `UNKNOWN_CRS` | 422 | file content can't be processed; the record is kept with `status: FAILED` |

---

## Project structure

```
geomeasure-api/
├── app/
│   ├── main.py                 app setup, middleware, error handlers
│   ├── config.py               settings
│   ├── db.py, models.py        database
│   ├── schemas.py              API response models
│   ├── api/files.py            endpoints
│   ├── services/processing.py  upload and processing pipeline
│   └── geo/
│       ├── readers/            file type detection, safe zip, Shapefile and KML readers
│       ├── crs.py              CRS detection and reprojection
│       └── measure.py          area and length calculation
├── tests/                      pytest suite
├── samples/                    example files and expected results
├── scripts/                    sample generators and a sample runner
├── Dockerfile, docker-compose.yml
└── requirements.txt, requirements-dev.txt
```

---

## Architecture

### Application structure
The code has three layers:

1. **API layer** (`app/api/`): handles HTTP requests, validation and responses.
2. **Service layer** (`app/services/`): runs the upload and processing pipeline.
3. **Geo layer** (`app/geo/`): reads files, handles CRS and calculates measurements. It doesn't depend on FastAPI or the database, so it can be tested on its own and reused by a background worker later.

### File-processing flow
1. Check the file type by extension **and** file content.
2. Stream the upload to disk in chunks, enforcing the size limit.
3. Set status to `PROCESSING` and open the file with the matching reader:
   - **KML**: safe XML parsing, folders become layers
   - **KMZ**: unzipped in memory, then read as KML
   - **Zip**: unzipped in memory, each `.shp` becomes a layer
4. Detect each layer's CRS.
5. Convert each feature to WGS84 (stored as GeoJSON) and measure it.
6. Save the results and set status to `COMPLETED`, or `FAILED` with a reason.

### Measurement flow
1. Features with no shape, a broken shape or an unsupported type get a status and are skipped.
2. Height values are dropped (measurements are 2D) and self-intersecting polygons are repaired.
3. A projected CRS is chosen for each feature:
   - normally, the **UTM zone** the feature is in (e.g. EPSG:32643 for Bengaluru)
   - for features too large for one zone or near the poles, a projection centred on the feature
4. Area, perimeter and length are calculated in that projection.
5. The same values are calculated on the WGS84 ellipsoid as a cross-check, and the difference is returned.

### CRS handling

| Input | CRS used |
|---|---|
| Shapefile with `.prj` | read from the `.prj` |
| KML / KMZ | WGS84 (EPSG:4326), as the KML standard requires |
| Shapefile without `.prj`, lon/lat coordinates | WGS84 assumed, with a warning in the response |
| Shapefile without `.prj`, other coordinates | rejected with `UNKNOWN_CRS` instead of guessing |

Geometries are never measured in degrees. They are always transformed **source CRS → WGS84 → UTM** (or the fallback projection) first.

---

## Design decisions

- **FastAPI instead of Django REST Framework.** The service is a pure API. FastAPI gives typed models, input validation and automatic documentation with less code.
- **UTM for measuring.** It's the standard for survey and drone work, accurate within one zone, and the EPSG code lets anyone check the result in QGIS. Web Mercator was rejected because it distorts areas heavily away from the equator.
- **Geodesic cross-check.** A second, projection-free measurement catches wrong CRS data that would otherwise give silently wrong numbers.
- **Extra points along long edges before reprojecting.** A straight edge in one CRS is curved in another. This keeps large polygons accurate; small features aren't affected.
- **No GDAL.** pyshp and defusedxml read Shapefile and KML without native dependencies, so setup is a single `pip install` on any OS. More formats can be added later.
- **Per-feature error handling.** One bad feature doesn't fail the whole file.
- **Measurements stored in columns.** Area and length can be filtered and sorted in SQL.
- **Synchronous processing.** Simple, and the client gets the result in one request. The service layer is ready to move to a background queue.
- **SQLite by default.** No setup needed; the database URL is configurable.

---

## Testing

```bash
pytest          # 66 tests
ruff check .    # lint
```

| File | Covers |
|---|---|
| `tests/test_measure.py` | known answers, holes, projection choice, large and polar features, broken shapes |
| `tests/test_readers.py` | KML, KMZ and Shapefile reading, CRS detection, zip bombs, XML attacks |
| `tests/test_api.py` | every endpoint and error code, and every sample file's expected result |

One key test: a 100 m × 100 m square stored in UTM must measure exactly **10,000 m²** end to end.

---

## Built for Aereo's use case

Aereo Cloud turns drone data into insights for **mining, infrastructure, energy and utilities**. This project is built with that data in mind:

- **Industry samples.** The test files include a mine (pit, waste dumps, stockpiles, haul roads), a solar park, a transmission line, land parcels, a drone flight path and ground control points.
- **Drone data support.** Flight paths recorded as `gx:Track` in KML are read and measured as lines.
- **Multi-layer files.** One zip with parcels, roads and points becomes separate layers, like a real site survey.
- **Indian coordinate systems.** Data in UTM 42N/43N/44N and India's national LCC projection (EPSG:7755) is handled correctly.
- **Reliable numbers.** Every measurement is cross-checked, because clients make decisions on these values.
- **Production practices.** Layered architecture, consistent error codes, status tracking, input security, tests, Docker and CI.

It also covers the skills listed in the role: Python, FastAPI, REST API design, SQL with SQLAlchemy, object-oriented code, debugging, Git, Docker and GIS.

---

## Learnings

- Latitude and longitude are angles, not distances, so measuring directly on them gives meaningless results. The coordinate system must be tracked at every step.
- Every map projection distorts something. UTM is accurate locally, while Web Mercator and national projections can be off by large amounts when used to measure.
- A straight line in one coordinate system is curved in another, which matters for large features.
- A Shapefile is several files. The `.prj`, `.dbf` and `.cpg` carry the CRS, attributes and text encoding.
- Real-world files are messy, and uploads are an attack surface (zip bombs, XML attacks).
- Having a second way to check results helped me find a real bug during development.

---

## Future scope

1. **Stockpile volume**: calculate cubic metres from a polygon and a drone elevation model
2. **Survey comparison**: compare two surveys of the same site over time
3. **Background processing** with Redis and a worker queue for large files
4. **More formats**: GeoJSON, GeoPackage, DXF
5. **PostGIS** for spatial queries and indexing
6. **3D measurements**: surface area and slope using height values
7. **Antimeridian support**: split shapes that cross the ±180° line
8. **Authentication** and separate data per client organisation
9. **React + TypeScript frontend** with reports and comparison views
10. **Cloud deployment** with object storage for uploads
