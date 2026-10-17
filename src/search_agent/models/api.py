"""API-facing models: search request/response and candidate item.

Self-contained extraction so the agent package has no dependency on a host
service. camelCase aliases are preserved for JSON round-tripping.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class PermissionContext(BaseModel):
    """Optional permission context (tenant / talent-pool scoping)."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    companyId: int | None = None
    hrId: int | None = None
    hasFullAccess: bool = False
    allowedPoolIds: list[int] | None = None
    allowedCollaboratorIds: list[int] | None = None


class SearchRequest(BaseModel):
    """Natural-language search request."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    query: str = Field(min_length=1)
    companyId: int = Field(default=1, gt=0)
    hrId: int | None = Field(default=None, gt=0)
    topK: int = Field(default=20, ge=1, le=5000)
    recallPoolSize: int | None = Field(default=None, ge=1, le=5000)
    details: bool = Field(default=False)
    permission: PermissionContext | None = None
    conversationId: str | None = None


class CandidateItem(BaseModel):
    """One candidate in the result list. camelCase on the wire."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    id: str | None = None
    docId: str
    candidateProfileId: str | None = None
    userId: int | str | None = None
    newestAppId: int | None = None
    score: float = 0.0
    matchRate: int | None = None
    name: str | None = None
    workYears: float | None = None
    summaryEn: str | None = None
    avatar: str = ""
    summary: str = ""
    gender: int | None = None
    age: int | None = None
    currentResidence: str | None = None
    tags: list[str] = Field(default_factory=list)
    skills: list[dict] = Field(default_factory=list)
    certificates: list[dict] = Field(default_factory=list)
    languages: list[dict] = Field(default_factory=list)
    workExperiences: list[dict] = Field(default_factory=list)
    internshipExperiences: list[dict] = Field(default_factory=list)
    projectExperiences: list[dict] = Field(default_factory=list)
    educations: list[dict] = Field(default_factory=list)
    expectedPositions: list[str] = Field(default_factory=list)
    expectedIndustries: list[str] = Field(default_factory=list)
    expectedCities: list[str] = Field(default_factory=list)
    expectedSalary: str | None = None
    expectedWorkType: str | None = None
    workState: str | None = None
    softMatchBreakdown: list[dict] | None = None
    profileSnapshot: dict | None = None
    matchExplanation: dict | None = None


class SearchResponse(BaseModel):
    """Search response, camelCase on the wire."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    query: str
    total: int
    items: list[CandidateItem]
    debug: dict | None = None
    clarification: dict | None = None


__all__ = ["CandidateItem", "PermissionContext", "SearchRequest", "SearchResponse"]
