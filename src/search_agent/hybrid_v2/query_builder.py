"""Compile a Hybrid V2 query plan into Elasticsearch 8 DSL.

The builder owns three concerns:

* tenant and visibility filters shared by every recall channel;
* a BM25 ``should`` channel over human-readable profile fields;
* one kNN clause per semantic condition/vector field.

It does not know about graph expansion, LLM aliasing, or host-specific
services. Those concerns were intentionally left out of the public migration.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any

from .config import HybridV2Config
from .embedding import Embedder
from .schemas import SOFT_VECTOR_FIELDS, HybridQueryResult, SoftCondition

_SOFT_FIELD_BM25_MAP: dict[str, tuple[str, ...]] = {
    "work_vector": (
        "work_experiences.job^2.0",
        "work_experiences.description^1.5",
        "work_experiences.company^2.0",
    ),
    "project_vector": (
        "project_experiences.project_name^2.0",
        "project_experiences.description^1.3",
    ),
    "education_vector": (
        "educations.major.name^1.6",
        "educations.college.name^1.8",
        "educations.degree.name^1.2",
    ),
    "self_evaluation_vector": (
        "overview.self_evaluation^1.5",
        "overview.name^0.2",
    ),
    "skills_vector": ("skills.name^2.2", "work_experiences.job^1.2"),
}

_NESTED_BM25_PATHS: frozenset[str] = frozenset(
    {
        "skills",
        "work_experiences",
        "project_experiences",
        "educations",
        "certificates",
        "languages",
        "talent_tags",
    }
)

_VECTOR_FIELD_BY_MODE: dict[str, dict[str, str]] = {
    "nested": {
        "work_vector": "work_segments.vector",
        "project_vector": "project_segments.vector",
        "education_vector": "education_segments.vector",
        "self_evaluation_vector": "self_evaluation_vector",
        "skills_vector": "skills_vector",
    },
    "flat": {
        "work_vector": "work_vector",
        "project_vector": "project_vector",
        "education_vector": "education_vector",
        "self_evaluation_vector": "self_evaluation_vector",
        "skills_vector": "skills_vector",
    },
}


@dataclass(frozen=True)
class HybridQueryPlan:
    body: dict[str, Any]
    score_max: float
    soft_clauses: tuple[dict[str, Any], ...]
    degraded_reasons: tuple[str, ...] = ()


def build_shared_filter(
    company_id: int,
    permission: dict[str, Any] | None = None,
    *,
    config: HybridV2Config | None = None,
) -> list[dict[str, Any]]:
    """Build tenant/profile/visibility filters from configurable field names."""

    cfg = config or HybridV2Config.from_env()
    clauses: list[dict[str, Any]] = []
    if cfg.tenant_field:
        clauses.append({"term": {cfg.tenant_field: company_id}})
    if cfg.entity_join_field and cfg.entity_join_value:
        clauses.append({"term": {cfg.entity_join_field: cfg.entity_join_value}})

    if cfg.soft_delete_field:
        field = cfg.soft_delete_field
        clauses.append(
            {
                "bool": {
                    "should": [
                        {"term": {field: False}},
                        {"bool": {"must_not": [{"exists": {"field": field}}]}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )

    if permission and not permission.get("hasFullAccess"):
        allowed_pool_ids = permission.get("allowedPoolIds")
        allowed_collaborator_ids = permission.get("allowedCollaboratorIds")
        if allowed_pool_ids is not None:
            clauses.append(
                {"terms": {"profile_pool_id": allowed_pool_ids}}
                if allowed_pool_ids
                else {"term": {"profile_pool_id": -1}}
            )
        if allowed_collaborator_ids is not None:
            clauses.append(
                {"terms": {"collaborator_id": allowed_collaborator_ids}}
                if allowed_collaborator_ids
                else {"term": {"collaborator_id": -1}}
            )
    return clauses


def _normalize_boosts(weights: list[float]) -> list[float]:
    if not weights:
        return []
    positive = [max(0.0, float(weight)) for weight in weights]
    total = sum(positive)
    if not math.isfinite(total) or total <= 0:
        equal = 1.0 / len(positive)
        return [equal] * len(positive)
    return [weight / total for weight in positive]


def _vector_field(logical_field: str, config: HybridV2Config) -> str:
    mode = config.vector_mode
    mapping = _VECTOR_FIELD_BY_MODE.get(mode)
    if mapping is None:
        raise ValueError(f"unknown vector mode {mode!r}; expected 'nested' or 'flat'")
    return mapping[logical_field]


def _nested_path_for_bm25_field(field_spec: str) -> str | None:
    base = str(field_spec or "").split("^", 1)[0].strip()
    root = base.split(".", 1)[0] if base else ""
    return root if root in _NESTED_BM25_PATHS else None


def _bm25_should_clauses(
    text: str,
    field_specs: list[str],
    *,
    boost: float,
) -> list[dict[str, Any]]:
    nested_by_path: dict[str, list[str]] = {}
    flat_fields: list[str] = []
    for spec in dict.fromkeys(field_specs):
        path = _nested_path_for_bm25_field(spec)
        if path:
            nested_by_path.setdefault(path, []).append(spec)
        else:
            flat_fields.append(spec)

    terms = [term.strip() for term in str(text or "").split() if term.strip()]
    if not terms:
        terms = [str(text or "").strip()] if str(text or "").strip() else []

    clauses: list[dict[str, Any]] = []
    for path, path_fields in nested_by_path.items():
        phrase_clauses: list[dict[str, Any]] = []
        for field_spec in path_fields:
            field_name = field_spec.split("^", 1)[0]
            field_boost = boost
            if "^" in field_spec:
                try:
                    field_boost *= float(field_spec.rsplit("^", 1)[1])
                except ValueError:
                    pass
            for term in terms:
                phrase_clauses.append(
                    {"match_phrase": {field_name: {"query": term, "boost": field_boost}}}
                )
        if phrase_clauses:
            clauses.append(
                {
                    "nested": {
                        "path": path,
                        "query": {
                            "bool": {
                                "should": phrase_clauses,
                                "minimum_should_match": 1,
                            }
                        },
                        "score_mode": "max",
                    }
                }
            )

    for field_spec in flat_fields:
        field_name = field_spec.split("^", 1)[0]
        field_boost = boost
        if "^" in field_spec:
            try:
                field_boost *= float(field_spec.rsplit("^", 1)[1])
            except ValueError:
                pass
        for term in terms:
            clauses.append({"match_phrase": {field_name: {"query": term, "boost": field_boost}}})
    return clauses


def _inject_bm25_should(
    body: dict[str, Any],
    soft: list[SoftCondition],
    *,
    enabled: bool,
) -> None:
    if not enabled or not soft:
        return
    bool_query = body.get("query", {}).get("bool")
    if not isinstance(bool_query, dict):
        return
    should: list[dict[str, Any]] = []
    for cond in soft[:6]:
        fields: list[str] = []
        for logical_field in cond.fields:
            fields.extend(_SOFT_FIELD_BM25_MAP.get(logical_field, ()))
        if fields:
            should.extend(
                _bm25_should_clauses(
                    cond.text,
                    fields,
                    boost=max(0.1, min(5.0, float(cond.weight))),
                )
            )
    if should:
        bool_query["should"] = should
        bool_query["minimum_should_match"] = 0


def _build_knn_clauses(
    soft: list[SoftCondition],
    *,
    top_k: int,
    shared_filter: list[dict[str, Any]],
    hard_filter: list[dict[str, Any]],
    hard_must_not: list[dict[str, Any]],
    embedder: Embedder,
    config: HybridV2Config,
) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]], list[str]]:
    expanded: list[tuple[str, str, float]] = []
    for cond in soft:
        for field in cond.fields:
            if field in SOFT_VECTOR_FIELDS:
                expanded.append((cond.text, field, float(cond.weight)))
    if not expanded:
        return [], 0.0, [], []

    unique_texts = list(dict.fromkeys(text for text, _, _ in expanded))
    embeddings: dict[str, list[float] | None] = {}
    degraded: list[str] = []
    batch_size = max(1, int(config.embedding_batch_size))
    for offset in range(0, len(unique_texts), batch_size):
        batch = unique_texts[offset : offset + batch_size]
        try:
            vectors = embedder.embed_many(batch)
        except Exception:
            degraded.append("embedding_batch_failed")
            vectors = [None] * len(batch)
        for text, vector in zip(batch, vectors, strict=False):
            if not isinstance(vector, list) or len(vector) != int(embedder.dims):
                embeddings[text] = None
            else:
                embeddings[text] = vector

    surviving: list[tuple[str, str, float, list[float]]] = []
    for text, field, weight in expanded:
        vector = embeddings.get(text)
        if vector is None:
            continue
        if not any(abs(value) > 1e-12 for value in vector):
            degraded.append("embedding_zero_vector")
            continue
        surviving.append((text, field, weight, vector))
    if not surviving:
        if "embedding_batch_failed" not in degraded:
            degraded.append("embedding_unavailable")
        return [], 0.0, [], degraded

    knn_filter = list(shared_filter) + list(hard_filter)
    if hard_must_not:
        knn_filter = [{"bool": {"filter": knn_filter, "must_not": list(hard_must_not)}}]

    boosts = _normalize_boosts([weight for _, _, weight, _ in surviving])
    knn_k = max(int(config.knn_k), int(top_k))
    num_candidates = max(int(config.knn_num_candidates), knn_k)
    clauses: list[dict[str, Any]] = []
    meta: list[dict[str, Any]] = []
    for (text, logical_field, weight, vector), boost in zip(surviving, boosts, strict=True):
        clauses.append(
            {
                "field": _vector_field(logical_field, config),
                "query_vector": list(vector),
                "k": knn_k,
                "num_candidates": num_candidates,
                "boost": float(boost),
                "filter": copy.deepcopy(knn_filter),
            }
        )
        meta.append(
            {
                "text": text,
                "field": logical_field,
                "weight": float(weight),
                "boost": float(boost),
            }
        )
    return clauses, float(sum(boosts)), meta, degraded


def _apply_exclusions(body: dict[str, Any], exclude_doc_ids: list[str] | None) -> None:
    excluded = [str(doc_id) for doc_id in (exclude_doc_ids or []) if str(doc_id).strip()]
    if not excluded:
        return
    bool_query = body.setdefault("query", {}).setdefault("bool", {})
    must_not = bool_query.get("must_not")
    if isinstance(must_not, list):
        must_not.append({"ids": {"values": excluded}})
    elif must_not:
        bool_query["must_not"] = [must_not, {"ids": {"values": excluded}}]
    else:
        bool_query["must_not"] = [{"ids": {"values": excluded}}]

    for clause in body.get("knn", []) or []:
        if not isinstance(clause, dict):
            continue
        filters = clause.get("filter")
        if isinstance(filters, list):
            filters.append({"bool": {"must_not": [{"ids": {"values": excluded}}]}})


def build_hybrid_query_plan(
    parsed: HybridQueryResult,
    *,
    company_id: int,
    permission: dict[str, Any] | None,
    top_k: int,
    embedder: Embedder,
    config: HybridV2Config | None = None,
    exclude_doc_ids: list[str] | None = None,
) -> HybridQueryPlan:
    """Build the complete ES request and keep trace metadata outside the body."""

    cfg = config or HybridV2Config.from_env()
    shared_filter = build_shared_filter(company_id, permission, config=cfg)
    hard_filter = list(parsed.hard.filter or [])
    hard_must_not = list(parsed.hard.must_not or [])
    soft = list(parsed.soft or [])

    bool_clause: dict[str, Any] = {"filter": shared_filter + hard_filter}
    if hard_must_not:
        bool_clause["must_not"] = list(hard_must_not)
    body: dict[str, Any] = {
        "size": max(1, int(top_k)),
        "query": {"bool": bool_clause},
        "explain": False,
        "track_total_hits": True,
        "_source": {
            "excludes": [
                "overall_vector",
                "work_vector",
                "project_vector",
                "education_vector",
                "self_evaluation_vector",
                "skills_vector",
                "work_segments",
                "project_segments",
                "education_segments",
            ]
        },
    }

    knn, score_max, soft_meta, degraded = _build_knn_clauses(
        soft,
        top_k=int(top_k),
        shared_filter=shared_filter,
        hard_filter=hard_filter,
        hard_must_not=hard_must_not,
        embedder=embedder,
        config=cfg,
    )
    _inject_bm25_should(body, soft, enabled=cfg.bm25_enabled)
    if knn:
        body["knn"] = knn
    else:
        body["sort"] = [
            {"candidate_authority_time": {"order": "desc", "unmapped_type": "date"}},
            {"candidate_profile_id": {"order": "asc", "unmapped_type": "keyword"}},
        ]
    _apply_exclusions(body, exclude_doc_ids)
    return HybridQueryPlan(
        body=body,
        score_max=score_max,
        soft_clauses=tuple(soft_meta),
        degraded_reasons=tuple(dict.fromkeys(degraded)),
    )


def build_hybrid_es_body(
    parsed: HybridQueryResult,
    *,
    company_id: int,
    permission: dict[str, Any] | None,
    top_k: int,
    embedder: Embedder,
    config: HybridV2Config | None = None,
) -> tuple[dict[str, Any], float, list[dict[str, Any]]]:
    """Compatibility wrapper returning ``(body, score_max, soft_meta)``."""

    plan = build_hybrid_query_plan(
        parsed,
        company_id=company_id,
        permission=permission,
        top_k=top_k,
        embedder=embedder,
        config=config,
    )
    return plan.body, plan.score_max, list(plan.soft_clauses)


def sanitize_body_for_trace(body: dict[str, Any]) -> dict[str, Any]:
    """Remove query vectors before exposing a request body in trace output."""

    sanitized = copy.deepcopy(body)
    for clause in sanitized.get("knn", []) or []:
        if isinstance(clause, dict) and isinstance(clause.get("query_vector"), list):
            clause["query_vector"] = f"<vector len={len(clause['query_vector'])}>"
    return sanitized


__all__ = [
    "HybridQueryPlan",
    "build_hybrid_es_body",
    "build_hybrid_query_plan",
    "build_shared_filter",
    "sanitize_body_for_trace",
]
