FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# ---- System deps ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ---- uv ----
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

WORKDIR /app

# ---- Install deps (cache) ----
COPY pyproject.toml uv.lock* ./
COPY packages/shared-protocol/pyproject.toml packages/shared-protocol/
COPY services/agent/pyproject.toml        services/agent/
RUN uv sync --no-install-project --package agent

# ---- Copy source ----
COPY packages/shared-protocol packages/shared-protocol
COPY services/agent            services/agent
RUN uv sync --package agent

EXPOSE 8765

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8765/health || exit 1

CMD ["uv", "run", "--package", "agent", "uvicorn", "agent.main:app", \
     "--host", "0.0.0.0", "--port", "8765"]