# syntax=docker/dockerfile:1

# --- Builder: resolve the locked environment with uv -------------------------
FROM ghcr.io/astral-sh/uv:0.10.7 AS uv

FROM python:3.12-slim-bookworm AS builder

COPY --from=uv /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependency layer: cached unless the lockfile or project metadata changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

# Project layer: install the application itself (non-editable, no source tree
# is required at runtime).
COPY app ./app
RUN uv sync --locked --no-dev --no-editable

# --- Runtime: Debian 12 with ffmpeg and VAAPI drivers ------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    HOME=/tmp

# Bookworm ships the Intel non-free driver outside main; enable the components
# explicitly rather than relying on host repositories.
RUN sed -i \
        's/^Components: main$/Components: main contrib non-free non-free-firmware/' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        vainfo \
        libva-drm2 \
        mesa-va-drivers \
        intel-media-va-driver-non-free \
        tini \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 hearth \
    && useradd --uid 1000 --gid hearth --create-home --shell /usr/sbin/nologin hearth \
    && mkdir -p /config /transcode \
    && chown hearth:hearth /config /transcode

WORKDIR /app
COPY --from=builder --chown=root:root /app/.venv /app/.venv

USER hearth

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('HEARTH_PORT', '8000')}/api/v1/health\", timeout=3)"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["hearth", "api"]
