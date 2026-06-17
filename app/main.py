from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.routers.library import router as library_router
from app.routers.media import api_router as media_api_router
from app.routers.media import stream_router
from app.routers.torrents import router as torrents_router
from app.routers.uploads import router as uploads_router

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    init_db()
    settings.resolve_upload_dir()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(media_api_router)
app.include_router(stream_router)
app.include_router(library_router)
app.include_router(uploads_router)
app.include_router(torrents_router)

public_dir = settings.resolve_public_dir()
app.mount("/public", StaticFiles(directory=public_dir), name="public")


@app.get("/")
def index():
    index_file = public_dir / "index.html"
    return FileResponse(index_file)
