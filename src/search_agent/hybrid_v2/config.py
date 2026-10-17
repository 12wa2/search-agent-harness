"""Environment-driven configuration for the standalone Hybrid V2 engine.

No credentials or host-specific defaults live in the package.  The engine can
run against any Elasticsearch 8 index that follows the documented candidate
mapping, and can use either the deterministic local embedding or any
OpenAI-compatible embedding endpoint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)) or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)) or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class HybridV2Config:
    """Runtime knobs with safe, self-contained defaults."""

    es_url: str
    es_index: str
    es_username: str
    es_password: str
    es_api_key: str
    es_verify_certs: bool
    es_request_timeout: float
    embedding_provider: str
    embedding_model: str
    embedding_dims: int
    embedding_batch_size: int
    embedding_api_key: str
    embedding_base_url: str
    vector_mode: str
    tenant_field: str
    entity_join_field: str
    entity_join_value: str
    soft_delete_field: str
    bm25_enabled: bool
    knn_k: int
    knn_num_candidates: int
    cheap_pool_size: int
    rerank_scan_cap: int
    rerank_target_n: int
    rerank_shard_size: int
    rerank_min_confidence: float
    rerank_timeout_sec: float
    rerank_workers: int
    rerank_provider: str
    rerank_model: str
    rerank_api_key: str
    rerank_base_url: str
    match_rate_baseline: int
    tag_dict_path: str
    salary_bands_path: str
    onet_alias_path: str

    @classmethod
    def from_env(cls) -> "HybridV2Config":
        es_url = _env("SEARCH_AGENT_HYBRID_V2_ES_URL") or _env(
            "SEARCH_AGENT_ES_URL", "http://127.0.0.1:9200"
        )
        es_index = _env("SEARCH_AGENT_HYBRID_V2_ES_INDEX") or _env(
            "SEARCH_AGENT_ES_INDEX", "candidate"
        )
        return cls(
            es_url=es_url,
            es_index=es_index,
            es_username=_env("SEARCH_AGENT_ES_USERNAME"),
            es_password=_env("SEARCH_AGENT_ES_PASSWORD"),
            es_api_key=_env("SEARCH_AGENT_ES_API_KEY"),
            es_verify_certs=_env_bool("SEARCH_AGENT_ES_VERIFY_CERTS", True),
            es_request_timeout=_env_float("SEARCH_AGENT_ES_REQUEST_TIMEOUT", 30.0),
            embedding_provider=_env("SEARCH_AGENT_EMBEDDING_PROVIDER", "hash").lower(),
            embedding_model=_env("SEARCH_AGENT_EMBEDDING_MODEL", "text-embedding-3-small"),
            embedding_dims=_env_int("SEARCH_AGENT_EMBEDDING_DIMS", 256),
            embedding_batch_size=max(1, _env_int("SEARCH_AGENT_EMBEDDING_BATCH_SIZE", 16)),
            embedding_api_key=_env("SEARCH_AGENT_EMBEDDING_API_KEY")
            or _env("OPENAI_API_KEY")
            or _env("SEARCH_AGENT_API_KEY"),
            embedding_base_url=_env("SEARCH_AGENT_EMBEDDING_BASE_URL")
            or _env("OPENAI_BASE_URL")
            or _env("SEARCH_AGENT_BASE_URL"),
            vector_mode=_env("SEARCH_AGENT_HYBRID_V2_VECTOR_MODE", "nested").lower(),
            tenant_field=_env("SEARCH_AGENT_HYBRID_V2_TENANT_FIELD", "company_id"),
            entity_join_field=_env("SEARCH_AGENT_HYBRID_V2_ENTITY_JOIN_FIELD", "entity_join"),
            entity_join_value=_env("SEARCH_AGENT_HYBRID_V2_ENTITY_JOIN_VALUE", "profile"),
            soft_delete_field=_env("SEARCH_AGENT_HYBRID_V2_SOFT_DELETE_FIELD", "deleted"),
            bm25_enabled=_env_bool("SEARCH_AGENT_HYBRID_V2_BM25_ENABLED", True),
            knn_k=max(1, _env_int("SEARCH_AGENT_HYBRID_V2_KNN_K", 100)),
            knn_num_candidates=max(1, _env_int("SEARCH_AGENT_HYBRID_V2_KNN_NUM_CANDIDATES", 500)),
            cheap_pool_size=max(1, _env_int("SEARCH_AGENT_HYBRID_V2_CHEAP_POOL_SIZE", 100)),
            rerank_scan_cap=max(1, _env_int("SEARCH_AGENT_HYBRID_V2_RERANK_SCAN_CAP", 100)),
            rerank_target_n=max(1, _env_int("SEARCH_AGENT_HYBRID_V2_RERANK_TARGET_N", 20)),
            rerank_shard_size=max(1, _env_int("SEARCH_AGENT_HYBRID_V2_RERANK_SHARD_SIZE", 20)),
            rerank_min_confidence=min(
                1.0,
                max(
                    0.0,
                    _env_float("SEARCH_AGENT_HYBRID_V2_RERANK_MIN_CONFIDENCE", 0.5),
                ),
            ),
            rerank_timeout_sec=max(
                1.0, _env_float("SEARCH_AGENT_HYBRID_V2_RERANK_TIMEOUT_SEC", 30.0)
            ),
            rerank_workers=max(1, _env_int("SEARCH_AGENT_HYBRID_V2_RERANK_WORKERS", 4)),
            rerank_provider=_env("SEARCH_AGENT_HYBRID_V2_RERANK_PROVIDER", "local").lower(),
            rerank_model=_env("SEARCH_AGENT_HYBRID_V2_RERANK_MODEL", "gpt-4o-mini"),
            rerank_api_key=_env("SEARCH_AGENT_RERANK_API_KEY")
            or _env("OPENAI_API_KEY")
            or _env("SEARCH_AGENT_API_KEY"),
            rerank_base_url=_env("SEARCH_AGENT_RERANK_BASE_URL")
            or _env("OPENAI_BASE_URL")
            or _env("SEARCH_AGENT_BASE_URL"),
            match_rate_baseline=min(
                100,
                max(0, _env_int("SEARCH_AGENT_HYBRID_V2_MATCH_RATE_BASELINE", 40)),
            ),
            tag_dict_path=_env("SEARCH_AGENT_HYBRID_V2_TAG_DICT"),
            salary_bands_path=_env("SEARCH_AGENT_HYBRID_V2_SALARY_BANDS"),
            onet_alias_path=_env("SEARCH_AGENT_HYBRID_V2_ONET_ALIASES"),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return non-secret configuration for health checks and traces."""
        return {
            "esUrl": self.es_url,
            "esIndex": self.es_index,
            "embeddingProvider": self.embedding_provider,
            "embeddingModel": self.embedding_model,
            "embeddingDims": self.embedding_dims,
            "vectorMode": self.vector_mode,
            "rerankProvider": self.rerank_provider,
            "knnK": self.knn_k,
            "knnNumCandidates": self.knn_num_candidates,
            "rerankTargetN": self.rerank_target_n,
        }


def hybrid_v2_configured() -> bool:
    """Whether an explicit ES endpoint was configured for this process."""
    return bool(_env("SEARCH_AGENT_HYBRID_V2_ES_URL") or _env("SEARCH_AGENT_ES_URL"))


__all__ = ["HybridV2Config", "hybrid_v2_configured"]
