# OmniSimpleMem REST server — multimodal lifelong memory for sharp-fhir-mcp.
#
# Builds the OmniSimpleMem subproject straight from upstream
# (aiming-lab/SimpleMem), patches in a vendored config.py shim
# (config_shim.py) when upstream is missing it, and runs the FastAPI server
# via a custom entrypoint (server_entry.py) that honours OPENAI_API_KEY,
# OPENAI_API_BASE (OpenAI-compat / Ollama / OpenRouter / vLLM), local
# sentence-transformers embedding configs, and a persistent data dir.

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps:
#   ffmpeg, libsndfile1, libsm6, libxext6 — audio/video/image processing
#   git — clone upstream repo
#   build-essential — wheels for sentence-transformers / faiss / torch
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ffmpeg \
        libsndfile1 \
        libsm6 \
        libxext6 \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt

ARG SIMPLEMEM_REF=main
RUN git clone --depth 1 --branch ${SIMPLEMEM_REF} \
        https://github.com/aiming-lab/SimpleMem.git /opt/SimpleMem

WORKDIR /opt/SimpleMem/OmniSimpleMem

# Vendor the config.py shim if upstream still hasn't shipped one.
COPY config_shim.py /tmp/config_shim.py
RUN if [ ! -f omni_memory/core/config.py ]; then \
        echo "Upstream omni_memory/core/config.py missing — copying vendored shim"; \
        cp /tmp/config_shim.py omni_memory/core/config.py; \
    else \
        echo "Upstream config.py present — skipping shim"; \
    fi

# Install OmniSimpleMem with all optional groups (visual+audio+vector+server).
RUN pip install -e ".[all]"

# Custom server entrypoint — env-driven config.
COPY server_entry.py /opt/server_entry.py

# Pre-warm the default sentence-transformers model so first request is fast.
RUN python -c "from sentence_transformers import SentenceTransformer; \
               SentenceTransformer('all-MiniLM-L6-v2')" || true

ENV OMNI_MEMORY_DATA_DIR=/data \
    EMBEDDING_MODEL_NAME=all-MiniLM-L6-v2 \
    EMBEDDING_DIM=384 \
    LLM_MODEL=gpt-4o-mini \
    PYTHONPATH=/opt:/opt/SimpleMem/OmniSimpleMem

VOLUME ["/data"]
EXPOSE 8000

# Run via our wrapper which re-exports `app` with custom startup hook.
CMD ["uvicorn", "server_entry:app", "--host", "0.0.0.0", "--port", "8000"]
