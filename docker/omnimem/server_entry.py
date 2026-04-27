"""Custom OmniSimpleMem ASGI entrypoint.

The bundled ``omni_memory.app`` hard-codes a small init that only reads
``OPENAI_API_KEY`` / ``OPENAI_API_BASE``. This wrapper re-uses the bundled
FastAPI ``app`` but replaces its startup handler to honour additional env
vars so we can drive embedding model, LLM model, OpenAI-compatible base URL,
and data dir from docker-compose:

    OPENAI_API_KEY            Required for LLM operations (summary/query/caption).
    OPENAI_API_BASE           Optional — point at any OpenAI-compatible API
                              (OpenRouter, Ollama OpenAI-mode, vLLM, etc.).
    LLM_MODEL                 Override summary/query/caption models in one shot.
    EMBEDDING_MODEL_NAME      Defaults to ``all-MiniLM-L6-v2`` (local).
    EMBEDDING_DIM             Must match the embedding model (384 for MiniLM).
    OMNI_MEMORY_DATA_DIR      Persistent data dir (default ``/data``).
    DEBUG_MODE                ``1`` enables debug logs.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import omni_memory.app as _omniapp
from omni_memory.app import app  # noqa: F401  re-exported for uvicorn
from omni_memory.core.config import LLMConfig, OmniMemoryConfig
from omni_memory.orchestrator import OmniMemoryOrchestrator

logger = logging.getLogger("omnimem.entry")


def _build_config() -> OmniMemoryConfig:
    cfg = OmniMemoryConfig()
    cfg.llm.api_key = os.getenv("OPENAI_API_KEY") or cfg.llm.api_key
    if base := os.getenv("OPENAI_API_BASE"):
        cfg.llm.api_base_url = base
    if model := os.getenv("LLM_MODEL"):
        cfg.set_unified_model(model)
    if emb_model := os.getenv("EMBEDDING_MODEL_NAME"):
        cfg.embedding.model_name = emb_model
    if emb_dim := os.getenv("EMBEDDING_DIM"):
        try:
            cfg.embedding.embedding_dim = int(emb_dim)
        except ValueError:
            pass
    if os.getenv("DEBUG_MODE", "").lower() in {"1", "true", "yes"}:
        cfg.debug_mode = True
        cfg.log_level = "DEBUG"
    return cfg


# Replace the bundled startup handler to honour the broader env surface.
app.router.on_startup = []  # drop the bundled handler


@app.on_event("startup")
async def _custom_startup() -> None:
    cfg = _build_config()
    data_dir = os.getenv("OMNI_MEMORY_DATA_DIR", "/data")
    cfg.storage.base_dir = data_dir
    cfg.storage.cold_storage_dir = f"{data_dir}/cold_storage"
    cfg.storage.index_dir = f"{data_dir}/index"
    cfg.ensure_directories()

    _omniapp.orchestrator = OmniMemoryOrchestrator(config=cfg, data_dir=data_dir)
    logger.info(
        "Omni-Memory started | embedding=%s dim=%d | llm=%s | base=%s | data=%s",
        cfg.embedding.model_name,
        cfg.embedding.embedding_dim,
        cfg.llm.summary_model,
        cfg.llm.api_base_url or "(default OpenAI)",
        data_dir,
    )


@app.on_event("shutdown")
async def _custom_shutdown() -> None:
    orch: Optional[OmniMemoryOrchestrator] = _omniapp.orchestrator
    if orch is not None:
        try:
            orch.close()
        except Exception:  # noqa: BLE001
            logger.exception("Error closing orchestrator")
        _omniapp.orchestrator = None
