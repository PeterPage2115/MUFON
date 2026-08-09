# syntax=docker/dockerfile:1
# travian-discord-report-bot — production image (built by CI, T15).
#
# Strategy: two-stage. The builder installs uv via pip (ships a self-contained
# musl binary — no curl needed on alpine) and runs `uv sync --frozen --no-dev`
# into a venv at /opt/venv. The final stage copies the venv — a REAL (non-
# editable) install of the project, so the image does not depend on the source
# tree at runtime — plus src/ for reference. No build toolchain (gcc etc.)
# ever enters the final stage; the lockfile resolves to musllinux wheels for
# asyncpg>=0.29 / pydantic-core / aiohttp, so nothing compiles in the builder
# either.

# ---- builder --------------------------------------------------------------
FROM python:3.12-alpine AS builder

# uv via pip: single wheel install, no astral installer / curl required.
RUN pip install --no-cache-dir uv

WORKDIR /app
# README.md is required by hatchling (pyproject: readme = "README.md") when uv
# builds the project wheel.
COPY uv.lock pyproject.toml README.md ./
COPY src ./src

# --frozen: uv.lock is the source of truth (no re-resolution). --no-dev:
# runtime deps only. --no-editable: install the project as a real wheel into
# the venv. UV_PROJECT_ENVIRONMENT pins the venv path for the final stage.
RUN UV_PROJECT_ENVIRONMENT=/opt/venv uv sync --frozen --no-dev --no-editable

# ---- final ----------------------------------------------------------------
FROM python:3.12-alpine

# tzdata comes from pip (locked dep) — ZoneInfo(FETCH_TZ/REPORT_TZ) works on
# alpine without apk tzdata. Verified by the CI smoke test.
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/src /app/src

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    # SQLite lives on the travian-data volume mounted at /data.
    SQLITE_PATH=/data/travian.db \
    # Loopback by default; docker-compose overrides to 0.0.0.0 (the port is
    # published loopback-only on the HOST, so the dashboard never leaves
    # localhost).
    DASHBOARD_BIND=127.0.0.1

# Non-root runtime user; /data is the volume mount point and must be writable.
RUN adduser -D -u 10001 appuser \
    && mkdir -p /data \
    && chown appuser /data

WORKDIR /app
USER appuser

# /api/status is only 200 once the process passed startup validation — i.e.
# with a REAL DISCORD_TOKEN (main() exits 1 on validation failure BEFORE the
# dashboard thread starts). Without a valid env the container reports
# unhealthy; that is the intended signal.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/status', timeout=3)"]

CMD ["python", "-m", "travian.bot.main"]
