from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ResolveRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)


class MediaStream(BaseModel):
    url: str
    format_id: str | None = None
    ext: str | None = None
    quality: str | None = None
    is_hls: bool = False
    headers: dict[str, str] = Field(default_factory=dict)


class MediaItem(BaseModel):
    id: str | None = None
    title: str
    webpage_url: str
    thumbnail: str | None = None
    duration_seconds: int | None = None
    streams: list[MediaStream] = Field(default_factory=list)


class ResolveResponse(BaseModel):
    items: list[MediaItem]


class TokenRequest(BaseModel):
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    expires_in_seconds: int | None = None


class TokenResponse(BaseModel):
    token: str
    stream_url: str
    expires_at: int


class PrebufferRequest(BaseModel):
    token: str = Field(min_length=16, max_length=4000)
    max_bytes: int | None = Field(default=None, ge=1024, le=268435456)


class PrebufferResponse(BaseModel):
    ok: bool = True
    cached_bytes: int
    requested_bytes: int
    total_size_bytes: int | None = None


class HistoryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    source_url: str
    playback_url: str | None = None
    position_seconds: float = 0
    duration_seconds: float | None = None


class FavoriteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    source_url: str
    thumbnail_url: str | None = None


class MagnetRequest(BaseModel):
    magnet: str = Field(min_length=10, max_length=5000)


class TorrentStatusResponse(BaseModel):
    id: str
    status: str | None = None
    progress: float = 0
    filename: str | None = None
    streamable: bool = False
    stream_url: str | None = None
    links: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class StreamInitRequest(BaseModel):
    retries: int | None = None
    interval_seconds: float | None = None


class UploadMetadata(BaseModel):
    id: int
    filename: str
    content_type: str | None
    size_bytes: int
    sha256: str
    stored_path: str
    created_at: str
