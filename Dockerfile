# sharp-fhir-mcp — SHARP-on-MCP compliant FHIR R4 MCP server.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Build deps for any wheels that need C extensions (httpx → no, but pydantic
# core may pull a wheel; keep gcc light just in case).
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

# Install the package + the optional `memory` group (mem0ai). Skipping the
# group is fine; the memory_* tools are simply not registered when mem0 is
# unavailable.
RUN pip install -e ".[memory]"

ENV HOST=0.0.0.0 \
    PORT=8000 \
    LOG_LEVEL=INFO \
    MEM0_DATA_DIR=/data/mem0

VOLUME ["/data"]
EXPOSE 8000

# fastmcp 2.x CLI auto-discovers `mcp` in src/sharp_fhir_mcp/server.py.
# We use the package's own main() which calls mcp.run(transport="http", ...)
# so the SHARP middleware (added at module import) is in place.
CMD ["sharp-fhir-mcp"]
