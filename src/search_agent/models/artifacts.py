"""Agent artifacts: job profile, talent profile, search path, judgments, etc.

These are pure pydantic models; no backend internals.
``SearchPath`` holds a ``CandidateSearchIR`` intent (not a WhereNode/ES query).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from search_agent.contract.tool import SearchToolResult
from search_agent.models.ir import (
    CandidateSearchIR,
    SemanticConditionIR,
    StructuredConditionIR,
)

JobAbilityLevel = Literal["must", "nice"]
PathYield = Literal["high", "mid", "low", "exploratory"]
PathStatus = Literal["pending", "running", "done", "exhausted", "dropped"]
Quality = Literal["rich", "thin", "empty"]


class JobAbility(BaseModel):
    name: str
    level: JobAbilityLevel = "must"
    evidence_hints: list[str] = Field(default_factory=list)


class JobProfile(BaseModel):
    raw_query: str
    title: str | None = None
    must_abilities: list[JobAbility] = Field(default_factory=list)
    nice_abilities: list[JobAbility] = Field(default_factory=list)
    hard_constraints: list[StructuredConditionIR] = Field(default_factory=list)
    subjective_difficulty: Literal["low", "mid", "high"] = "mid"
    difficulty_reason: str = ""
    needs_clarification: bool = False
    clarify_questions: list[str] = Field(default_factory=list)


class CandidateSource(BaseModel):
    id: str
    kind: Literal[
        "company", "industry", "role", "product", "project", "skill_combo", "transferable"
    ]
    value: str
    rationale: str = ""
    transfer_from: list[str] = Field(default_factory=list)


class TalentProfile(BaseModel):
    source_hypotheses: list[CandidateSource] = Field(default_factory=list)
    must_keywords: list[str] = Field(default_factory=list)
    soft_signals: list[SemanticConditionIR] = Field(default_factory=list)
    risk_filters: list[str] = Field(default_factory=list)


class SearchPath(BaseModel):
    id: str
    rationale: str = ""
    intent: CandidateSearchIR
    source_refs: list[str] = Field(default_factory=list)
    expected_yield: PathYield = "mid"
    signature: str = ""
    status: PathStatus = "pending"


class CandidateJudgment(BaseModel):
    doc_id: str
    score: float = 0.0
    matched_abilities: list[str] = Field(default_factory=list)
    missing_abilities: list[str] = Field(default_factory=list)
    hard_matches: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    source_path_ids: list[str] = Field(default_factory=list)


class PathResult(BaseModel):
    path_id: str
    tool_result: SearchToolResult
    judgments: list[CandidateJudgment] = Field(default_factory=list)
    quality: Quality = "empty"
    quality_reason: str = ""


class PathAction(BaseModel):
    kind: Literal["add_path", "loosen", "tighten", "drop", "clarify"]
    target_path_id: str | None = None
    rationale: str = ""
    intent: CandidateSearchIR | None = None


class Reflection(BaseModel):
    common_traits: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    inferred_soft_signals: list[SemanticConditionIR] = Field(default_factory=list)
    new_source_hints: list[CandidateSource] = Field(default_factory=list)
    path_actions: list[PathAction] = Field(default_factory=list)


class Budget(BaseModel):
    max_iterations: int = 3
    max_llm_calls: int = 40
    max_paths_per_iteration: int = 4
    time_budget_sec: float = 12.0
    target_qualified: int = 20


__all__ = [
    "Budget",
    "CandidateJudgment",
    "CandidateSource",
    "JobAbility",
    "JobAbilityLevel",
    "JobProfile",
    "PathAction",
    "PathResult",
    "PathStatus",
    "PathYield",
    "Quality",
    "Reflection",
    "SearchPath",
    "TalentProfile",
]
