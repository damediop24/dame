# Vesper Stream

Vesper Stream is a production-ready monolithic full-stack streaming web application built with:

- Backend: Python 3.12+, FastAPI, Uvicorn
- Frontend: HTML5 + CSS3 + Vanilla JavaScript (no frameworks, no build tools)
- Database: SQLite
- Media Resolution: `yt-dlp` + `BeautifulSoup` + `httpx`
- Streaming: tokenized proxy, HTTP range support, HLS manifest rewriting
- Torrent Integration: AllDebrid (magnet + `.torrent` upload + status polling + stream retries)
- Deployment: Docker + Railway

The static frontend is served from `/public` and the API is served by FastAPI.

## Features

- Resolve playable media streams from URLs or search queries
- Tokenized expiring playback URLs (`/stream/{token}`)
- Proxy streaming that supports:
  - HTTP range requests (video seeking)
  - HLS manifest rewriting (`.m3u8`) so segment/key URLs stay proxied
  - Preservation of required playback headers
- Library endpoints backed by SQLite:
  - History
  - Favorites
  - Upload metadata
- AllDebrid torrent workflows:
  - Magnet link ingestion
  - `.torrent` upload
  - Progress polling
  - Streamability retries
  - Playback start when streamable links are available

## Project Structure

```text
.
├── app
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── security.py
│   ├── schemas.py
│   ├── routers
│   │   ├── media.py
│   │   ├── torrents.py
│   │   ├── library.py
│   │   └── uploads.py
│   ├── services
│   │   ├── resolver.py
│   │   └── alldebrid.py
│   └── utils
│       ├── headers.py
│       └── hls.py
├── public
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── data
├── requirements.txt
├── Dockerfile
├── railway.json
├── Procfile
└── .env.example
```

## Local Development

### 1. Requirements

- Python 3.12+
- `ffmpeg` (recommended for broader media compatibility)

### 2. Install

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
```

Important settings:

- `DATABASE_PATH`:
  - Local example: `./data/vesper.db`
  - Container example: `/data/vesper.db`
- `SECRET_KEY`: set a long random secret in production
- `ALLDEBRID_API_KEY`: required for torrent endpoints

### 4. Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 3000 --reload
```

Open:

- UI: [http://localhost:3000](http://localhost:3000)
- Health: [http://localhost:3000/health](http://localhost:3000/health)

## Docker

Build and run:

```bash
docker build -t vesper-stream .
docker run --rm -p 3000:3000 \
  -e PORT=3000 \
  -e DATABASE_PATH=/data/vesper.db \
  -e UPLOAD_DIR=/data/uploads \
  -e SECRET_KEY="replace-me" \
  -e ALLDEBRID_API_KEY="your-api-key" \
  -v vesper_data:/data \
  vesper-stream
```

Then open [http://localhost:3000](http://localhost:3000).

## Railway Deployment

This repo is ready for Railway using Docker (`railway.json` + `Dockerfile`).

1. Create a new Railway project from this repository.
2. Set environment variables:
   - `PORT` (Railway sets this automatically)
   - `DATABASE_PATH=/data/vesper.db`
   - `UPLOAD_DIR=/data/uploads`
   - `SECRET_KEY=<strong-random-value>`
   - `ALLDEBRID_API_KEY=<your-key>` (if using torrents)
3. Deploy. App starts on `0.0.0.0:$PORT`.

## API Overview

### Media

- `POST /api/media/resolve`
  - Body: `{ "query": "<url-or-search-text>" }`
- `POST /api/media/token`
  - Body: `{ "url": "https://...", "headers": {"referer": "..."}, "expires_in_seconds": 900 }`
- `GET /stream/{token}`
  - Tokenized proxy stream endpoint with range and HLS support

### Library

- `GET /api/library/history`
- `POST /api/library/history`
- `DELETE /api/library/history/{id}`
- `DELETE /api/library/history`
- `GET /api/library/favorites`
- `POST /api/library/favorites`
- `DELETE /api/library/favorites/{id}`
- `GET /api/library/uploads`
- `GET /api/library/torrents`

### Uploads

- `POST /api/uploads` (multipart file upload metadata + storage)

### Torrents (AllDebrid)

- `POST /api/torrents/magnet`
  - Body: `{ "magnet": "magnet:?xt=..." }`
- `POST /api/torrents/upload`
  - multipart `.torrent` file upload
- `GET /api/torrents/{id}/status`
- `POST /api/torrents/{id}/stream`
  - Body: `{ "retries": 20, "interval_seconds": 3 }` (both optional)

## Security Notes

- Stream URLs are HMAC-signed expiring tokens.
- Always set `SECRET_KEY` in production.
- Restrict CORS (`CORS_ORIGINS`) for production deployments.
- Store secrets in Railway variables, not in git.

## Database Schema

SQLite tables created at startup:

- `history`
- `favorites`
- `uploads_metadata`
- `torrent_sessions`

## Operational Notes

- If `DATABASE_PATH=/data/vesper.db` is not writable locally, app falls back to `./data/vesper.db`.
- For AllDebrid endpoints, `ALLDEBRID_API_KEY` must be configured.
- `yt-dlp` extraction quality depends on upstream providers and available formats.
