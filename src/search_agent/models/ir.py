"""Candidate-search IR (intermediate representation) models.

The parser returns business intent instead of backend query DSL. Field
policies and the query compiler own all backend-specific decisions.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Vector fields that semantic (soft) conditions may target.
SOFT_VECTOR_FIELDS = frozenset(
    {
        "work_vector",
        "project_vector",
        "education_vector",
        "self_evaluation_vector",
        "skills_vector",
    }
)

TenureIntent = Literal["generic", "title", "recent"]

StructuredField = Literal[
    "name",
    "skill",
    "company",
    "internship_company",
    "position",
    "internship_job",
    "intention_position",
    "school",
    "major",
    "degree",
    "city",
    "intention_city",
    "live_city",
    "work_years",
    "work_segment_count",
    "employment_gap",
    "latest_company",
    "latest_work_job",
    "recent_tenure_1",
    "max_tenure",
    "avg_tenure",
    "short_job_count",
    "job_stability",
    "inter_segment_gap",
    "max_inter_segment_gap",
    "career_discontinuity",
    "role_tenure",
    "graduation_year",
    "age",
    "birth_year",
    "gender",
    "work_state",
    "expected_salary",
    "talent_tag",
    "certificate",
    "language",
]

ConditionOperator = Literal["eq", "any", "all", "not", "gte", "lte", "range"]


PeerHint = Literal["industry_peers", "famous_background"]
ExpandScope = Literal["peers"]


class IRMeta(BaseModel):
    """Parser metadata that mirrors the recall meta contract."""

    model_config = ConfigDict(extra="forbid")

    needClarification: bool = False
    clarifyQuestions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    tenureIntent: TenureIntent | None = None
    expandScope: ExpandScope | None = None
    peerHint: PeerHint | None = None


class StructuredConditionIR(BaseModel):
    """A structured business condition identified by the parser.

    Examples:
        ``field=skill, operator=any, values=["Python", "Java"]``
        ``field=work_years, operator=gte, values=[5]``
    """

    model_config = ConfigDict(extra="forbid")

    field: StructuredField
    operator: ConditionOperator = "eq"
    values: list[str | int | float | bool] = Field(default_factory=list)
    required: bool = True
    weight: float = Field(default=1.0, ge=0.1, le=5.0)
    softText: str | None = None

    @field_validator("values")
    @classmethod
    def _strip_string_values(
        cls, values: list[str | int | float | bool]
    ) -> list[str | int | float | bool]:
        cleaned: list[str | int | float | bool] = []
        for value in values:
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    cleaned.append(stripped)
            else:
                cleaned.append(value)
        return cleaned

    @model_validator(mode="after")
    def _validate_values(self) -> "StructuredConditionIR":
        if self.operator == "range":
            if len(self.values) != 2:
                raise ValueError("range operator requires exactly two values")
            return self
        if not self.values:
            raise ValueError("structured condition requires at least one value")
        return self


class SemanticConditionIR(BaseModel):
    """A semantic condition that should be routed to one or more vector fields."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    fields: list[str] = Field(min_length=1)
    weight: float = Field(default=1.0, ge=0.1, le=5.0)

    @field_validator("fields")
    @classmethod
    def _validate_vector_fields(cls, fields: list[str]) -> list[str]:
        invalid = [field for field in fields if field not in SOFT_VECTOR_FIELDS]
        if invalid:
            raise ValueError(f"invalid soft vector fields: {invalid}")
        return fields


BoolNodeType = Literal["and", "or", "not", "leaf"]


class WhereNode(BaseModel):
    """Boolean AST node for structured constraints.

    - and: children[]
    - or: children[] + optional minimumShouldMatch
    - not: child
    - leaf: condition
    """

    model_config = ConfigDict(extra="forbid")

    type: BoolNodeType
    children: list["WhereNode"] = Field(default_factory=list)
    child: "WhereNode | None" = None
    condition: StructuredConditionIR | None = None
    minimumShouldMatch: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate_shape(self) -> "WhereNode":
        if self.type in {"and", "or"}:
            if not self.children:
                raise ValueError(f"{self.type} node requires non-empty children")
            if self.child is not None or self.condition is not None:
                raise ValueError(f"{self.type} node cannot include child/condition")
            if self.type == "and" and self.minimumShouldMatch is not None:
                raise ValueError("and node cannot set minimumShouldMatch")
            if self.type == "or" and self.minimumShouldMatch is None:
                self.minimumShouldMatch = 1
            return self

        if self.type == "not":
            if self.child is None:
                raise ValueError("not node requires child")
            if self.children or self.condition is not None or self.minimumShouldMatch is not None:
                raise ValueError("not node can only include child")
            return self

        # leaf
        if self.condition is None:
            raise ValueError("leaf node requires condition")
        if self.children or self.child is not None or self.minimumShouldMatch is not None:
            raise ValueError("leaf node can only include condition")
        return self


class CandidateSearchIR(BaseModel):
    """Search intent intermediate representation."""

    model_config = ConfigDict(extra="forbid")

    # boolean expression tree for hard constraints
    where: WhereNode
    semantic: list[SemanticConditionIR] = Field(default_factory=list)
    sort: list[dict[str, Any]] | None = None
    meta: IRMeta = Field(default_factory=IRMeta)


WhereNode.model_rebuild()


__all__ = [
    "CandidateSearchIR",
    "ConditionOperator",
    "ExpandScope",
    "IRMeta",
    "PeerHint",
    "TenureIntent",
    "BoolNodeType",
    "SemanticConditionIR",
    "WhereNode",
    "StructuredConditionIR",
    "StructuredField",
]
