from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.config import settings
from app.database import execute, fetch_one, upsert_torrent_session
from app.schemas import MagnetRequest, StreamInitRequest, TorrentStatusResponse
from app.security import issue_stream_token
from app.services.alldebrid import AllDebridError, alldebrid_client

router = APIRouter(prefix="/api/torrents", tags=["torrents"])


@router.post("/magnet", status_code=status.HTTP_201_CREATED)
async def add_magnet(payload: MagnetRequest):
    try:
        upload_payload = await alldebrid_client.upload_magnet(payload.magnet)
    except AllDebridError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if isinstance(upload_payload.get("error"), dict):
        error_message = upload_payload["error"].get("message") or "Unable to upload magnet"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_message)

    external_id = _extract_external_id(upload_payload)
    if not external_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not parse torrent id from AllDebrid")

    state = await _safe_get_state(external_id)
    links = state.get("links", [])
    stream_url = await _safe_create_stream_url(links)

    status_payload = state.get("status") if state else upload_payload
    title = _extract_filename(status_payload) or _extract_filename(upload_payload)
    status_label = _extract_status(status_payload) or _extract_status(upload_payload)
    progress = _extract_progress(status_payload) or _extract_progress(upload_payload)

    raw = {
        "upload": upload_payload,
        "status": state.get("status") if state else None,
        "files": state.get("files") if state else None,
    }

    upsert_torrent_session(
        external_id=external_id,
        source_type="magnet",
        title=title,
        status=status_label,
        progress=progress,
        stream_url=stream_url,
        raw_json=raw,
    )

    return TorrentStatusResponse(
        id=external_id,
        status=status_label,
        progress=progress,
        filename=title,
        streamable=bool(links),
        stream_url=stream_url,
        links=links,
        raw=raw,
    )


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_torrent(file: UploadFile = File(...)):
    filename = file.filename or "upload.torrent"
    if not filename.lower().endswith(".torrent"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .torrent files are supported")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded torrent file is empty")

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Torrent file is too large")

    try:
        upload_payload = await alldebrid_client.upload_torrent_file(filename, content)
    except AllDebridError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if isinstance(upload_payload.get("error"), dict):
        error_message = upload_payload["error"].get("message") or "Unable to upload torrent"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_message)

    external_id = _extract_external_id(upload_payload)
    if not external_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not parse torrent id from AllDebrid")

    state = await _safe_get_state(external_id)
    links = state.get("links", [])
    stream_url = await _safe_create_stream_url(links)

    status_payload = state.get("status") if state else upload_payload
    title = _extract_filename(status_payload) or _extract_filename(upload_payload) or filename
    status_label = _extract_status(status_payload) or _extract_status(upload_payload)
    progress = _extract_progress(status_payload) or _extract_progress(upload_payload)

    raw = {
        "upload": upload_payload,
        "status": state.get("status") if state else None,
        "files": state.get("files") if state else None,
    }

    upsert_torrent_session(
        external_id=external_id,
        source_type="torrent_file",
        title=title,
        status=status_label,
        progress=progress,
        stream_url=stream_url,
        raw_json=raw,
    )

    sha256 = hashlib.sha256(content).hexdigest()
    execute(
        """
        INSERT INTO uploads_metadata (filename, content_type, size_bytes, sha256, stored_path)
        VALUES (?, ?, ?, ?, ?)
        """,
        (filename, file.content_type, len(content), sha256, f"alldebrid://{external_id}"),
    )

    return TorrentStatusResponse(
        id=external_id,
        status=status_label,
        progress=progress,
        filename=title,
        streamable=bool(links),
        stream_url=stream_url,
        links=links,
        raw=raw,
    )


@router.get("/{torrent_id}/status", response_model=TorrentStatusResponse)
async def get_torrent_status(torrent_id: str):
    try:
        state = await alldebrid_client.get_magnet_state(torrent_id)
    except AllDebridError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    status_payload = state.get("status") or {}
    links = state.get("links") or []

    existing = fetch_one("SELECT stream_url FROM torrent_sessions WHERE external_id = ?", (torrent_id,))
    stream_url = existing["stream_url"] if existing else None
    if not stream_url and links:
        stream_url = await _safe_create_stream_url(links)

    status_label = _extract_status(status_payload)
    progress = _extract_progress(status_payload)
    filename = _extract_filename(status_payload)

    raw = {
        "status": status_payload,
        "files": state.get("files"),
    }

    upsert_torrent_session(
        external_id=torrent_id,
        source_type="magnet",
        title=filename,
        status=status_label,
        progress=progress,
        stream_url=stream_url,
        raw_json=raw,
    )

    return TorrentStatusResponse(
        id=torrent_id,
        status=status_label,
        progress=progress,
        filename=filename,
        streamable=bool(links),
        stream_url=stream_url,
        links=links,
        raw=raw,
    )


@router.post("/{torrent_id}/stream", response_model=TorrentStatusResponse)
async def init_torrent_stream(torrent_id: str, payload: StreamInitRequest):
    retries = payload.retries or settings.torrent_stream_retries
    interval = payload.interval_seconds or settings.torrent_poll_interval_seconds

    try:
        state = await alldebrid_client.wait_until_streamable(torrent_id, retries, interval)
    except AllDebridError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    links = state.get("links") or []
    status_payload = state.get("status") or {}

    if not links:
        status_label = _extract_status(status_payload) or "processing"
        progress = _extract_progress(status_payload)
        upsert_torrent_session(
            external_id=torrent_id,
            source_type="magnet",
            title=_extract_filename(status_payload),
            status=status_label,
            progress=progress,
            stream_url=None,
            raw_json=state,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Torrent is not streamable yet. Retry after more progress.",
        )

    stream_url = await _create_stream_url_from_links(links)

    status_label = _extract_status(status_payload) or "ready"
    progress = _extract_progress(status_payload)
    filename = _extract_filename(status_payload)

    upsert_torrent_session(
        external_id=torrent_id,
        source_type="magnet",
        title=filename,
        status=status_label,
        progress=progress,
        stream_url=stream_url,
        raw_json=state,
    )

    return TorrentStatusResponse(
        id=torrent_id,
        status=status_label,
        progress=progress,
        filename=filename,
        streamable=True,
        stream_url=stream_url,
        links=links,
        raw=state,
    )


async def _safe_get_state(external_id: str) -> dict[str, Any]:
    try:
        return await alldebrid_client.get_magnet_state(external_id)
    except AllDebridError:
        return {}


async def _safe_create_stream_url(links: list[str]) -> str | None:
    if not links:
        return None
    try:
        return await _create_stream_url_from_links(links)
    except (AllDebridError, HTTPException):
        return None


async def _create_stream_url_from_links(links: list[str]) -> str:
    last_error: Exception | None = None
    for link in links:
        try:
            unlocked = await alldebrid_client.unlock_link(link)
            token, _ = issue_stream_token({"url": unlocked, "headers": {}})
            return f"/stream/{token}"
        except AllDebridError as exc:
            last_error = exc
            continue

    if last_error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(last_error))
    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not unlock any streamable link")


def _extract_external_id(payload: Any) -> str | None:
    for key in ("id", "magnet_id", "hash"):
        value = _find_first_key(payload, key)
        if isinstance(value, (str, int)) and str(value):
            return str(value)
    return None


def _extract_status(payload: Any) -> str | None:
    for key in ("status", "statusValue"):
        value = _find_first_key(payload, key)
        if value is not None:
            return str(value)

    status_code = _find_first_key(payload, "statusCode")
    if isinstance(status_code, (int, float)):
        if int(status_code) == 4:
            return "ready"
        return f"code_{int(status_code)}"
    return None


def _extract_progress(payload: Any) -> float:
    for key in ("progress", "downloadPercent"):
        value = _find_first_key(payload, key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            cleaned = value.strip().rstrip("%")
            try:
                return float(cleaned)
            except ValueError:
                continue

    downloaded = _find_first_key(payload, "downloaded")
    size = _find_first_key(payload, "size")
    if isinstance(downloaded, (int, float)) and isinstance(size, (int, float)) and size > 0:
        return max(0.0, min(100.0, float(downloaded) / float(size) * 100.0))

    return 0.0


def _extract_filename(payload: Any) -> str | None:
    for key in ("filename", "name", "title"):
        value = _find_first_key(payload, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _find_first_key(value: Any, needle: str) -> Any:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.lower() == needle.lower():
                return nested
            found = _find_first_key(nested, needle)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first_key(item, needle)
            if found is not None:
                return found
    return None
