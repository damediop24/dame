from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass

import httpx

from app.config import settings


@dataclass
class PrebufferEntry:
    content: bytes
    content_type: str | None
    total_size: int | None
    created_at: float
    last_access: float


class PrebufferCache:
    def __init__(self) -> None:
        self._entries: dict[str, PrebufferEntry] = {}

    def make_cache_key(self, url: str, headers: dict[str, str]) -> str:
        normalized_headers = {k.lower(): v for k, v in sorted(headers.items(), key=lambda item: item[0].lower())}
        payload = json.dumps({"url": url, "headers": normalized_headers}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def prebuffer(
        self,
        *,
        url: str,
        headers: dict[str, str],
        max_bytes: int,
        timeout_seconds: float,
    ) -> tuple[str, PrebufferEntry]:
        requested_bytes = max(1024, min(max_bytes, 268435456))

        request_headers = dict(headers)
        request_headers["range"] = f"bytes=0-{requested_bytes - 1}"

        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
            response = await client.get(url, headers=request_headers)
            response.raise_for_status()
            content = await response.aread()

        total_size = self._parse_total_size(response.headers.get("content-range"), response.headers.get("content-length"))
        now = time.time()

        entry = PrebufferEntry(
            content=content,
            content_type=response.headers.get("content-type"),
            total_size=total_size,
            created_at=now,
            last_access=now,
        )

        cache_key = self.make_cache_key(url, headers)
        self._entries[cache_key] = entry
        self._evict_if_needed()
        return cache_key, entry

    def get_cached_range(self, cache_key: str, start: int, end: int | None) -> tuple[bytes, dict[str, str]] | None:
        entry = self._entries.get(cache_key)
        if not entry:
            return None

        available_end = len(entry.content) - 1
        if available_end < 0 or start < 0:
            return None

        if end is None:
            if entry.total_size is not None and entry.total_size - 1 <= available_end:
                end = entry.total_size - 1
            else:
                return None

        if start > end or end > available_end:
            return None

        entry.last_access = time.time()
        content = entry.content[start : end + 1]

        total_size = entry.total_size
        if total_size is None:
            total_part = "*"
        else:
            total_part = str(total_size)

        headers = {
            "Content-Range": f"bytes {start}-{end}/{total_part}",
            "Content-Length": str(len(content)),
            "Accept-Ranges": "bytes",
        }
        if entry.content_type:
            headers["Content-Type"] = entry.content_type

        return content, headers

    def _evict_if_needed(self) -> None:
        max_entries = max(1, settings.prebuffer_cache_entries)
        max_bytes = max(1024 * 1024, settings.prebuffer_cache_max_bytes)

        def total_bytes() -> int:
            return sum(len(entry.content) for entry in self._entries.values())

        if len(self._entries) <= max_entries and total_bytes() <= max_bytes:
            return

        items = sorted(self._entries.items(), key=lambda item: item[1].last_access)
        for cache_key, _ in items:
            if len(self._entries) <= max_entries and total_bytes() <= max_bytes:
                break
            self._entries.pop(cache_key, None)

    def _parse_total_size(self, content_range: str | None, content_length: str | None) -> int | None:
        if content_range:
            match = re.search(r"/(\d+)$", content_range.strip())
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    return None

        if content_length:
            try:
                return int(content_length)
            except ValueError:
                return None

        return None


prebuffer_cache = PrebufferCache()
