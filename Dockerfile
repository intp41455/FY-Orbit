# Stage 1: Build dependencies with uv
FROM python:3.13-slim AS builder

WORKDIR /app

# Install essentials for building and compiling if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gcc python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv from official binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy dependency definition
COPY pyproject.toml ./

# Create virtualenv and install production dependencies (excluding GUI extras)
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN uv pip install --no-cache -r pyproject.toml

# Copy source and install package metadata
COPY src/ ./src/
RUN uv pip install --no-cache --no-deps -e .

# Stage 2: Minimal runtime
FROM python:3.13-slim AS runtime

WORKDIR /app

# Install curl for healthcheck and ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Create non-root application user
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/bash -m appuser

# Copy application files
COPY --chown=appuser:appgroup alembic.ini ./
COPY --chown=appuser:appgroup migrations/ ./migrations/
COPY --chown=appuser:appgroup src/ ./src/
COPY --chown=appuser:appgroup pyproject.toml ./
COPY --chown=appuser:appgroup web/dist/ /app/web/dist/
ENV FY_STATIC_DIR=/app/web/dist

# Create runtime directories for local assets/artifacts with non-root ownership
RUN mkdir -p /app/.runtime/artifacts /app/.runtime/assets && \
    chown -R appuser:appgroup /app/.runtime

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "find_yourself.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
