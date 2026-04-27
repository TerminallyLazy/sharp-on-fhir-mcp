"""Optional mem0 (https://github.com/mem0ai/mem0) clinical-memory client.

Why mem0:
    * Apache-2.0, 50k+ stars, actively maintained
    * Embedded SDK — no separate service / database / dashboard required
    * Built-in OpenAI-compatible LLM support (OpenAI proper, Ollama,
      OpenRouter, vLLM, LM Studio …) for memory extraction + summarisation
    * Defaults to local sentence-transformers / Chroma if you don't pass
      OpenAI keys, but auto-extraction quality is better with an LLM

The mem0 SDK is sync; this wrapper offloads each call to a worker thread
via ``asyncio.to_thread`` so it composes cleanly with FastMCP's async tools.

Memory scoping:
    * ``user_id``  — FHIR Patient/<id> (one bucket per patient)
    * ``agent_id`` — fixed: ``sharp-fhir-mcp``
    * ``run_id``   — optional MCP session id

Environment configuration is fully delegated to mem0's
:func:`mem0.Memory.from_config`. We construct the config dict from a
small set of env vars so the user only needs to set ``OPENAI_API_KEY``
(or ``OPENAI_API_BASE`` for an OpenAI-compatible endpoint).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Where mem0 persists its vector store + history db inside the container.
DEFAULT_MEM0_DATA_DIR = "/data/mem0"

AGENT_ID = "sharp-fhir-mcp"


class Mem0Error(RuntimeError):
    """Raised when the mem0 SDK call fails."""


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _build_config() -> dict[str, Any]:
    """Build a mem0 Memory config dict from env vars.

    Env vars consumed:
        OPENAI_API_KEY        Required for LLM-driven memory extraction.
        OPENAI_API_BASE       Optional — switch to OpenAI-compat provider.
        MEM0_LLM_MODEL        LLM model name (default ``gpt-4o-mini``).
        MEM0_EMBED_MODEL      Embedder name (default
                              ``text-embedding-3-small``; for local Ollama
                              use ``nomic-embed-text``).
        MEM0_EMBED_PROVIDER   ``openai`` (default) | ``ollama`` |
                              ``huggingface``.
        MEM0_LLM_PROVIDER     ``openai`` (default) | ``ollama`` |
                              ``litellm``.
        MEM0_VECTOR_STORE     ``chroma`` (default) | ``qdrant`` | ``pgvector``.
        MEM0_DATA_DIR         Persistent data dir (default ``/data/mem0``).
    """
    api_key = os.getenv("OPENAI_API_KEY")
    api_base = os.getenv("OPENAI_API_BASE")
    llm_provider = os.getenv("MEM0_LLM_PROVIDER", "openai")
    embed_provider = os.getenv("MEM0_EMBED_PROVIDER", "openai")
    llm_model = os.getenv("MEM0_LLM_MODEL", "gpt-4o-mini")
    embed_model = os.getenv("MEM0_EMBED_MODEL", "text-embedding-3-small")
    vector_store = os.getenv("MEM0_VECTOR_STORE", "chroma")
    data_dir = os.getenv("MEM0_DATA_DIR", DEFAULT_MEM0_DATA_DIR)
    Path(data_dir).mkdir(parents=True, exist_ok=True)

    llm_cfg: dict[str, Any] = {"model": llm_model}
    if api_key:
        llm_cfg["api_key"] = api_key
    if api_base:
        # mem0 reads `openai_base_url` for OpenAI provider and `ollama_base_url`
        # for Ollama. We forward the value for whichever the user picked.
        if llm_provider == "openai":
            llm_cfg["openai_base_url"] = api_base
        elif llm_provider == "ollama":
            llm_cfg["ollama_base_url"] = api_base

    embed_cfg: dict[str, Any] = {"model": embed_model}
    if api_key and embed_provider == "openai":
        embed_cfg["api_key"] = api_key
    if api_base and embed_provider == "openai":
        embed_cfg["openai_base_url"] = api_base
    if api_base and embed_provider == "ollama":
        embed_cfg["ollama_base_url"] = api_base

    vector_cfg: dict[str, Any]
    if vector_store == "chroma":
        vector_cfg = {"path": str(Path(data_dir) / "chroma"), "collection_name": "sharp_fhir_mcp"}
    elif vector_store == "qdrant":
        vector_cfg = {"path": str(Path(data_dir) / "qdrant"), "collection_name": "sharp_fhir_mcp"}
    else:
        # pgvector / others — let mem0 default; expect user to set their own
        # connection params via mem0-specific env vars.
        vector_cfg = {}

    return {
        "llm": {"provider": llm_provider, "config": llm_cfg},
        "embedder": {"provider": embed_provider, "config": embed_cfg},
        "vector_store": {"provider": vector_store, "config": vector_cfg},
        "history_db_path": str(Path(data_dir) / "history.db"),
    }


class Mem0Client:
    """Async wrapper around the sync ``mem0.Memory`` SDK."""

    def __init__(self, memory: Any) -> None:
        self._mem = memory

    # --
    # Construction
    # --

    @classmethod
    def from_env(cls) -> "Mem0Client | None":
        """Return a configured client, or ``None`` if mem0 isn't installed
        or memory has been explicitly disabled."""
        if _truthy("MEM0_DISABLED"):
            return None
        try:
            from mem0 import Memory  # type: ignore[import-not-found]
        except ImportError:
            logger.info(
                "mem0ai not installed — install with `pip install -e .[memory]` "
                "to enable the memory_* tools."
            )
            return None

        try:
            memory = Memory.from_config(_build_config())
        except Exception as e:  # noqa: BLE001 — mem0 may raise many kinds
            logger.warning("Failed to initialise mem0: %s", e)
            return None

        return cls(memory)

    @property
    def is_configured(self) -> bool:
        return self._mem is not None

    # --
    # Helpers
    # --

    @staticmethod
    def _patient_user_id(patient_id: str) -> str:
        """mem0 user_id namespaced to a FHIR Patient resource."""
        return f"patient:{patient_id}"

    async def _to_thread(self, fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except Exception as e:  # noqa: BLE001 — surface upstream
            raise Mem0Error(str(e)) from e

    # --
    # Public API
    # --

    async def add_text(
        self,
        text: str,
        *,
        patient_id: str,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add a single text memory tagged to a patient.

        ``text`` is passed as a one-message conversation; mem0 will run its
        memory-extraction pipeline (LLM-summarised facts) before storing.
        """
        return await self._to_thread(
            self._mem.add,
            messages=[{"role": "user", "content": text}],
            user_id=self._patient_user_id(patient_id),
            agent_id=AGENT_ID,
            run_id=run_id,
            metadata=metadata or {},
        )

    async def search(
        self,
        query: str,
        *,
        patient_id: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Semantic search scoped to one patient."""
        merged: dict[str, Any] = {"user_id": self._patient_user_id(patient_id)}
        if filters:
            merged.update(filters)
        return await self._to_thread(
            self._mem.search, query=query, filters=merged, limit=limit
        )

    async def get_all(
        self,
        *,
        patient_id: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        return await self._to_thread(
            self._mem.get_all,
            user_id=self._patient_user_id(patient_id),
            limit=limit,
        )

    async def get(self, memory_id: str) -> dict[str, Any]:
        return await self._to_thread(self._mem.get, memory_id=memory_id)

    async def delete(self, memory_id: str) -> dict[str, Any]:
        return await self._to_thread(self._mem.delete, memory_id=memory_id)

    async def delete_all(self, *, patient_id: str) -> dict[str, Any]:
        return await self._to_thread(
            self._mem.delete_all, user_id=self._patient_user_id(patient_id)
        )

    async def reset(self) -> None:
        await self._to_thread(self._mem.reset)


__all__ = ["Mem0Client", "Mem0Error"]
