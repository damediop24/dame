from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.config import settings
from app.database import execute, fetch_one
from app.schemas import UploadMetadata

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.post("", response_model=UploadMetadata, status_code=status.HTTP_201_CREATED)
async def upload_file(file: UploadFile = File(...)):
    upload_dir = settings.resolve_upload_dir()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum size of {settings.max_upload_size_mb} MB",
        )

    sha256 = hashlib.sha256(content).hexdigest()
    safe_name = _sanitize_filename(file.filename or "upload.bin")
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    stored_path = upload_dir / stored_name
    stored_path.write_bytes(content)

    row_id = execute(
        """
        INSERT INTO uploads_metadata (filename, content_type, size_bytes, sha256, stored_path)
        VALUES (?, ?, ?, ?, ?)
        """,
        (file.filename or safe_name, file.content_type, len(content), sha256, str(stored_path.resolve())),
    )

    created = fetch_one(
        """
        SELECT id, filename, content_type, size_bytes, sha256, stored_path, created_at
        FROM uploads_metadata
        WHERE id = ?
        """,
        (row_id,),
    )

    if created is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Upload metadata missing")

    return UploadMetadata(**created)


def _sanitize_filename(name: str) -> str:
    stripped = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._")
    return stripped or "upload.bin"
