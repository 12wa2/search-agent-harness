"""LLM client abstraction for agent stages.

- ``LlmClient``: the protocol every stage depends on.
- ``OpenAILlmClient``: real client for any OpenAI-compatible chat endpoint
  (OpenAI, Qwen/DashScope, DeepSeek, vLLM, ...) using strict json_schema output.
- ``FakeLlmClient``: canned responses keyed by ``schema_name`` (tests/demo).

Environment for ``OpenAILlmClient``::

    SEARCH_AGENT_API_KEY / OPENAI_API_KEY
    SEARCH_AGENT_BASE_URL / OPENAI_BASE_URL   # optional
    SEARCH_AGENT_MODEL                        # default model name
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Protocol


class LlmClient(Protocol):
    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]: ...


class OpenAILlmClient:
    """Chat Completions client with strict JSON-schema output."""

    def __init__(
        self,
        default_model: str | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._default_model = default_model or os.environ.get("SEARCH_AGENT_MODEL") or "gpt-4o-mini"
        self._api_key = (
            api_key or os.environ.get("SEARCH_AGENT_API_KEY") or os.environ.get("OPENAI_API_KEY")
        )
        self._base_url = (
            base_url or os.environ.get("SEARCH_AGENT_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        )
        self._timeout = timeout

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                'real LLM calls require the "openai" package: '
                'pip install "search-agent-harness[openai]"'
            ) from exc
        if not self._api_key:
            raise RuntimeError(
                "missing API key: set SEARCH_AGENT_API_KEY (or OPENAI_API_KEY), "
                "or run with --offline to use the scripted demo LLM"
            )
        client = OpenAI(api_key=self._api_key, base_url=self._base_url)
        resp = client.chat.completions.create(
            model=model or self._default_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
            timeout=self._timeout,
        )
        content = (resp.choices[0].message.content or "{}").strip()
        return json.loads(content)


class FakeLlmClient:
    """Returns canned JSON by schema_name; records calls. For tests."""

    def __init__(
        self,
        responses: dict[str, Any] | Callable[[str, str], Any] | None = None,
    ) -> None:
        self._responses = responses or {}
        self.calls: list[tuple[str, str]] = []

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append((schema_name, user))
        if callable(self._responses):
            return self._responses(schema_name, user)
        if schema_name in self._responses:
            r = self._responses[schema_name]
            return r() if callable(r) else r
        return {}


__all__ = ["FakeLlmClient", "LlmClient", "OpenAILlmClient"]
