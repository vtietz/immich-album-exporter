FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

FROM base AS runtime

RUN adduser --disabled-password --gecos "" appuser

USER appuser

ENTRYPOINT ["immich-album-exporter"]
CMD ["worker", "--config", "/config/config.yml"]

FROM base AS dev

USER root

RUN pip install --no-cache-dir ".[dev]"

ENTRYPOINT ["python", "-m", "immich_album_exporter"]
CMD ["sync-once", "--config", "/config/config.yml"]
