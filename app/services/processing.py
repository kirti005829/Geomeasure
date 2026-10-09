"""Upload + processing pipeline.

    upload ──► validate name/size ──► stream to disk (sha256 on the fly)
           ──► detect format ──► read layers ──► resolve CRS per layer
           ──► per feature: reproject to WGS84 ─┬─► store GeoJSON
                                                └─► densify in source CRS
                                                    ─► measure (projected + geodesic)
           ──► persist features ──► status COMPLETED | FAILED

The service knows nothing about HTTP; routes translate its results and
exceptions into responses. That keeps it reusable from a background worker
(see "Future scope" in the README).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from shapely.geometry import mapping
from sqlalchemy.orm import Session

from app.config import Settings
from app.geo.crs import crs_label, densify_in_source_crs, resolve_layer_crs, to_wgs84
from app.geo.exceptions import GeoFileError
from app.geo.measure import MeasurementResult, MeasurementStatus, measure_geometry
from app.geo.models import Layer, RawFeature
from app.geo.readers import ArchiveLimits, detect_kind, read_geofile
from app.models import Feature, FileStatus, UploadedFile, new_id

log = logging.getLogger(__name__)

_CHUNK = 1024 * 1024
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]+")


class UploadTooLargeError(Exception):
    def __init__(self, limit_mb: int):
        super().__init__(f"File exceeds the {limit_mb} MB upload limit.")
        self.limit_mb = limit_mb


class EmptyUploadError(Exception):
    pass


def sanitize_filename(name: str | None) -> str:
    # Browsers on Windows may send full paths; keep only the base name.
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _UNSAFE_CHARS.sub("_", base).strip(" .")
    return cleaned[:200] or "upload"


class FileProcessor:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    # ------------------------------------------------------------------ #
    # Ingest
    # ------------------------------------------------------------------ #
    def ingest(self, filename: str | None, stream: BinaryIO) -> UploadedFile:
        safe_name = sanitize_filename(filename)
        kind = detect_kind(safe_name)  # raises UnsupportedFileTypeError -> 415

        record = UploadedFile(
            id=new_id(), filename=safe_name, file_type=kind.value, status=FileStatus.PENDING
        )
        path, size, digest = self._store(stream, record.id, Path(safe_name).suffix.lower())
        record.storage_path, record.size_bytes, record.sha256 = str(path), size, digest

        self.db.add(record)
        self.db.commit()
        self.process(record)
        return record

    def _store(self, stream: BinaryIO, file_id: str, ext: str) -> tuple[Path, int, str]:
        upload_dir = Path(self.settings.upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / f"{file_id}{ext}"  # never trust the client's name on disk
        tmp = dest.with_suffix(dest.suffix + ".part")

        sha, size = hashlib.sha256(), 0
        try:
            with tmp.open("wb") as out:
                while chunk := stream.read(_CHUNK):
                    size += len(chunk)
                    if size > self.settings.max_upload_bytes:
                        raise UploadTooLargeError(self.settings.max_upload_mb)
                    sha.update(chunk)
                    out.write(chunk)
            if size == 0:
                raise EmptyUploadError("Uploaded file is empty.")
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
        return dest, size, sha.hexdigest()

    # ------------------------------------------------------------------ #
    # Processing
    # ------------------------------------------------------------------ #
    def process(self, record: UploadedFile) -> UploadedFile:
        record.status = FileStatus.PROCESSING
        self.db.commit()
        started = time.perf_counter()

        try:
            data = Path(record.storage_path).read_bytes()
            limits = ArchiveLimits(
                max_uncompressed_bytes=self.settings.max_uncompressed_bytes,
                max_entries=self.settings.max_zip_entries,
            )
            result = read_geofile(detect_kind(record.filename), data, limits)
            warnings = list(result.warnings)

            features: list[Feature] = []
            layer_summaries = []
            for layer in result.layers:
                crs = resolve_layer_crs(layer, warnings)
                label = crs_label(crs)
                for raw in layer.features:
                    features.append(self._build_feature(raw, layer, label, len(features), warnings))
                layer_summaries.append(
                    {
                        "name": layer.name,
                        "crs": label,
                        "crs_source": layer.crs_source.value if layer.crs_source else None,
                        "feature_count": len(layer.features),
                    }
                )

            crs_values = {s["crs"] for s in layer_summaries}
            record.features = features
            record.feature_count = len(features)
            record.layers = layer_summaries
            record.crs = crs_values.pop() if len(crs_values) == 1 else ("MIXED" if crs_values else None)
            record.warnings = _dedupe(warnings)
            record.status = FileStatus.COMPLETED
            record.error = record.error_code = None
        except GeoFileError as exc:
            self.db.rollback()
            record.status, record.error, record.error_code = FileStatus.FAILED, exc.message, exc.code
        except Exception:  # unexpected: log with traceback, don't leak internals
            log.exception("Unexpected error while processing file %s", record.id)
            self.db.rollback()
            record.status = FileStatus.FAILED
            record.error = "Internal error while processing the file."
            record.error_code = "INTERNAL_ERROR"

        record.processing_ms = round((time.perf_counter() - started) * 1000, 2)
        record.processed_at = datetime.now(timezone.utc)
        self.db.add(record)
        self.db.commit()
        log.info(
            "Processed %s (%s): status=%s features=%s in %.1f ms",
            record.id,
            record.filename,
            record.status.value,
            record.feature_count,
            record.processing_ms,
        )
        return record

    def _build_feature(
        self, raw: RawFeature, layer: Layer, crs_text: str | None, index: int, warnings: list[str]
    ) -> Feature:
        assert layer.crs is not None
        geometry_json = None
        geometry_type = (
            raw.geometry.geom_type if raw.geometry is not None else raw.unsupported_type or raw.declared_type
        )

        try:
            if raw.geometry is not None and not raw.geometry.is_empty:
                wgs = to_wgs84(raw.geometry, layer.crs)
                geometry_json = mapping(wgs)
                densified = densify_in_source_crs(raw.geometry, layer.crs)
                measured = measure_geometry(to_wgs84(densified, layer.crs))
            else:
                measured = measure_geometry(raw.geometry, raw.unsupported_type, raw.geometry_error)
        except Exception as exc:  # one bad feature must not sink the whole file
            log.warning("Feature %s/%s failed: %s", layer.name, raw.index_in_layer, exc)
            measured = MeasurementResult(MeasurementStatus.ERROR, notes=[f"Measurement failed: {exc}"])

        for w in raw.warnings + ([raw.geometry_error] if raw.geometry_error else []):
            warnings.append(f"Layer '{layer.name}', feature {raw.index_in_layer}: {w}")

        return Feature(
            index=index,
            layer=layer.name,
            source_id=raw.source_id,
            geometry_type=geometry_type,
            geometry=geometry_json,
            source_crs=crs_text,
            properties=raw.properties,
            measurement_status=measured.status.value,
            area_m2=measured.area_m2,
            perimeter_m=measured.perimeter_m,
            length_m=measured.length_m,
            measurement_detail={
                "projected_crs": measured.projected_crs,
                "projected_crs_name": measured.projected_crs_name,
                "projection_reason": measured.projection_reason,
                "geodesic_area_m2": measured.geodesic_area_m2,
                "geodesic_perimeter_m": measured.geodesic_perimeter_m,
                "geodesic_length_m": measured.geodesic_length_m,
                "difference_pct": measured.difference_pct,
                "notes": measured.notes + raw.warnings,
            },
        )


def geometry_type_counts(features: list[Feature]) -> dict[str, int]:
    return dict(Counter(f.geometry_type or "None" for f in features))


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
