from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Generator, Iterable

from app.config import settings


DB_PATH = settings.resolve_database_path()


def _dict_factory(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                playback_url TEXT,
                position_seconds REAL DEFAULT 0,
                duration_seconds REAL,
                watched_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                thumbnail_url TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_url)
            );

            CREATE TABLE IF NOT EXISTS uploads_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                content_type TEXT,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS torrent_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_id TEXT NOT NULL UNIQUE,
                source_type TEXT NOT NULL,
                title TEXT,
                status TEXT,
                progress REAL DEFAULT 0,
                stream_url TEXT,
                raw_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        _ensure_columns(
            conn,
            "history",
            {
                "source_url": "TEXT NOT NULL DEFAULT ''",
                "playback_url": "TEXT",
                "position_seconds": "REAL DEFAULT 0",
                "duration_seconds": "REAL",
                "watched_at": "TEXT",
            },
        )
        _ensure_columns(
            conn,
            "favorites",
            {
                "source_url": "TEXT NOT NULL DEFAULT ''",
                "thumbnail_url": "TEXT",
                "created_at": "TEXT",
            },
        )
        _ensure_columns(
            conn,
            "uploads_metadata",
            {
                "content_type": "TEXT",
                "size_bytes": "INTEGER NOT NULL DEFAULT 0",
                "sha256": "TEXT NOT NULL DEFAULT ''",
                "stored_path": "TEXT NOT NULL DEFAULT ''",
                "created_at": "TEXT",
            },
        )
        _ensure_columns(
            conn,
            "torrent_sessions",
            {
                "source_type": "TEXT NOT NULL DEFAULT 'magnet'",
                "title": "TEXT",
                "status": "TEXT",
                "progress": "REAL DEFAULT 0",
                "stream_url": "TEXT",
                "raw_json": "TEXT",
                "created_at": "TEXT",
                "updated_at": "TEXT",
            },
        )
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_history_watched_at ON history(watched_at DESC);
            CREATE INDEX IF NOT EXISTS idx_favorites_created_at ON favorites(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_uploads_created_at ON uploads_metadata(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_torrents_updated_at ON torrent_sessions(updated_at DESC);
            """
        )


def _ensure_columns(conn: sqlite3.Connection, table: str, required_columns: dict[str, str]) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing = {row["name"] for row in rows}
    for column_name, column_def in required_columns.items():
        if column_name in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_def}")


def fetch_all(query: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with get_connection() as conn:
        cur = conn.execute(query, tuple(params))
        return cur.fetchall()


def fetch_one(query: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    with get_connection() as conn:
        cur = conn.execute(query, tuple(params))
        return cur.fetchone()


def execute(query: str, params: Iterable[Any] = ()) -> int:
    with get_connection() as conn:
        cur = conn.execute(query, tuple(params))
        return int(cur.lastrowid)


def get_table_columns(table: str) -> set[str]:
    with get_connection() as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def upsert_torrent_session(
    external_id: str,
    source_type: str,
    title: str | None,
    status: str | None,
    progress: float | None,
    stream_url: str | None,
    raw_json: dict[str, Any] | None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO torrent_sessions (external_id, source_type, title, status, progress, stream_url, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(external_id) DO UPDATE SET
                source_type=excluded.source_type,
                title=COALESCE(excluded.title, torrent_sessions.title),
                status=COALESCE(excluded.status, torrent_sessions.status),
                progress=COALESCE(excluded.progress, torrent_sessions.progress),
                stream_url=COALESCE(excluded.stream_url, torrent_sessions.stream_url),
                raw_json=COALESCE(excluded.raw_json, torrent_sessions.raw_json),
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                external_id,
                source_type,
                title,
                status,
                progress,
                stream_url,
                json.dumps(raw_json) if raw_json is not None else None,
            ),
        )
