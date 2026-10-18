"""Runtime-selected SearchTool used by the HTTP console.

The offline and online presets differ in both layers: offline uses the scripted
LLM plus the in-memory demo corpus, while online uses a real provider plus the
configured retrieval backend. Mode changes are guarded by ``RuntimeLlmRouter``
at the server boundary, so one active run cannot mix two tools.
"""

from __future__ import annotations

from typing import Callable

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchTool,
    SearchToolRequest,
    SearchToolResult,
)
from search_agent.llm.runtime import RuntimeMode


class RuntimeSearchTool:
    """Delegate ``SearchTool`` calls to the tool selected by runtime mode."""

    def __init__(
        self,
        offline_tool: SearchTool,
        online_tool: SearchTool | None,
        *,
        mode: Callable[[], RuntimeMode],
    ) -> None:
        self._offline_tool = offline_tool
        self._online_tool = online_tool
        self._mode = mode

    @property
    def online_configured(self) -> bool:
        return self._online_tool is not None

    @property
    def active_tool_name(self) -> str:
        return type(self._active_tool()).__name__

    @property
    def offline_tool_name(self) -> str:
        return type(self._offline_tool).__name__

    @property
    def online_tool_name(self) -> str | None:
        return type(self._online_tool).__name__ if self._online_tool else None

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult:
        return self._active_tool().search_with_intent(req)

    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult:
        return self._active_tool().rerank_pool(req)

    def _active_tool(self) -> SearchTool:
        if self._mode() == "offline":
            return self._offline_tool
        if self._online_tool is None:
            raise RuntimeError("完整在线模式需要配置在线检索后端（Hybrid V2 + Elasticsearch）。")
        return self._online_tool


__all__ = ["RuntimeSearchTool"]
