"""Embedding providers for the hybrid recall channel."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from search_agent.hybrid_v2.config import HybridV2Config
from search_agent.tools.embedding import embed_text


@runtime_checkable
class Embedder(Protocol):
    dims: int

    def embed_many(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """Deterministic, offline-safe embedder used by demos and tests."""

    def __init__(self, dims: int = 256) -> None:
        self.dims = int(dims)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [embed_text(text, dims=self.dims) for text in texts]


class OpenAICompatibleEmbedder:
    """Any OpenAI-compatible ``/embeddings`` endpoint (OpenAI, DashScope, vLLM...)."""

    def __init__(
        self,
        *,
        model: str,
        dims: int,
        api_key: str,
        base_url: str = "",
    ) -> None:
        if not api_key:
            raise RuntimeError(
                "SEARCH_AGENT_EMBEDDING_API_KEY (or OPENAI_API_KEY) is required "
                "when SEARCH_AGENT_EMBEDDING_PROVIDER=openai"
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                'OpenAI-compatible embeddings need: pip install -e ".[openai]"'
            ) from exc
        self.dims = int(dims)
        self._model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url or None)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embeddings.create(model=self._model, input=texts)
        return [list(row.embedding) for row in response.data]


def build_embedder(config: HybridV2Config) -> Embedder:
    if config.embedding_provider == "hash":
        return HashEmbedder(config.embedding_dims)
    if config.embedding_provider in {"openai", "openai-compatible"}:
        return OpenAICompatibleEmbedder(
            model=config.embedding_model,
            dims=config.embedding_dims,
            api_key=config.embedding_api_key,
            base_url=config.embedding_base_url,
        )
    raise ValueError(
        f"unknown embedding provider {config.embedding_provider!r}; expected 'hash' or 'openai'"
    )


__all__ = ["Embedder", "HashEmbedder", "OpenAICompatibleEmbedder", "build_embedder"]
