from __future__ import annotations

import asyncio
import html
import re
import time
import urllib.parse
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from yt_dlp import YoutubeDL

from app.config import settings
from app.schemas import MediaItem, MediaStream


class _NoopYtDlpLogger:
    def debug(self, msg: str) -> None:
        return None

    def warning(self, msg: str) -> None:
        return None

    def error(self, msg: str) -> None:
        return None


class MediaResolver:
    def __init__(self) -> None:
        self._timeout = settings.request_timeout_seconds
        self._resolve_cache_ttl_seconds = max(30, settings.resolve_cache_ttl_seconds)
        self._resolve_cache: dict[str, tuple[float, list[MediaItem]]] = {}

    async def resolve(self, query: str) -> list[MediaItem]:
        normalized_query = query.strip()
        if not normalized_query:
            return []

        cached = self._get_cached(normalized_query)
        if cached is not None:
            return cached

        if self._is_url(normalized_query):
            custom_items = await self._resolve_custom_url(normalized_query)
            if custom_items:
                self._set_cached(normalized_query, custom_items)
                return custom_items

            timeout_at = time.monotonic() + max(5.0, settings.resolve_timeout_seconds)
            tasks = {
                asyncio.create_task(self._extract_with_ytdlp(normalized_query)),
                asyncio.create_task(self._scrape_as_items(normalized_query)),
            }

            try:
                while tasks and time.monotonic() < timeout_at:
                    done, pending = await asyncio.wait(
                        tasks,
                        timeout=max(0.1, timeout_at - time.monotonic()),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        break

                    for task in done:
                        try:
                            items = task.result()
                        except Exception:
                            items = []

                        if items:
                            for pending_task in pending:
                                pending_task.cancel()
                            self._set_cached(normalized_query, items)
                            return items

                    tasks = set(pending)
            finally:
                for task in tasks:
                    task.cancel()

            return []

        items = await self._extract_with_ytdlp(f"ytsearch8:{normalized_query}", is_search=True)
        if items:
            self._set_cached(normalized_query, items)
        return items

    def _get_cached(self, key: str) -> list[MediaItem] | None:
        entry = self._resolve_cache.get(key)
        if not entry:
            return None

        created_at, items = entry
        if (time.time() - created_at) > self._resolve_cache_ttl_seconds:
            self._resolve_cache.pop(key, None)
            return None

        return [item.model_copy(deep=True) for item in items]

    def _set_cached(self, key: str, items: list[MediaItem]) -> None:
        self._resolve_cache[key] = (time.time(), [item.model_copy(deep=True) for item in items])
        if len(self._resolve_cache) > 200:
            oldest = sorted(self._resolve_cache.items(), key=lambda kv: kv[1][0])[:50]
            for cache_key, _ in oldest:
                self._resolve_cache.pop(cache_key, None)

    async def _scrape_as_items(self, url: str) -> list[MediaItem]:
        item = await self._scrape_with_beautifulsoup(url)
        return [item] if item else []

    async def _resolve_custom_url(self, url: str) -> list[MediaItem]:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if "bunkr" in host:
            if parsed.path.startswith("/a/"):
                return await self._extract_bunkr_album(url)
            if parsed.path.startswith("/f/"):
                item = await self._extract_bunkr_file(url)
                return [item] if item else []
        return []

    async def _extract_bunkr_album(self, album_url: str) -> list[MediaItem]:
        headers = self._media_request_headers(album_url)
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            try:
                response = await client.get(album_url, headers=headers)
                response.raise_for_status()
            except httpx.HTTPError:
                return []

            webpage = response.text
            soup = BeautifulSoup(webpage, "html.parser")
            album_title = (
                (soup.find("meta", property="og:title") or {}).get("content")
                or (soup.title.string.strip() if soup.title and soup.title.string else "Bunkr Album")
            )

            raw_links = re.findall(r"href=['\"]([^'\"]+)['\"]", webpage)
            file_links: list[str] = []
            for raw_link in raw_links:
                absolute = urljoin(album_url, raw_link)
                parsed_link = urlparse(absolute)
                if not re.match(r"^/f/[A-Za-z0-9]+$", parsed_link.path):
                    continue
                if parsed_link.netloc.lower() and "bunkr" not in parsed_link.netloc.lower():
                    continue
                if absolute not in file_links:
                    file_links.append(absolute)

            max_items = 48
            file_links = file_links[:max_items]
            if not file_links:
                return []

            semaphore = asyncio.Semaphore(8)

            async def worker(link: str) -> MediaItem | None:
                async with semaphore:
                    return await self._extract_bunkr_file(link, client=client)

            tasks = [asyncio.create_task(worker(link)) for link in file_links]
            results: list[MediaItem] = []
            for task in tasks:
                try:
                    item = await task
                except Exception:
                    item = None
                if item and item.streams:
                    if item.title.strip().lower() in {"video", "file", "untitled"}:
                        item.title = f"{album_title}"
                    results.append(item)

            return results

    async def _extract_bunkr_file(self, file_url: str, client: httpx.AsyncClient | None = None) -> MediaItem | None:
        owns_client = client is None
        headers = self._media_request_headers(file_url)
        local_client = client or httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)

        try:
            response = await local_client.get(file_url, headers=headers)
            response.raise_for_status()
            webpage = response.text
            soup = BeautifulSoup(webpage, "html.parser")

            title = (
                (soup.find("meta", property="og:title") or {}).get("content")
                or (soup.title.string.strip() if soup.title and soup.title.string else "Bunkr File")
            )
            thumbnail = (soup.find("meta", property="og:image") or {}).get("content")

            js_cdn = self._extract_js_var(webpage, "jsCDN")
            js_type = self._extract_js_var(webpage, "jsType") or "video/mp4"
            sign_url = self._extract_js_var(webpage, "signUrl")
            cover = self._extract_js_var(webpage, "videoCoverUrl")

            stream_url: str | None = None
            if js_cdn:
                stream_url = await self._build_signed_bunkr_url(js_cdn, sign_url, local_client)

            if not stream_url:
                direct_candidates = re.findall(r"https?://[^\"'\s<>]+", webpage)
                for candidate in direct_candidates:
                    lower = candidate.lower()
                    if "/storage/media/" in lower or lower.endswith((".mp4", ".mov", ".webm", ".m3u8")):
                        stream_url = candidate
                        break

            if not stream_url:
                return None

            final_thumbnail = cover or thumbnail
            ext = stream_url.split("?")[0].rstrip("/").rsplit(".", 1)[-1].lower() if "." in stream_url else None

            return MediaItem(
                title=title,
                webpage_url=file_url,
                thumbnail=final_thumbnail,
                streams=[
                    MediaStream(
                        url=stream_url,
                        format_id="bunkr",
                        ext=ext,
                        quality=js_type,
                        is_hls=ext == "m3u8",
                        headers=headers,
                    )
                ],
            )
        except httpx.HTTPError:
            return None
        finally:
            if owns_client:
                await local_client.aclose()

    async def _build_signed_bunkr_url(self, raw_url: str, sign_url: str | None, client: httpx.AsyncClient) -> str:
        cleaned_raw = raw_url.replace("\\/", "/")
        if not sign_url:
            return cleaned_raw

        cleaned_sign = sign_url.replace("\\/", "/")
        parsed = urlparse(cleaned_raw)
        path = urllib.parse.unquote(parsed.path)

        try:
            response = await client.get(cleaned_sign, params={"path": path}, headers={"User-Agent": self._media_request_headers(cleaned_raw)["User-Agent"]})
            response.raise_for_status()
            payload = response.json()
            token = payload.get("token")
            ex = payload.get("ex")
            if token and ex:
                separator = "&" if parsed.query else "?"
                return f"{cleaned_raw}{separator}token={token}&ex={ex}"
        except (httpx.HTTPError, ValueError):
            return cleaned_raw

        return cleaned_raw

    def _extract_js_var(self, text: str, name: str) -> str | None:
        pattern = rf"var\s+{re.escape(name)}\s*=\s*['\"]([^'\"]+)['\"]"
        match = re.search(pattern, text)
        if not match:
            return None
        value = html.unescape(match.group(1)).replace("\\/", "/")
        return value.strip()

    def _is_url(self, value: str) -> bool:
        parsed = urlparse(value)
        return bool(parsed.scheme and parsed.netloc)

    async def _extract_with_ytdlp(self, query: str, is_search: bool = False) -> list[MediaItem]:
        def run_extract() -> dict:
            options = {
                "quiet": True,
                "no_warnings": True,
                "logger": _NoopYtDlpLogger(),
                "skip_download": True,
                "noplaylist": not is_search,
                "extract_flat": False,
                "socket_timeout": settings.resolve_timeout_seconds,
            }
            with YoutubeDL(options) as ydl:
                return ydl.extract_info(query, download=False)

        try:
            info = await asyncio.to_thread(run_extract)
        except Exception:
            return []

        entries = info.get("entries") if isinstance(info, dict) else None
        if entries:
            mapped = [self._map_entry(entry) for entry in entries if entry]
            return [item for item in mapped if item.streams]

        if isinstance(info, dict):
            item = self._map_entry(info)
            return [item] if item.streams else []

        return []

    def _map_entry(self, entry: dict) -> MediaItem:
        title = entry.get("title") or "Untitled"
        webpage_url = entry.get("webpage_url") or entry.get("original_url") or ""

        streams: list[MediaStream] = []
        formats = entry.get("formats") or []
        for fmt in formats:
            stream_url = fmt.get("url")
            if not stream_url:
                continue

            protocol = str(fmt.get("protocol") or "")
            ext = fmt.get("ext")
            is_hls = "m3u8" in protocol or ext == "m3u8"
            quality = (
                fmt.get("format_note")
                or fmt.get("quality")
                or fmt.get("resolution")
                or fmt.get("format")
            )

            http_headers = fmt.get("http_headers") or entry.get("http_headers") or {}
            safe_headers = {
                str(key): str(value)
                for key, value in http_headers.items()
                if isinstance(key, str) and isinstance(value, str)
            }

            streams.append(
                MediaStream(
                    url=stream_url,
                    format_id=str(fmt.get("format_id") or ""),
                    ext=ext,
                    quality=str(quality) if quality is not None else None,
                    is_hls=is_hls,
                    headers=safe_headers,
                )
            )

        direct_url = entry.get("url")
        if direct_url and not streams:
            streams.append(
                MediaStream(
                    url=direct_url,
                    format_id="direct",
                    ext=entry.get("ext"),
                    quality="source",
                    is_hls=str(entry.get("protocol") or "").startswith("m3u8")
                    or str(entry.get("ext") or "") == "m3u8",
                    headers={
                        str(k): str(v)
                        for k, v in (entry.get("http_headers") or {}).items()
                        if isinstance(k, str) and isinstance(v, str)
                    },
                )
            )

        return MediaItem(
            id=str(entry.get("id") or "") or None,
            title=title,
            webpage_url=webpage_url,
            thumbnail=entry.get("thumbnail"),
            duration_seconds=entry.get("duration"),
            streams=streams[:12],
        )

    async def _scrape_with_beautifulsoup(self, url: str) -> MediaItem | None:
        headers = self._media_request_headers(url)

        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
            except httpx.HTTPError:
                return None

        soup = BeautifulSoup(response.text, "html.parser")

        kvs_item = self._extract_kvs_item(url, response.text, soup, headers)
        if kvs_item and kvs_item.streams:
            return kvs_item

        title = (
            (soup.find("meta", property="og:title") or {}).get("content")
            or (soup.title.string.strip() if soup.title and soup.title.string else "Scraped Media")
        )

        thumbnail = (soup.find("meta", property="og:image") or {}).get("content")

        candidates: list[str] = []
        for tag in soup.select("video source[src]"):
            src = tag.get("src")
            if src:
                candidates.append(src)

        for selector in [
            'meta[property="og:video"]',
            'meta[property="og:video:url"]',
            'meta[name="twitter:player:stream"]',
        ]:
            tag = soup.select_one(selector)
            if tag and tag.get("content"):
                candidates.append(tag["content"])

        for anchor in soup.select("a[href]"):
            href = str(anchor.get("href"))
            lower = href.lower()
            if lower.endswith(".m3u8") or lower.endswith(".mp4") or lower.endswith(".mpd"):
                candidates.append(href)

        for script_url in re.findall(r"https?://[^\"'\s<>]+", response.text):
            lower = script_url.lower()
            if any(ext in lower for ext in (".m3u8", ".mp4", ".mpd", "/get_file/")) and ".mp4.jpg" not in lower:
                candidates.append(script_url)

        deduped: list[str] = []
        for item in candidates:
            candidate = urljoin(url, item)
            if candidate not in deduped:
                deduped.append(candidate)

        streams = [
            MediaStream(
                url=item,
                format_id="scraped",
                ext=(
                    item.split("?")[0].rstrip("/").rsplit(".", 1)[-1].lower()
                    if "." in item.split("?")[0].rstrip("/")
                    else None
                ),
                quality="source",
                is_hls=item.split("?")[0].rstrip("/").lower().endswith(".m3u8"),
                headers=headers,
            )
            for item in deduped
        ]

        if not streams:
            return None

        return MediaItem(
            title=title,
            webpage_url=url,
            thumbnail=thumbnail,
            duration_seconds=None,
            streams=streams,
        )

    def _extract_kvs_item(
        self,
        page_url: str,
        webpage: str,
        soup: BeautifulSoup,
        request_headers: dict[str, str],
    ) -> MediaItem | None:
        script_blocks = re.findall(r"(?is)<script\b[^>]*>(.*?)</script>", webpage)
        for block in script_blocks:
            if "kt_player(" not in block or "video_url" not in block:
                continue

            pairs = {
                key: self._unescape_js(value)
                for key, value in re.findall(r"([A-Za-z_]\w*)\s*:\s*'((?:\\.|[^'])*)'", block)
            }
            if "video_url" not in pairs:
                continue

            license_code = pairs.get("license_code", "")
            title = (
                pairs.get("video_title")
                or (soup.find("meta", property="og:title") or {}).get("content")
                or (soup.title.string.strip() if soup.title and soup.title.string else "Scraped Media")
            )
            thumbnail = pairs.get("preview_url") or (soup.find("meta", property="og:image") or {}).get("content")

            streams: list[MediaStream] = []
            url_keys = sorted(
                [key for key in pairs.keys() if re.match(r"^video_(?:url|alt_url\d*)$", key)],
                key=lambda value: (0 if value == "video_url" else 1, value),
            )

            for key in url_keys:
                source_url = pairs.get(key, "")
                if "/get_file/" not in source_url:
                    continue

                real_url = self._kvs_get_real_url(source_url, license_code)
                if not real_url:
                    continue

                absolute_url = urljoin(page_url, real_url)
                path_no_query = absolute_url.split("?")[0].rstrip("/")
                ext = path_no_query.rsplit(".", 1)[-1].lower() if "." in path_no_query else None
                if ext == "jpg":
                    continue

                quality = pairs.get(f"{key}_text") or key.replace("_", " ")
                streams.append(
                    MediaStream(
                        url=absolute_url,
                        format_id=key,
                        ext=ext,
                        quality=quality,
                        is_hls=ext == "m3u8",
                        headers=request_headers,
                    )
                )

            deduped_streams: list[MediaStream] = []
            seen_urls: set[str] = set()
            for stream in streams:
                if stream.url in seen_urls:
                    continue
                seen_urls.add(stream.url)
                deduped_streams.append(stream)

            if deduped_streams:
                return MediaItem(
                    id=pairs.get("video_id") or None,
                    title=title,
                    webpage_url=page_url,
                    thumbnail=urljoin(page_url, thumbnail) if thumbnail else None,
                    duration_seconds=None,
                    streams=deduped_streams,
                )

        return None

    def _media_request_headers(self, page_url: str) -> dict[str, str]:
        parsed = urlparse(page_url)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            ),
            "Referer": page_url,
        }
        if origin:
            headers["Origin"] = origin
        return headers

    def _unescape_js(self, value: str) -> str:
        unescaped = value.replace("\\/", "/")
        return bytes(unescaped, "utf-8").decode("unicode_escape")

    def _kvs_get_real_url(self, video_url: str, license_code: str) -> str:
        if not video_url.startswith("function/0/"):
            return video_url
        if not license_code:
            return video_url

        parsed = urllib.parse.urlparse(video_url[len("function/0/") :])
        url_parts = parsed.path.split("/")
        if len(url_parts) < 4:
            return parsed.geturl()

        hash_length = 32
        hash_value = url_parts[3][:hash_length]
        if len(hash_value) < hash_length:
            return parsed.geturl()

        token = self._kvs_get_license_token(license_code)
        if len(token) < hash_length:
            return parsed.geturl()

        indices = list(range(hash_length))
        accum = 0
        for src in reversed(range(hash_length)):
            accum += token[src]
            dest = (src + accum) % hash_length
            indices[src], indices[dest] = indices[dest], indices[src]

        remapped_hash = "".join(hash_value[index] for index in indices)
        url_parts[3] = remapped_hash + url_parts[3][hash_length:]
        return urllib.parse.urlunparse(parsed._replace(path="/".join(url_parts)))

    def _kvs_get_license_token(self, license_code: str) -> list[int]:
        code = license_code.replace("$", "")
        if not code.isdigit():
            return []

        values = [int(char) for char in code]
        mod_license = code.replace("0", "1")
        center = len(mod_license) // 2
        front_half = int(mod_license[: center + 1])
        back_half = int(mod_license[center:])
        mod_license = str(4 * abs(front_half - back_half))[: center + 1]

        token: list[int] = []
        for index, current in enumerate(map(int, mod_license)):
            for offset in range(4):
                if index + offset >= len(values):
                    continue
                token.append((values[index + offset] + current) % 10)
        return token


media_resolver = MediaResolver()
