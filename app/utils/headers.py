from __future__ import annotations

from typing import Iterable

PASSTHROUGH_RESPONSE_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-length",
    "content-range",
    "content-type",
    "etag",
    "expires",
    "last-modified",
}

FORWARDED_REQUEST_HEADERS = {
    "accept",
    "accept-language",
    "origin",
    "range",
    "referer",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
    "user-agent",
}


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        normalized = key.strip().lower()
        if not normalized or not value:
            continue
        if normalized in {"connection", "host", "transfer-encoding"}:
            continue
        sanitized[normalized] = value.strip()
    return sanitized


def passthrough_response_headers(source_headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    output: dict[str, str] = {}
    for key, value in source_headers:
        if key.lower() in PASSTHROUGH_RESPONSE_HEADERS:
            output[key] = value
    return output
