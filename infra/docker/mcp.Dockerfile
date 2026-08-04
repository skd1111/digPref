# Generic MCP server Dockerfile. `MCP_PACKAGE` build-arg selects which
# sub-package to install (e.g. services/mcp-servers/mcp-server-database).
ARG PYTHON_VERSION=3.11-slim
FROM python:${PYTHON_VERSION}

ARG MCP_PACKAGE=invalid

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

WORKDIR /app

# ---- shared-protocol dep ----
COPY pyproject.toml uv.lock* ./
COPY packages/shared-protocol/pyproject.toml packages/shared-protocol/

# ---- the target MCP server ----
COPY ${MCP_PACKAGE}/pyproject.toml ${MCP_PACKAGE}/
COPY services/mcp-servers/_template_/pyproject.toml services/mcp-servers/_template_/

# Sentinel so uv can resolve
RUN mkdir -p packages/shared-protocol/src/protocol && \
    touch  packages/shared-protocol/src/protocol/__init__.py

RUN uv sync --no-install-project --package $(basename ${MCP_PACKAGE})

COPY packages/shared-protocol packages/shared-protocol
COPY ${MCP_PACKAGE} ${MCP_PACKAGE}
RUN uv sync --package $(basename ${MCP_PACKAGE})

ENV EAIDE_AUDIT_DB=/var/lib/eaide/audit/audit.sqlite

# Stdio MCP servers don't expose a port — they communicate via stdin/stdout.
# The Agent process spawns them as child processes.

CMD ["sh", "-c", "uv run --package $(basename ${MCP_PACKAGE}) $(basename ${MCP_PACKAGE})"]