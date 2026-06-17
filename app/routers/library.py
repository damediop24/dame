from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.database import execute, fetch_all, fetch_one, get_table_columns
from app.schemas import FavoriteCreate, HistoryCreate

router = APIRouter(prefix="/api/library", tags=["library"])


@router.get("/history")
def list_history(limit: int = 100):
    safe_limit = min(max(limit, 1), 500)
    return fetch_all(
        """
        SELECT id, title, source_url, playback_url, position_seconds, duration_seconds, watched_at
        FROM history
        ORDER BY watched_at DESC
        LIMIT ?
        """,
        (safe_limit,),
    )


@router.post("/history", status_code=status.HTTP_201_CREATED)
def add_history(item: HistoryCreate):
    columns = get_table_columns("history")

    values: dict[str, object] = {
        "title": item.title,
        "source_url": item.source_url,
        "playback_url": item.playback_url,
        "position_seconds": item.position_seconds,
        "duration_seconds": item.duration_seconds,
    }

    if "url" in columns:
        values["url"] = item.source_url
    if "resolved_url" in columns:
        values["resolved_url"] = item.playback_url or item.source_url
    if "progress_seconds" in columns:
        values["progress_seconds"] = item.position_seconds

    insert_columns = [name for name in values.keys() if name in columns]
    insert_values = [values[name] for name in insert_columns]
    placeholders = ", ".join("?" for _ in insert_columns)

    row_id = execute(
        f"INSERT INTO history ({', '.join(insert_columns)}) VALUES ({placeholders})",
        tuple(insert_values),
    )
    created = fetch_one("SELECT * FROM history WHERE id = ?", (row_id,))
    return created


@router.delete("/history/{history_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_history_item(history_id: int):
    existing = fetch_one("SELECT id FROM history WHERE id = ?", (history_id,))
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="History item not found")
    execute("DELETE FROM history WHERE id = ?", (history_id,))


@router.delete("/history", status_code=status.HTTP_204_NO_CONTENT)
def clear_history():
    execute("DELETE FROM history")


@router.get("/favorites")
def list_favorites(limit: int = 100):
    safe_limit = min(max(limit, 1), 500)
    return fetch_all(
        """
        SELECT id, title, source_url, thumbnail_url, created_at
        FROM favorites
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (safe_limit,),
    )


@router.post("/favorites", status_code=status.HTTP_201_CREATED)
def add_favorite(item: FavoriteCreate):
    columns = get_table_columns("favorites")
    lookup_column = "source_url" if "source_url" in columns else "url"

    existing = fetch_one(f"SELECT * FROM favorites WHERE {lookup_column} = ?", (item.source_url,))
    if existing:
        return existing

    values: dict[str, object] = {
        "title": item.title,
        "source_url": item.source_url,
        "thumbnail_url": item.thumbnail_url,
    }
    if "url" in columns:
        values["url"] = item.source_url
    if "media_type" in columns:
        values["media_type"] = "video"

    insert_columns = [name for name in values.keys() if name in columns]
    insert_values = [values[name] for name in insert_columns]
    placeholders = ", ".join("?" for _ in insert_columns)

    row_id = execute(
        f"INSERT INTO favorites ({', '.join(insert_columns)}) VALUES ({placeholders})",
        tuple(insert_values),
    )
    return fetch_one("SELECT * FROM favorites WHERE id = ?", (row_id,))


@router.delete("/favorites/{favorite_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_favorite(favorite_id: int):
    existing = fetch_one("SELECT id FROM favorites WHERE id = ?", (favorite_id,))
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Favorite not found")
    execute("DELETE FROM favorites WHERE id = ?", (favorite_id,))


@router.get("/uploads")
def list_uploads(limit: int = 100):
    safe_limit = min(max(limit, 1), 500)
    return fetch_all(
        """
        SELECT id, filename, content_type, size_bytes, sha256, stored_path, created_at
        FROM uploads_metadata
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (safe_limit,),
    )


@router.get("/torrents")
def list_torrents(limit: int = 100):
    safe_limit = min(max(limit, 1), 500)
    return fetch_all(
        """
        SELECT id, external_id, source_type, title, status, progress, stream_url, created_at, updated_at
        FROM torrent_sessions
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (safe_limit,),
    )
