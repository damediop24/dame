FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY public ./public
COPY README.md ./README.md
COPY run.py ./run.py

ENV PORT=3000 \
    DATABASE_PATH=/data/vesper.db \
    UPLOAD_DIR=/data/uploads \
    PUBLIC_DIR=/app/public

EXPOSE 3000

CMD ["python", "run.py"]
