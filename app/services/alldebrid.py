from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings


class AllDebridError(RuntimeError):
    pass


class AllDebridClient:
    def __init__(self) -> None:
        parsed = urlparse(settings.alldebrid_api_base)
        self.base_origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "https://api.alldebrid.com"
        self.default_path_prefix = parsed.path.rstrip("/")
        self.api_key = settings.alldebrid_api_key
        self.timeout = settings.request_timeout_seconds

    def _ensure_configured(self) -> None:
        if not self.api_key:
            raise AllDebridError("AllDebrid API key is not configured. Set ALLDEBRID_API_KEY.")

    def _build_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if path.startswith("/v4"):
            return f"{self.base_origin}{path}"

        prefix = self.default_path_prefix if self.default_path_prefix else "/v4"
        return f"{self.base_origin}{prefix}/{path.lstrip('/')}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | list[tuple[str, Any]] | None = None,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_configured()
        url = self._build_url(path)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    files=files,
                    headers=headers,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise AllDebridError(f"AllDebrid request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise AllDebridError("AllDebrid returned non-JSON response") from exc

        if payload.get("status") != "success":
            error = payload.get("error") or {}
            code = error.get("code") if isinstance(error, dict) else None
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise AllDebridError(f"{code}: {message}" if code else (message or "AllDebrid returned an error status"))

        data_obj = payload.get("data")
        return data_obj if isinstance(data_obj, dict) else {"value": data_obj}

    async def upload_magnet(self, magnet: str) -> dict[str, Any]:
        data = await self._request("POST", "/v4/magnet/upload", data={"magnets[]": magnet})
        magnets = data.get("magnets") if isinstance(data.get("magnets"), list) else []
        if magnets:
            first = magnets[0]
            if isinstance(first, dict):
                return first
        return data

    async def upload_torrent_file(self, filename: str, content: bytes) -> dict[str, Any]:
        files = {"files[]": (filename, content, "application/x-bittorrent")}
        data = await self._request("POST", "/v4/magnet/upload/file", files=files)
        uploaded_files = data.get("files") if isinstance(data.get("files"), list) else []
        if uploaded_files:
            first = uploaded_files[0]
            if isinstance(first, dict):
                return first
        return data

    async def get_magnet_status(self, external_id: str) -> dict[str, Any]:
        data = await self._request("POST", "/v4.1/magnet/status", data={"id": external_id})
        return self._select_magnet(data, external_id)

    async def get_magnet_files(self, external_id: str) -> dict[str, Any]:
        data = await self._request("POST", "/v4/magnet/files", data={"id[]": external_id})
        return self._select_magnet(data, external_id)

    async def get_magnet_state(self, external_id: str) -> dict[str, Any]:
        status_payload = await self.get_magnet_status(external_id)
        files_payload = await self.get_magnet_files(external_id)
        links = self.extract_stream_links(files_payload)
        if not links:
            links = self.extract_stream_links(status_payload)

        return {
            "status": status_payload,
            "files": files_payload,
            "links": links,
        }

    async def unlock_link(self, link: str) -> str:
        data = await self._request("POST", "/v4/link/unlock", data={"link": link})
        resolved = await self._extract_link_from_data(data)
        if resolved:
            return resolved
        raise AllDebridError("Unable to unlock stream link")

    async def wait_until_streamable(
        self,
        external_id: str,
        retries: int,
        interval_seconds: float,
    ) -> dict[str, Any]:
        last_state: dict[str, Any] = {}
        for _ in range(max(1, retries)):
            state = await self.get_magnet_state(external_id)
            last_state = state
            if state.get("links"):
                return state
            await asyncio.sleep(max(0.5, interval_seconds))
        return last_state

    async def _extract_link_from_data(self, payload: dict[str, Any]) -> str | None:
        direct_link = payload.get("link")
        if isinstance(direct_link, str) and direct_link.startswith("http"):
            return direct_link

        delayed_id = payload.get("delayed")
        if delayed_id is not None:
            return await self._poll_delayed_link(str(delayed_id))

        streams = payload.get("streams")
        generation_id = payload.get("id")
        if isinstance(streams, list) and generation_id is not None:
            stream_id = self._pick_stream_id(streams)
            stream_payload = await self._request(
                "POST",
                "/v4/link/streaming",
                data={"id": generation_id, "stream": stream_id},
            )
            return await self._extract_link_from_data(stream_payload)

        return None

    async def _poll_delayed_link(self, delayed_id: str) -> str | None:
        for _ in range(40):
            delayed_payload = await self._request("POST", "/v4/link/delayed", data={"id": delayed_id})
            link = delayed_payload.get("link")
            if isinstance(link, str) and link.startswith("http"):
                return link

            status_value = delayed_payload.get("status")
            if status_value == 2:
                return None

            wait_for = delayed_payload.get("time_left")
            if isinstance(wait_for, (int, float)):
                await asyncio.sleep(max(1.0, min(5.0, float(wait_for))))
            else:
                await asyncio.sleep(2.0)
        return None

    def _pick_stream_id(self, streams: list[Any]) -> str:
        best_stream: dict[str, Any] | None = None
        best_size = -1
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            size = stream.get("filesize")
            size_value = int(size) if isinstance(size, int) or (isinstance(size, str) and size.isdigit()) else 0
            if size_value >= best_size:
                best_size = size_value
                best_stream = stream

        if best_stream and best_stream.get("id") is not None:
            return str(best_stream["id"])
        if streams and isinstance(streams[0], dict) and streams[0].get("id") is not None:
            return str(streams[0]["id"])
        raise AllDebridError("No valid stream id available")

    def _select_magnet(self, payload: dict[str, Any], external_id: str) -> dict[str, Any]:
        magnets = payload.get("magnets")
        if isinstance(magnets, dict):
            return magnets
        if isinstance(magnets, list):
            for item in magnets:
                if isinstance(item, dict) and str(item.get("id")) == str(external_id):
                    return item
            for item in magnets:
                if isinstance(item, dict):
                    return item

        if isinstance(payload, dict):
            return payload
        return {}

    def extract_stream_links(self, payload: dict[str, Any]) -> list[str]:
        links: list[str] = []

        def collect(value: Any) -> None:
            if isinstance(value, str) and value.startswith("http"):
                links.append(value)
                return
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key.lower() in {"link", "url", "l"} and isinstance(nested, str) and nested.startswith("http"):
                        links.append(nested)
                    else:
                        collect(nested)
                return
            if isinstance(value, list):
                for item in value:
                    collect(item)

        collect(payload)

        ordered: list[str] = []
        for link in links:
            if link not in ordered:
                ordered.append(link)

        return ordered


alldebrid_client = AllDebridClient()
