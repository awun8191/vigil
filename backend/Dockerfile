# syntax=docker/dockerfile:1
FROM python:3.15-slim-bookworm

# Install system dependencies (curl for healthchecks, libpq for PostgreSQL)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Install uv binary from official distribution
COPY --from=ghcr.io/astral-sh/uv:0.4.29 /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Enable bytecode optimization and configure virtualenv PATH
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Cache package dependencies
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source code
COPY src ./src

# Install project into virtual environment
RUN uv sync --frozen --no-dev

# Expose standard API port
EXPOSE 8000

# Default entrypoint starts the FastAPI server
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
