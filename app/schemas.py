from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import FileStatus

M2_PER_HECTARE = 10_000.0
M2_PER_KM2 = 1_000_000.0
M2_PER_ACRE = 4_046.8564224  # international acre
M_PER_KM = 1_000.0


class MeasurementMethod(str, Enum):
    projected = "projected"
    geodesic = "geodesic"


class Links(BaseModel):
    self: str
    features: str
    measurements: str
    geojson: str


class LayerInfo(BaseModel):
    name: str
    crs: str | None
    crs_source: str | None = Field(description="prj_file | kml_spec | assumed")
    feature_count: int


class FileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    feature_count: int
    crs: str | None = Field(description="Source CRS. 'MIXED' if layers differ.")
    status: FileStatus
    size_bytes: int
    sha256: str
    layers: list[LayerInfo] = []
    geometry_types: dict[str, int] = {}
    warnings: list[str] = []
    error: str | None = None
    error_code: str | None = None
    processing_ms: float | None = None
    created_at: datetime
    processed_at: datetime | None = None
    links: Links


class FileList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[FileInfo]


class FeatureOut(BaseModel):
    index: int
    layer: str
    source_id: str | None
    geometry_type: str | None
    crs: str | None = Field(description="CRS of the source data for this feature")
    geometry: dict[str, Any] | None = Field(description="GeoJSON geometry in WGS84 lon/lat (RFC 7946)")
    properties: dict[str, Any]


class FeaturePage(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    items: list[FeatureOut]


class Area(BaseModel):
    m2: float
    hectares: float
    km2: float
    acres: float

    @classmethod
    def from_m2(cls, m2: float | None) -> Area | None:
        if m2 is None:
            return None
        return cls(
            m2=round(m2, 3),
            hectares=round(m2 / M2_PER_HECTARE, 6),
            km2=round(m2 / M2_PER_KM2, 8),
            acres=round(m2 / M2_PER_ACRE, 6),
        )


class Length(BaseModel):
    m: float
    km: float

    @classmethod
    def from_m(cls, m: float | None) -> Length | None:
        if m is None:
            return None
        return cls(m=round(m, 3), km=round(m / M_PER_KM, 6))


class MethodInfo(BaseModel):
    primary: MeasurementMethod
    projected_crs: str | None
    projected_crs_name: str | None
    projection_reason: str | None
    projected: dict[str, float | None]
    geodesic: dict[str, float | None]
    difference_pct: float | None = Field(
        description="(projected - geodesic) / geodesic x 100. A sanity check on the CRS handling."
    )


class FeatureMeasurement(BaseModel):
    index: int
    layer: str
    source_id: str | None
    name: str | None
    geometry_type: str | None
    status: str
    area: Area | None = None
    perimeter: Length | None = None
    length: Length | None = None
    method: MethodInfo | None = None
    notes: list[str] = []


class TypeTotals(BaseModel):
    count: int
    measured: int
    total_area: Area | None = None
    total_length: Length | None = None


class MeasurementSummary(BaseModel):
    feature_count: int
    by_status: dict[str, int]
    by_geometry_type: dict[str, TypeTotals]
    total_area: Area | None
    total_length: Length | None
    max_abs_difference_pct: float | None = Field(
        description="Largest projected-vs-geodesic disagreement across features"
    )


class MeasurementReport(BaseModel):
    file_id: str
    filename: str
    crs: str | None
    method: MeasurementMethod
    summary: MeasurementSummary
    total: int = Field(description="Features matching the filters")
    limit: int
    offset: int
    items: list[FeatureMeasurement]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
