from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import HTTPException, status

from app.config import settings


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def issue_stream_token(payload: dict[str, Any], expires_in_seconds: int | None = None) -> tuple[str, int]:
    ttl = expires_in_seconds or settings.token_ttl_seconds
    expires_at = int(time.time()) + int(ttl)
    envelope = {"exp": expires_at, "payload": payload}

    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    body_enc = _b64_encode(body)
    signature = hmac.new(settings.secret_key.encode("utf-8"), body_enc.encode("utf-8"), hashlib.sha256).digest()
    signature_enc = _b64_encode(signature)

    return f"{body_enc}.{signature_enc}", expires_at


def verify_stream_token(token: str) -> dict[str, Any]:
    try:
        body_enc, signature_enc = token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token format") from exc

    expected_sig = hmac.new(settings.secret_key.encode("utf-8"), body_enc.encode("utf-8"), hashlib.sha256).digest()
    try:
        provided_sig = _b64_decode(signature_enc)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token signature") from exc

    if not hmac.compare_digest(expected_sig, provided_sig):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token signature")

    try:
        envelope = json.loads(_b64_decode(body_enc).decode("utf-8"))
    except (ValueError, binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token payload") from exc

    expires_at = int(envelope.get("exp", 0))
    if expires_at < int(time.time()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")

    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    return payload
