"""Runtime presets switch both the LLM and the SearchTool."""

from __future__ import annotations

import pytest

from search_agent.contract.tool import SearchToolResult
from search_agent.tools.runtime import RuntimeSearchTool


class _OfflineSpy:
    def __init__(self) -> None:
        self.search_calls = 0
        self.rerank_calls = 0

    def search_with_intent(self, _req):
        self.search_calls += 1
        return SearchToolResult(total=1, quality="rich")

    def rerank_pool(self, _req):
        self.rerank_calls += 1
        return SearchToolResult(total=1, quality="rich")


class _OnlineSpy(_OfflineSpy):
    pass


def test_runtime_search_tool_switches_both_protocol_methods() -> None:
    mode = ["offline"]
    offline = _OfflineSpy()
    online = _OnlineSpy()
    tool = RuntimeSearchTool(offline, online, mode=lambda: mode[0])

    assert tool.active_tool_name == "_OfflineSpy"
    assert tool.offline_tool_name == "_OfflineSpy"
    assert tool.online_tool_name == "_OnlineSpy"
    assert tool.online_configured is True

    request = object()
    assert tool.search_with_intent(request).total == 1
    assert tool.rerank_pool(request).total == 1
    assert offline.search_calls == 1
    assert offline.rerank_calls == 1
    assert online.search_calls == 0
    assert online.rerank_calls == 0

    mode[0] = "online"
    assert tool.active_tool_name == "_OnlineSpy"
    assert tool.search_with_intent(request).total == 1
    assert tool.rerank_pool(request).total == 1
    assert offline.search_calls == 1
    assert offline.rerank_calls == 1
    assert online.search_calls == 1
    assert online.rerank_calls == 1


def test_runtime_search_tool_rejects_missing_online_backend() -> None:
    offline = _OfflineSpy()
    tool = RuntimeSearchTool(offline, None, mode=lambda: "online")

    assert tool.online_configured is False
    assert tool.online_tool_name is None
    with pytest.raises(RuntimeError, match="Hybrid V2"):
        tool.search_with_intent(object())
