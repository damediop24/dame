from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, Response, StreamingResponse

from app.config import settings
from app.schemas import PrebufferRequest, PrebufferResponse, ResolveRequest, ResolveResponse, TokenRequest, TokenResponse
from app.security import issue_stream_token, verify_stream_token
from app.services.prebuffer import prebuffer_cache
from app.services.resolver import media_resolver
from app.utils.headers import FORWARDED_REQUEST_HEADERS, passthrough_response_headers, sanitize_headers
from app.utils.hls import rewrite_hls_manifest

api_router = APIRouter(prefix="/api/media", tags=["media"])
stream_router = APIRouter(tags=["stream"])

RANGE_PATTERN = re.compile(r"^bytes=(\d+)-(\d*)$", re.IGNORECASE)


@api_router.post("/resolve", response_model=ResolveResponse)
async def resolve_media(payload: ResolveRequest) -> ResolveResponse:
    items = await media_resolver.resolve(payload.query)
    return ResolveResponse(items=items)


@api_router.post("/token", response_model=TokenResponse)
async def create_stream_token(payload: TokenRequest) -> TokenResponse:
    if not _is_http_url(payload.url):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only HTTP(S) media URLs are supported")

    clean_headers = sanitize_headers(payload.headers)
    token, expires_at = issue_stream_token(
        {"url": payload.url, "headers": clean_headers},
        expires_in_seconds=payload.expires_in_seconds,
    )

    return TokenResponse(token=token, stream_url=f"/stream/{token}", expires_at=expires_at)


@api_router.post("/prebuffer", response_model=PrebufferResponse)
async def prebuffer_media(payload: PrebufferRequest) -> PrebufferResponse:
    token_payload = verify_stream_token(payload.token)
    target_url = str(token_payload.get("url") or "")
    if not _is_http_url(target_url):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid stream URL")

    token_headers = sanitize_headers(token_payload.get("headers") or {})
    requested_bytes = payload.max_bytes or settings.prebuffer_max_bytes

    try:
        _, entry = await prebuffer_cache.prebuffer(
            url=target_url,
            headers=token_headers,
            max_bytes=requested_bytes,
            timeout_seconds=settings.request_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Prebuffer failed: {exc}") from exc

    return PrebufferResponse(
        cached_bytes=len(entry.content),
        requested_bytes=requested_bytes,
        total_size_bytes=entry.total_size,
    )


@stream_router.api_route("/stream/{token}", methods=["GET", "HEAD"])
async def stream_tokenized_media(token: str, request: Request):
    payload = verify_stream_token(token)

    target_url = str(payload.get("url") or "")
    if not _is_http_url(target_url):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid stream URL")

    token_headers = sanitize_headers(payload.get("headers") or {})

    requested_range = _parse_range_header(request.headers.get("range"))
    if requested_range is not None:
        cache_key = prebuffer_cache.make_cache_key(target_url, token_headers)
        cached = prebuffer_cache.get_cached_range(cache_key, requested_range[0], requested_range[1])
        if cached is not None:
            cached_content, cached_headers = cached
            if request.method.upper() == "HEAD":
                return Response(status_code=status.HTTP_206_PARTIAL_CONTENT, headers=cached_headers)
            return Response(
                content=cached_content,
                status_code=status.HTTP_206_PARTIAL_CONTENT,
                headers=cached_headers,
                media_type=cached_headers.get("Content-Type"),
            )

    upstream_headers = _merge_headers(token_headers, request)

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.request_timeout_seconds, connect=settings.request_timeout_seconds),
        follow_redirects=True,
    )

    try:
        upstream_request = client.build_request(request.method, target_url, headers=upstream_headers)
        upstream_response = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Upstream request failed: {exc}") from exc

    content_type = upstream_response.headers.get("content-type", "")
    is_manifest = target_url.split("?")[0].lower().endswith(".m3u8") or "mpegurl" in content_type.lower()

    response_headers = passthrough_response_headers(upstream_response.headers.items())
    response_headers.setdefault("Accept-Ranges", "bytes")

    if is_manifest:
        raw = await upstream_response.aread()
        await upstream_response.aclose()
        await client.aclose()

        manifest_text = raw.decode(upstream_response.encoding or "utf-8", errors="replace")

        def token_factory(resolved_url: str) -> str:
            child_token, _ = issue_stream_token({"url": resolved_url, "headers": token_headers})
            return f"/stream/{child_token}"

        rewritten = rewrite_hls_manifest(manifest_text, target_url, token_factory)

        response_headers["content-type"] = "application/vnd.apple.mpegurl"
        response_headers.pop("content-length", None)

        return PlainTextResponse(
            rewritten,
            status_code=upstream_response.status_code,
            headers=response_headers,
            media_type="application/vnd.apple.mpegurl",
        )

    if request.method.upper() == "HEAD":
        await upstream_response.aclose()
        await client.aclose()
        return Response(status_code=upstream_response.status_code, headers=response_headers)

    async def body_iterator():
        try:
            async for chunk in upstream_response.aiter_bytes():
                yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    return StreamingResponse(
        body_iterator(),
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _merge_headers(token_headers: dict[str, str], request: Request) -> dict[str, str]:
    headers: dict[str, str] = dict(token_headers)
    for key, value in request.headers.items():
        normalized = key.lower()
        if normalized in FORWARDED_REQUEST_HEADERS and normalized not in headers:
            headers[normalized] = value

    range_header = request.headers.get("range")
    if range_header:
        headers["range"] = range_header

    return headers


def _parse_range_header(range_header: str | None) -> tuple[int, int | None] | None:
    if not range_header:
        return None

    match = RANGE_PATTERN.match(range_header.strip())
    if not match:
        return None

    start = int(match.group(1))
    end_text = match.group(2)
    end = int(end_text) if end_text else None

    if end is not None and end < start:
        return None

    return start, end
