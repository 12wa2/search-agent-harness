"""Search Agent Harness -- an eight-stage reflective retrieval agent.

The agent reasons (understand/plan/judge/reflect/revise) and calls a search
backend only through the ``SearchTool`` protocol, so backends can be swapped
without touching the harness. See docs/ARCHITECTURE.md for the design.
"""

from search_agent.config import budget_from_env
from search_agent.contract.tool import (
    RerankToolRequest,
    SearchTool,
    SearchToolRequest,
    SearchToolResult,
    ToolCandidate,
)
from search_agent.harness import Harness
from search_agent.models.api import CandidateItem, SearchRequest, SearchResponse
from search_agent.runner import HarnessRunner, LoopRunner
from search_agent.service import SearchAgentService

__version__ = "0.1.0"

__all__ = [
    "CandidateItem",
    "Harness",
    "HarnessRunner",
    "LoopRunner",
    "RerankToolRequest",
    "SearchAgentService",
    "SearchRequest",
    "SearchResponse",
    "SearchTool",
    "SearchToolRequest",
    "SearchToolResult",
    "ToolCandidate",
    "budget_from_env",
    "__version__",
]
