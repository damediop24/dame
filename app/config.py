from __future__ import annotations

import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Vesper Stream"
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = 3000

    database_path: str = "/data/vesper.db"
    upload_dir: str = "./uploads"

    secret_key: str = "change-this-in-production"
    token_ttl_seconds: int = 900

    cors_origins: str = "*"

    alldebrid_api_base: str = "https://api.alldebrid.com/v4"
    alldebrid_api_key: str | None = None
    alldebrid_agent: str = "vesper-stream"
    torrent_stream_retries: int = 20
    torrent_poll_interval_seconds: float = 3.0

    request_timeout_seconds: float = 20.0
    max_upload_size_mb: int = 100
    resolve_timeout_seconds: float = 18.0
    resolve_cache_ttl_seconds: int = 600

    prebuffer_max_bytes: int = 33554432
    prebuffer_cache_entries: int = 32
    prebuffer_cache_max_bytes: int = 268435456

    public_dir: str = "./public"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if not raw:
            return ["*"]

        if raw.startswith("["):
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, list):
                    cleaned = [str(item).strip() for item in loaded if str(item).strip()]
                    return cleaned or ["*"]
            except json.JSONDecodeError:
                pass

        return [origin.strip() for origin in raw.split(",") if origin.strip()] or ["*"]

    def resolve_database_path(self) -> Path:
        requested = Path(self.database_path)
        try:
            requested.parent.mkdir(parents=True, exist_ok=True)
            return requested
        except PermissionError:
            fallback = Path("./data/vesper.db")
            fallback.parent.mkdir(parents=True, exist_ok=True)
            return fallback

    def resolve_upload_dir(self) -> Path:
        upload_path = Path(self.upload_dir)
        upload_path.mkdir(parents=True, exist_ok=True)
        return upload_path

    def resolve_public_dir(self) -> Path:
        return Path(self.public_dir).resolve()


settings = Settings()
