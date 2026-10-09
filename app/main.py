from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pyproj
import shapely
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException

from app.api.files import router as files_router
from app.config import Settings, get_settings
from app.db import Base, build_engine, build_sessionmaker
from app.geo.exceptions import GeoFileError, UnsupportedFileTypeError
from app.services.processing import EmptyUploadError, UploadTooLargeError


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")


def _error(status: int, code: str, message: str, details=None) -> JSONResponse:
    body = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(app.state.engine)
        yield
        app.state.engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=(
            "Upload a zipped Shapefile, KML or KMZ and get per-feature area/length, "
            "computed in a locally appropriate projected CRS and cross-checked "
            "against geodesic (ellipsoidal) measurements."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = build_engine(settings.database_url)
    app.state.sessionmaker = build_sessionmaker(app.state.engine)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Process-Time-ms"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
        return response

    @app.exception_handler(GeoFileError)
    async def geo_error(_: Request, exc: GeoFileError):
        status = 415 if isinstance(exc, UnsupportedFileTypeError) else 422
        return _error(status, exc.code, exc.message)

    @app.exception_handler(UploadTooLargeError)
    async def too_large(_: Request, exc: UploadTooLargeError):
        return _error(413, "FILE_TOO_LARGE", str(exc))

    @app.exception_handler(EmptyUploadError)
    async def empty(_: Request, exc: EmptyUploadError):
        return _error(400, "EMPTY_FILE", str(exc))

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        codes = {404: "NOT_FOUND", 409: "CONFLICT", 405: "METHOD_NOT_ALLOWED"}
        return _error(exc.status_code, codes.get(exc.status_code, "HTTP_ERROR"), str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return _error(422, "VALIDATION_ERROR", "Request validation failed.", details)

    # ---- routes ------------------------------------------------------------
    app.include_router(files_router)

    @app.get("/health", tags=["meta"], summary="Liveness + library versions")
    def health():
        return {
            "status": "ok",
            "version": settings.version,
            "shapely": shapely.__version__,
            "pyproj": pyproj.__version__,
            "proj": pyproj.proj_version_str,
        }

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse(url="/docs")

    return app


app = create_app()
