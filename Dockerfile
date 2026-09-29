FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KARAOKE_BIND=0.0.0.0

RUN apt-get update -o Acquire::Retries=5 \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ffmpeg \
        libsndfile1 \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./

# Keep the default image CPU-only so it works on ordinary VPS instances.
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY app.py queue_manager.py audio_processing.py music_metadata.py cloud_storage.py ./
COPY static ./static

RUN mkdir -p data downloads models stems

EXPOSE 8010

VOLUME ["/app/data", "/app/downloads", "/app/models", "/app/stems"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl --fail http://127.0.0.1:8010/api/health || exit 1

CMD ["python", "app.py"]
