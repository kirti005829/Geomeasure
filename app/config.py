from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="GEO_", extra="ignore")

    app_name: str = "Geospatial File Measurement API"
    version: str = "1.0.0"
    debug: bool = False

    database_url: str = "sqlite:///./data/geomeasure.db"
    upload_dir: Path = Path("./data/uploads")

    max_upload_mb: int = 50
    max_uncompressed_mb: int = 500
    max_zip_entries: int = 1000

    cors_origins: list[str] = ["*"]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_uncompressed_bytes(self) -> int:
        return self.max_uncompressed_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
