"""SearchTool contract: the stable boundary between the agent harness and search backends.

The Agent depends ONLY on this protocol plus the IR contract types
(``CandidateSearchIR`` etc.) and ``CandidateItem``. Real backends implement it.
See ``tools/memory.py`` for a complete reference implementation.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from search_agent.models.api import CandidateItem
from search_agent.models.ir import CandidateSearchIR

ToolQuality = Literal["rich", "thin", "empty"]


class SearchToolRequest(BaseModel):
    """Structured intent handed to the tool. Agent already did understanding,
    so the tool skips ``parse_query_to_ir`` and executes from ``intent``."""

    intent: CandidateSearchIR
    query_text: str
    company_id: int
    hr_id: int | None = None
    permission: dict[str, Any] | None = None
    top_k: int
    recall_pool_size: int | None = None
    routing: int
    exclude_doc_ids: list[str] = Field(default_factory=list)
    details: bool = False
    skip_rerank: bool = False


class ToolCandidate(BaseModel):
    """A ranked candidate as returned by the tool, with structured facts."""

    doc_id: str
    score: float
    item: CandidateItem
    hard_matches: list[dict[str, Any]] = Field(default_factory=list)
    match_explanation: dict[str, Any] = Field(default_factory=dict)
    path_timings_ms: dict[str, int] = Field(default_factory=dict)


class SearchToolResult(BaseModel):
    """Result of one ``search_with_intent`` call (one path)."""

    items: list[ToolCandidate] = Field(default_factory=list)
    total: int = 0
    total_relation: str = "eq"
    quality: ToolQuality = "empty"
    degraded_reason: str | None = None
    es_query: dict[str, Any] | None = None
    timings_ms: dict[str, int] = Field(default_factory=dict)


class RerankToolRequest(BaseModel):
    """Global rerank over a cross-path merged pool."""

    query_text: str
    candidates: list[ToolCandidate]
    job_context: dict[str, Any]
    top_k: int
    routing: int


@runtime_checkable
class SearchTool(Protocol):
    """Agent's only execution interface. Search backends implement it."""

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult: ...

    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult: ...


__all__ = [
    "RerankToolRequest",
    "SearchTool",
    "SearchToolRequest",
    "SearchToolResult",
    "ToolCandidate",
    "ToolQuality",
]
