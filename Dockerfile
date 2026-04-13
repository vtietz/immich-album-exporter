FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY requirements.txt requirements-dev.txt /app/
COPY src /app/src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

FROM base AS runtime

RUN adduser --disabled-password --gecos "" appuser

USER appuser

ENTRYPOINT ["immich-album-exporter"]
CMD ["worker", "--config", "/config/config.yml"]

FROM base AS dev

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements-dev.txt

ENTRYPOINT ["python", "-m", "immich_album_exporter"]
CMD ["sync-once", "--config", "/config/config.yml"]
