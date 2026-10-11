# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------- builder
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Dependencies first so source edits don't invalidate the cached layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --extra observability --no-install-project

# ---------------------------------------------------------------------------- runtime
FROM python:3.13-slim-trixie AS runtime

ARG APP_VERSION=2.0.0
LABEL org.opencontainers.image.title="sleeptrack-api" \
      org.opencontainers.image.description="SleepTrack API" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.source="https://github.com/KhaledSaeed18/sleep-track-backend" \
      org.opencontainers.image.licenses="MIT"

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ENVIRONMENT=production \
    PORT=8000

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app app ./app

USER app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '8000')}/health/live\", timeout=2)"]

CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS:-127.0.0.1}\" --no-server-header"]
