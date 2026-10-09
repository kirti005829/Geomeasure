"""/api/files endpoints."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Feature, FileStatus, UploadedFile
from app.schemas import (
    Area,
    ErrorResponse,
    FeatureMeasurement,
    FeatureOut,
    FeaturePage,
    FileInfo,
    FileList,
    LayerInfo,
    Length,
    Links,
    MeasurementMethod,
    MeasurementReport,
    MeasurementSummary,
    MethodInfo,
    TypeTotals,
)
from app.services.processing import FileProcessor, geometry_type_counts

router = APIRouter(prefix="/api/files", tags=["files"])

DbSession = Annotated[Session, Depends(get_db)]
Limit = Annotated[int, Query(ge=1, le=1000, description="Page size")]
Offset = Annotated[int, Query(ge=0, description="Items to skip")]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _get_file(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"File '{file_id}' not found.")
    return record


def _require_completed(record: UploadedFile) -> None:
    if record.status is not FileStatus.COMPLETED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"File is {record.status.value}; features are only available once COMPLETED."
            + (f" Reason: {record.error}" if record.error else ""),
        )


def _links(request: Request, file_id: str) -> Links:
    base = str(request.url_for("get_file", file_id=file_id))
    return Links(
        self=base,
        features=base + "features/",
        measurements=base + "measurements/",
        geojson=base + "geojson/",
    )


def _utc(value: datetime | None) -> datetime | None:
    # SQLite stores datetimes without an offset; everything we write is UTC.
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def to_file_info(request: Request, record: UploadedFile, type_counts: dict[str, int]) -> FileInfo:
    return FileInfo(
        id=record.id,
        filename=record.filename,
        file_type=record.file_type,
        feature_count=record.feature_count,
        crs=record.crs,
        status=record.status,
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        layers=[LayerInfo(**layer) for layer in record.layers or []],
        geometry_types=type_counts,
        warnings=record.warnings or [],
        error=record.error,
        error_code=record.error_code,
        processing_ms=record.processing_ms,
        created_at=_utc(record.created_at),
        processed_at=_utc(record.processed_at),
        links=_links(request, record.id),
    )


def _type_counts(db: Session, file_id: str) -> dict[str, int]:
    rows = db.execute(
        select(Feature.geometry_type, func.count())
        .where(Feature.file_id == file_id)
        .group_by(Feature.geometry_type)
    ).all()
    return {(t or "None"): n for t, n in rows}


def _pick(f: Feature, method: MeasurementMethod) -> tuple[float | None, float | None, float | None]:
    """(area, perimeter, length) according to the requested primary method."""
    if method is MeasurementMethod.geodesic:
        d = f.measurement_detail or {}
        return d.get("geodesic_area_m2"), d.get("geodesic_perimeter_m"), d.get("geodesic_length_m")
    return f.area_m2, f.perimeter_m, f.length_m


_NAME_KEYS = ("name", "title", "label")


def _mm(value: float | None) -> float | None:
    """Millimetre precision - anything finer is numerical noise."""
    return round(value, 3) if value is not None else None


def _display_name(props: dict[str, Any]) -> str | None:
    """Best human label: name/title/label in any case, else an *_ID field."""
    lowered = {str(k).lower(): v for k, v in props.items() if v not in (None, "")}
    for key in _NAME_KEYS:
        if key in lowered:
            return str(lowered[key])
    for key, value in lowered.items():
        if key == "id" or key.endswith("_id"):
            return str(value)
    # e.g. BLOCK, CIRCUIT, SURVEY_NO: the first short text attribute
    for key, value in lowered.items():
        if key not in {"description", "styleurl"} and isinstance(value, str) and len(value) <= 80:
            return value
    return None


def _to_measurement(f: Feature, method: MeasurementMethod) -> FeatureMeasurement:
    detail: dict[str, Any] = f.measurement_detail or {}
    area, perimeter, length = _pick(f, method)
    method_info = None
    if f.measurement_status == "MEASURED":
        method_info = MethodInfo(
            primary=method,
            projected_crs=detail.get("projected_crs"),
            projected_crs_name=detail.get("projected_crs_name"),
            projection_reason=detail.get("projection_reason"),
            projected={
                "area_m2": _mm(f.area_m2),
                "perimeter_m": _mm(f.perimeter_m),
                "length_m": _mm(f.length_m),
            },
            geodesic={
                "area_m2": _mm(detail.get("geodesic_area_m2")),
                "perimeter_m": _mm(detail.get("geodesic_perimeter_m")),
                "length_m": _mm(detail.get("geodesic_length_m")),
            },
            difference_pct=(
                round(detail["difference_pct"], 6) if detail.get("difference_pct") is not None else None
            ),
        )
    name = _display_name(f.properties or {})
    return FeatureMeasurement(
        index=f.index,
        layer=f.layer,
        source_id=f.source_id,
        name=str(name) if name is not None else None,
        geometry_type=f.geometry_type,
        status=f.measurement_status,
        area=Area.from_m2(area),
        perimeter=Length.from_m(perimeter),
        length=Length.from_m(length),
        method=method_info,
        notes=detail.get("notes") or [],
    )


def _summary(features: list[Feature], method: MeasurementMethod) -> MeasurementSummary:
    by_type: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "measured": 0, "area": None, "length": None}
    )
    total_area = total_length = None
    max_diff = None
    for f in features:
        area, _, length = _pick(f, method)
        bucket = by_type[f.geometry_type or "None"]
        bucket["count"] += 1
        if f.measurement_status == "MEASURED":
            bucket["measured"] += 1
        if area is not None:
            bucket["area"] = (bucket["area"] or 0.0) + area
            total_area = (total_area or 0.0) + area
        if length is not None:
            bucket["length"] = (bucket["length"] or 0.0) + length
            total_length = (total_length or 0.0) + length
        diff = (f.measurement_detail or {}).get("difference_pct")
        if diff is not None:
            max_diff = max(max_diff or 0.0, abs(diff))

    return MeasurementSummary(
        feature_count=len(features),
        by_status=dict(Counter(f.measurement_status for f in features)),
        by_geometry_type={
            t: TypeTotals(
                count=b["count"],
                measured=b["measured"],
                total_area=Area.from_m2(b["area"]),
                total_length=Length.from_m(b["length"]),
            )
            for t, b in sorted(by_type.items())
        },
        total_area=Area.from_m2(total_area),
        total_length=Length.from_m(total_length),
        max_abs_difference_pct=round(max_diff, 6) if max_diff is not None else None,
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=FileInfo,
    summary="Upload and process a geospatial file",
    responses={
        413: {"model": ErrorResponse, "description": "File too large"},
        415: {"model": ErrorResponse, "description": "Unsupported file type"},
        422: {"model": FileInfo, "description": "Stored, but the content could not be processed"},
    },
)
def upload_file(
    request: Request,
    db: DbSession,
    file: Annotated[UploadFile, File(description=".zip (Shapefile), .kml or .kmz")],
):
    """Accepts a zipped **Shapefile**, a **KML** or a **KMZ**.

    Processing is synchronous: the response already contains the final
    status. If the file is well-formed but its content is unusable (e.g. a
    zip with no `.shp`), the record is kept with status `FAILED` and a 422
    is returned so the client can still inspect it via `GET /api/files/{id}/`.
    """
    processor = FileProcessor(db, request.app.state.settings)
    record = processor.ingest(file.filename, file.file)
    info = to_file_info(request, record, geometry_type_counts(record.features))
    if record.status is FileStatus.FAILED:
        return JSONResponse(status_code=422, content=info.model_dump(mode="json"))
    return info


@router.get("/", response_model=FileList, summary="List uploaded files")
def list_files(request: Request, db: DbSession, limit: Limit = 50, offset: Offset = 0):
    total = db.scalar(select(func.count()).select_from(UploadedFile)) or 0
    records = db.scalars(
        select(UploadedFile).order_by(UploadedFile.created_at.desc()).limit(limit).offset(offset)
    ).all()
    items = [to_file_info(request, r, _type_counts(db, r.id)) for r in records]
    return FileList(total=total, limit=limit, offset=offset, items=items)


@router.get("/{file_id}/", response_model=FileInfo, summary="File information", name="get_file")
def get_file(request: Request, file_id: str, db: DbSession):
    record = _get_file(db, file_id)
    return to_file_info(request, record, _type_counts(db, file_id))


@router.get("/{file_id}/features/", response_model=FeaturePage, summary="Features with geometry")
def list_features(
    file_id: str,
    db: DbSession,
    limit: Limit = 100,
    offset: Offset = 0,
    geometry_type: Annotated[str | None, Query(description="e.g. Polygon")] = None,
    layer: str | None = None,
):
    """Every feature's index, geometry type, GeoJSON geometry (WGS84), source CRS and attributes."""
    record = _get_file(db, file_id)
    _require_completed(record)
    query = select(Feature).where(Feature.file_id == file_id)
    if geometry_type:
        query = query.where(Feature.geometry_type == geometry_type)
    if layer:
        query = query.where(Feature.layer == layer)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Feature.index).limit(limit).offset(offset)).all()
    items = [
        FeatureOut(
            index=f.index,
            layer=f.layer,
            source_id=f.source_id,
            geometry_type=f.geometry_type,
            crs=f.source_crs,
            geometry=f.geometry,
            properties=f.properties or {},
        )
        for f in rows
    ]
    return FeaturePage(file_id=file_id, total=total, limit=limit, offset=offset, items=items)


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementReport,
    summary="Area / length per feature",
)
def get_measurements(
    file_id: str,
    db: DbSession,
    method: Annotated[
        MeasurementMethod,
        Query(description="`projected` (local UTM/LAEA, default) or `geodesic` (WGS84 ellipsoid)"),
    ] = MeasurementMethod.projected,
    geometry_type: Annotated[str | None, Query(description="Filter, e.g. Polygon")] = None,
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="MEASURED, NOT_APPLICABLE, UNSUPPORTED, NO_GEOMETRY, ERROR"),
    ] = None,
    min_area_m2: Annotated[float | None, Query(ge=0)] = None,
    limit: Limit = 100,
    offset: Offset = 0,
):
    """Per-feature measurements plus a file-level summary.

    Polygons get **area** and perimeter, lines get **length**, points are
    `NOT_APPLICABLE`. Each measured feature also carries the geodesic
    cross-check and the projected CRS that was used.
    """
    record = _get_file(db, file_id)
    _require_completed(record)

    all_features = list(record.features)
    filtered = all_features
    if geometry_type:
        filtered = [f for f in filtered if f.geometry_type == geometry_type]
    if status_filter:
        filtered = [f for f in filtered if f.measurement_status == status_filter.upper()]
    if min_area_m2 is not None:
        filtered = [f for f in filtered if (_pick(f, method)[0] or 0.0) >= min_area_m2]

    page = filtered[offset : offset + limit]
    return MeasurementReport(
        file_id=record.id,
        filename=record.filename,
        crs=record.crs,
        method=method,
        summary=_summary(all_features, method),
        total=len(filtered),
        limit=limit,
        offset=offset,
        items=[_to_measurement(f, method) for f in page],
    )


@router.get(
    "/{file_id}/geojson/",
    summary="Features + measurements as a GeoJSON FeatureCollection",
    response_description="RFC 7946 FeatureCollection (opens in QGIS, geojson.io, Leaflet...)",
)
def get_geojson(file_id: str, db: DbSession):
    record = _get_file(db, file_id)
    _require_completed(record)
    features = []
    for f in record.features:
        detail = f.measurement_detail or {}
        features.append(
            {
                "type": "Feature",
                "id": f.index,
                "geometry": f.geometry,
                "properties": {
                    **(f.properties or {}),
                    "_layer": f.layer,
                    "_geometry_type": f.geometry_type,
                    "_status": f.measurement_status,
                    "_area_m2": f.area_m2,
                    "_perimeter_m": f.perimeter_m,
                    "_length_m": f.length_m,
                    "_projected_crs": detail.get("projected_crs"),
                    "_difference_pct": detail.get("difference_pct"),
                },
            }
        )
    return JSONResponse(
        {"type": "FeatureCollection", "name": record.filename, "features": features},
        media_type="application/geo+json",
    )


@router.delete("/{file_id}/", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a file")
def delete_file(file_id: str, db: DbSession):
    record = _get_file(db, file_id)
    Path(record.storage_path).unlink(missing_ok=True)
    db.delete(record)
    db.commit()
