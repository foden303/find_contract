FROM python:3.12-slim

# curl is only here so the container can health-check itself
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so code edits do not invalidate the wheel cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ ./core/
COPY web/ ./web/
COPY main.py ./

# Databases and uploads live here; the compose file mounts a volume over it
RUN mkdir -p /data /app/.uploads
ENV FINDER_CACHE_DIR=/data \
    FINDER_HOST=0.0.0.0 \
    FINDER_PORT=8765 \
    PYTHONUNBUFFERED=1

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8765/ >/dev/null || exit 1

CMD ["python", "-m", "web.app"]
