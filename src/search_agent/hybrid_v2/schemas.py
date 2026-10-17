"""Public model bridge for the standalone Hybrid V2 engine.

The agent already owns the IR contract.  The query compiler additionally needs
a small backend-neutral representation of the compiled hard/soft plan.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from search_agent.models.ir import (
    CandidateSearchIR,
    ConditionOperator,
    ExpandScope,
    IRMeta,
    PeerHint,
    SemanticConditionIR,
    StructuredConditionIR,
    StructuredField,
    TenureIntent,
    WhereNode,
)

SOFT_VECTOR_FIELDS = frozenset(
    {
        "work_vector",
        "project_vector",
        "education_vector",
        "self_evaluation_vector",
        "skills_vector",
    }
)

SOFT_VECTOR_NESTED_FIELDS: dict[str, str] = {
    "work_vector": "work_segments.vector",
    "project_vector": "project_segments.vector",
    "education_vector": "education_segments.vector",
}


class ESQueryMeta(BaseModel):
    needClarification: bool = False
    clarifyQuestions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SoftCondition(BaseModel):
    text: str = Field(min_length=1)
    fields: list[str] = Field(min_length=1)
    weight: float = Field(default=1.0, ge=0.1, le=5.0)


class HardBlock(BaseModel):
    filter: list[dict[str, Any]] = Field(default_factory=list)
    must_not: list[dict[str, Any]] = Field(default_factory=list)


class HybridQueryResult(BaseModel):
    hard: HardBlock = Field(default_factory=HardBlock)
    soft: list[SoftCondition] = Field(default_factory=list)
    sort: list[dict[str, Any]] | None = None
    meta: ESQueryMeta = Field(default_factory=ESQueryMeta)


__all__ = [
    "CandidateSearchIR",
    "ConditionOperator",
    "ESQueryMeta",
    "ExpandScope",
    "HardBlock",
    "HybridQueryResult",
    "IRMeta",
    "PeerHint",
    "SOFT_VECTOR_FIELDS",
    "SOFT_VECTOR_NESTED_FIELDS",
    "SemanticConditionIR",
    "SoftCondition",
    "StructuredConditionIR",
    "StructuredField",
    "TenureIntent",
    "WhereNode",
]
