# syntax=docker/dockerfile:1.7
# Multi-stage build for the taquantgeo backend (FastAPI + jobs + CLI).

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.11.7

FROM python:${PYTHON_VERSION}-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgeos-dev libproj-dev gdal-bin libgdal-dev \
        ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/*

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM base AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM base AS runtime
WORKDIR /app
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:${PATH}"
RUN useradd --uid 1000 --create-home --shell /bin/bash taq && chown -R taq:taq /app
USER taq:taq
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "taquantgeo_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
