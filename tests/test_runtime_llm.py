"""Runtime LLM routing tests that do not require FastAPI."""

from __future__ import annotations

import pytest

from search_agent.demo import ScriptedLlmClient
from search_agent.llm.runtime import RuntimeLlmRouter, RuntimeModeError


def _call(router: RuntimeLlmRouter) -> dict:
    return router.complete_json(
        system="system",
        user="user",
        schema_name="unused",
        schema={},
    )


def test_switch_mode_routes_later_calls_to_the_selected_client() -> None:
    offline = ScriptedLlmClient()
    online = ScriptedLlmClient()
    router = RuntimeLlmRouter(offline, online, initial_mode="offline")

    _call(router)
    assert len(offline.calls) == 1
    assert not online.calls

    router.switch_mode("online")
    _call(router)

    assert router.mode == "online"
    assert len(offline.calls) == 1
    assert len(online.calls) == 1
    assert router.last_usage is online.last_usage


def test_switch_mode_is_rejected_while_a_run_is_active() -> None:
    router = RuntimeLlmRouter(
        ScriptedLlmClient(),
        ScriptedLlmClient(),
        initial_mode="offline",
    )
    router.begin_run()
    try:
        with pytest.raises(RuntimeModeError, match="正在执行搜索"):
            router.switch_mode("online")
    finally:
        router.end_run()

    router.switch_mode("online")
    assert router.mode == "online"


def test_switch_to_unconfigured_online_client_is_rejected() -> None:
    class UnconfiguredClient(ScriptedLlmClient):
        is_configured = False

    router = RuntimeLlmRouter(
        ScriptedLlmClient(),
        UnconfiguredClient(),
        initial_mode="offline",
    )

    with pytest.raises(RuntimeModeError, match="SEARCH_AGENT_API_KEY"):
        router.switch_mode("online")

    assert router.mode == "offline"
