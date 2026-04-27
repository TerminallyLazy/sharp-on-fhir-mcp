"""Vendored ``omni_memory.core.config`` shim.

The OmniSimpleMem repository at ``aiming-lab/SimpleMem/OmniSimpleMem`` is
incomplete on the ``main`` branch as of writing — every other module is
present, but ``omni_memory/core/config.py`` was not committed, which means
``import omni_memory`` fails out of the box. This file restores the public
config API exactly as the upstream tests
(``OmniSimpleMem/tests/test_config.py``) expect, with sane defaults so the
rest of the package can run end-to-end.

REMOVE THIS FILE once upstream ships ``omni_memory/core/config.py``. The
Dockerfile only copies it in if the path is missing.

Schema reverse-engineered from:
    * ``OmniSimpleMem/tests/test_config.py`` (defaults + expected fields)
    * ``OmniSimpleMem/omni_memory/app.py`` (env-driven init)
    * grep of every ``config.<group>.<field>`` access across the package.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EmbeddingConfig:
    model_name: str = "all-MiniLM-L6-v2"
    embedding_dim: int = 384
    batch_size: int = 32
    api_key: str | None = None
    visual_embedding_dim: int = 512  # CLIP default


@dataclass
class RetrievalConfig:
    default_top_k: int = 10
    enable_hybrid_search: bool = True
    enable_graph_traversal: bool = True
    auto_expand_threshold: float = 0.7
    max_expanded_items: int = 5


@dataclass
class StorageConfig:
    base_dir: str = "./omni_memory_data"
    cold_storage_dir: str = "./omni_memory_data/cold_storage"
    index_dir: str = "./omni_memory_data/index"
    use_s3: bool = False


@dataclass
class LLMConfig:
    summary_model: str = "gpt-4o-mini"
    query_model: str = "gpt-4o-mini"
    caption_model: str = "gpt-4o-mini"
    whisper_model: str = "whisper-1"
    temperature: float = 0.0
    max_tokens: int = 1000
    api_key: str | None = None
    api_base_url: str | None = None


@dataclass
class EventConfig:
    auto_create_events: bool = True
    event_time_window_seconds: float = 300.0


@dataclass
class EntropyTriggerConfig:
    visual_similarity_threshold_high: float = 0.9
    visual_similarity_threshold_low: float = 0.5
    enable_visual_trigger: bool = True
    enable_audio_trigger: bool = True
    enable_text_trigger: bool = True
    visual_model_name: str = "ViT-B-32"
    audio_silence_threshold: float = 0.01


@dataclass
class RouterConfig:
    benchmark_safe: bool = False


@dataclass
class OmniMemoryConfig:
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    event: EventConfig = field(default_factory=EventConfig)
    entropy_trigger: EntropyTriggerConfig = field(default_factory=EntropyTriggerConfig)
    router: RouterConfig = field(default_factory=RouterConfig)
    debug_mode: bool = False
    log_level: str = "INFO"
    enable_self_evolution: bool = False
    evolution: Any = None

    def __post_init__(self) -> None:
        # Env fallbacks for OpenAI-compatible LLM (OPENAI_API_KEY /
        # OPENAI_API_BASE works for OpenAI proper, OpenRouter, Ollama in
        # OpenAI-compat mode, vLLM, etc.).
        if not self.llm.api_key:
            self.llm.api_key = os.getenv("OPENAI_API_KEY")
        if not self.llm.api_base_url:
            self.llm.api_base_url = os.getenv("OPENAI_API_BASE")

    # --
    # Convenience
    # --

    @classmethod
    def create_default(cls) -> "OmniMemoryConfig":
        return cls()

    def set_unified_model(self, model: str) -> "OmniMemoryConfig":
        self.llm.summary_model = model
        self.llm.query_model = model
        self.llm.caption_model = model
        return self

    def enable_evolution(self) -> "OmniMemoryConfig":
        self.enable_self_evolution = True
        try:
            from omni_memory.evolution import EvolutionConfig

            self.evolution = EvolutionConfig()
        except Exception:  # noqa: BLE001
            self.evolution = {}
        return self

    def ensure_directories(self) -> None:
        for d in (
            self.storage.base_dir,
            self.storage.cold_storage_dir,
            self.storage.index_dir,
        ):
            Path(d).mkdir(parents=True, exist_ok=True)

    # --
    # Serialisation
    # --

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OmniMemoryConfig":
        sub = {
            "embedding": EmbeddingConfig,
            "retrieval": RetrievalConfig,
            "storage": StorageConfig,
            "llm": LLMConfig,
            "event": EventConfig,
            "entropy_trigger": EntropyTriggerConfig,
            "router": RouterConfig,
        }
        kwargs: dict[str, Any] = {}
        for k, v in data.items():
            if k in sub and isinstance(v, dict):
                kwargs[k] = sub[k](**v)
            else:
                kwargs[k] = v
        return cls(**kwargs)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_json(cls, text: str) -> "OmniMemoryConfig":
        return cls.from_dict(json.loads(text))

    def save_to_file(self, path: str) -> None:
        Path(path).write_text(self.to_json())

    @classmethod
    def from_file(cls, path: str) -> "OmniMemoryConfig":
        return cls.from_json(Path(path).read_text())


__all__ = [
    "EmbeddingConfig",
    "RetrievalConfig",
    "StorageConfig",
    "LLMConfig",
    "EventConfig",
    "EntropyTriggerConfig",
    "RouterConfig",
    "OmniMemoryConfig",
]
