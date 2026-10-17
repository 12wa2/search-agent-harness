"""Usage accounting + resilience for the LLM clients.

The OpenAI path is exercised with an injected fake ``openai`` module, so retry,
backoff and JSON-repair wiring are covered without any network access.
"""

from __future__ import annotations

import sys
import types

import pytest

from search_agent.core.budget import BudgetState
from search_agent.llm.client import (
    FakeLlmClient,
    LlmUsage,
    MeteredLlmClient,
    OpenAILlmClient,
    estimate_tokens,
    parse_json_object,
)
from search_agent.models.artifacts import Budget


class _Usage:
    def __init__(self, prompt: int, completion: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion


class _HttpError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"http {status_code}")
        self.status_code = status_code


def _resp(content, usage):
    message = types.SimpleNamespace(content=content)
    choice = types.SimpleNamespace(message=message)
    return types.SimpleNamespace(choices=[choice], usage=usage)


def _install_openai_script(monkeypatch, script):
    """Feed the client a script: exceptions raise, tuples are (content, usage)."""
    module = types.ModuleType("openai")
    calls: list[dict] = []

    class _OpenAI:
        def __init__(self, **_kwargs):
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=self._create)
            )

        def _create(self, **kwargs):
            calls.append(kwargs)
            item = script.pop(0)
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, tuple):
                content, usage = item
            else:
                content, usage = '{"ok": true}', item
            return _resp(content, usage)

    module.OpenAI = _OpenAI
    monkeypatch.setitem(sys.modules, "openai", module)
    return calls


def _client(**kwargs):
    sleeps: list[float] = []
    client = OpenAILlmClient(api_key="k", sleep=sleeps.append, **kwargs)
    return client, sleeps


def test_openai_client_records_provider_usage(monkeypatch):
    _install_openai_script(monkeypatch, [_Usage(120, 30)])
    client, _ = _client()
    result = client.complete_json(system="s", user="u", schema_name="x", schema={})
    assert result == {"ok": True}
    assert client.last_usage == LlmUsage(prompt_tokens=120, completion_tokens=30, calls=1)
    assert client.usage.total_tokens == 150
    assert client.usage.estimated is False


def test_disable_thinking_sends_qwen_extension(monkeypatch):
    monkeypatch.setenv("SEARCH_AGENT_DISABLE_THINKING", "1")
    calls = _install_openai_script(monkeypatch, [_Usage(1, 1)])
    client, _ = _client()
    client.complete_json(system="s", user="u", schema_name="x", schema={})
    assert calls[0]["extra_body"] == {"enable_thinking": False}


def test_thinking_extension_is_opt_in(monkeypatch):
    monkeypatch.delenv("SEARCH_AGENT_DISABLE_THINKING", raising=False)
    calls = _install_openai_script(monkeypatch, [_Usage(1, 1)])
    client, _ = _client()
    client.complete_json(system="s", user="u", schema_name="x", schema={})
    assert "extra_body" not in calls[0]


def test_llm_timeout_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("SEARCH_AGENT_LLM_TIMEOUT_SEC", "12.5")
    calls = _install_openai_script(monkeypatch, [_Usage(1, 1)])
    client, _ = _client()
    client.complete_json(system="s", user="u", schema_name="x", schema={})
    assert calls[0]["timeout"] == 12.5


def test_openai_client_estimates_when_gateway_omits_usage(monkeypatch):
    _install_openai_script(monkeypatch, [None])
    client, _ = _client()
    client.complete_json(system="系统提示词", user="用户输入", schema_name="x", schema={})
    usage = client.last_usage
    assert usage is not None
    assert usage.estimated is True
    assert usage.total_tokens > 0


def test_transient_error_is_retried_with_backoff(monkeypatch):
    _install_openai_script(monkeypatch, [TimeoutError("t"), _Usage(10, 5)])
    client, sleeps = _client()
    result = client.complete_json(system="s", user="u", schema_name="x", schema={})
    assert result == {"ok": True}
    assert client.retries == 1
    assert client.last_retries == 1
    assert len(sleeps) == 1
    assert sleeps[0] > 0
    assert client.usage.total_tokens == 15


def test_retryable_status_codes_use_exponential_backoff(monkeypatch):
    _install_openai_script(monkeypatch, [_HttpError(429), _HttpError(503), _Usage(1, 1)])
    client, sleeps = _client()
    client.complete_json(system="s", user="u", schema_name="x", schema={})
    assert client.retries == 2
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]


def test_fatal_error_is_not_retried(monkeypatch):
    _install_openai_script(monkeypatch, [_HttpError(400)])
    client, sleeps = _client()
    with pytest.raises(_HttpError):
        client.complete_json(system="s", user="u", schema_name="x", schema={})
    assert client.retries == 0
    assert sleeps == []


def test_retries_give_up_and_reraise(monkeypatch):
    _install_openai_script(monkeypatch, [TimeoutError("t")] * 3)
    client, sleeps = _client()
    with pytest.raises(TimeoutError):
        client.complete_json(system="s", user="u", schema_name="x", schema={})
    assert client.retries == 2
    assert len(sleeps) == 2


def test_fenced_json_is_parsed(monkeypatch):
    payload = '```json\n{"ok": true}\n```'
    _install_openai_script(monkeypatch, [(payload, _Usage(5, 5))])
    client, _ = _client()
    assert client.complete_json(system="s", user="u", schema_name="x", schema={}) == {"ok": True}


def test_invalid_json_triggers_one_repair(monkeypatch):
    _install_openai_script(
        monkeypatch,
        [("抱歉，我无法回答", _Usage(5, 5)), ('{"ok": true}', _Usage(6, 5))],
    )
    client, _ = _client()
    assert client.complete_json(system="s", user="u", schema_name="x", schema={}) == {"ok": True}
    assert client.repairs == 1
    assert client.last_repairs == 1
    assert client.usage.total_tokens == 21


def test_invalid_json_raises_after_repair_budget(monkeypatch):
    _install_openai_script(monkeypatch, [("nope", _Usage(5, 5)), ("still nope", _Usage(5, 5))])
    client, _ = _client()
    with pytest.raises(ValueError):
        client.complete_json(system="s", user="u", schema_name="x", schema={})
    assert client.repairs == 1


def test_parse_json_object_handles_prose_and_fences():
    assert parse_json_object('前言 {"a": 1} 后记') == {"a": 1}
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json_object("no braces here")
    with pytest.raises(ValueError):
        parse_json_object("[1, 2]")


def test_fake_client_accumulates_estimated_usage():
    client = FakeLlmClient({"agent_plan": {"paths": []}})
    client.complete_json(system="s", user="u", schema_name="agent_plan", schema={})
    client.complete_json(system="s", user="u", schema_name="agent_missing", schema={})
    assert client.usage.calls == 2
    assert client.usage.estimated is True
    assert client.last_usage is not None
    assert client.last_usage.estimated is True


def test_metered_client_records_tokens_and_retries_into_budget():
    bs = BudgetState(budget=Budget())
    inner = FakeLlmClient({"agent_plan": {"paths": []}})
    inner.last_retries = 2
    metered = MeteredLlmClient(inner, bs)
    metered.complete_json(system="系统提示", user="用户输入", schema_name="agent_plan", schema={})
    assert bs.tokens_used > 0
    assert bs.tokens_estimated is True
    assert bs.llm_retries == 2
    assert metered.usage is inner.usage  # attribute passthrough to the wrapped client


def test_metered_client_estimates_for_custom_clients():
    class _Bare:
        def complete_json(self, **_kwargs):
            return {"paths": []}

    bs = BudgetState(budget=Budget())
    metered = MeteredLlmClient(_Bare(), bs)
    metered.complete_json(system="s", user="u", schema_name="agent_plan", schema={})
    assert bs.tokens_used > 0
    assert bs.tokens_estimated is True


def test_estimate_tokens_scales_with_length():
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("高并发系统设计") == 7
