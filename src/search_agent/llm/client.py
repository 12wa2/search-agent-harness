"""LLM client abstraction for agent stages.

- ``LlmClient``: the protocol every stage depends on.
- ``OpenAILlmClient``: real client for any OpenAI-compatible chat endpoint
  (OpenAI, Qwen/DashScope, DeepSeek, vLLM, ...) preferring strict json_schema
  output and downgrading to json_object or prompt-only JSON when a provider
  rejects that request field.
  Transient failures (timeout / 429 / 5xx) are retried with exponential backoff;
  replies that are not valid JSON get one repair round instead of crashing the loop.
- ``MeteredLlmClient``: wraps any client so token usage lands in the run budget.
- ``FakeLlmClient``: canned responses keyed by ``schema_name`` (tests/demo).

Environment for ``OpenAILlmClient``::

    SEARCH_AGENT_API_KEY / OPENAI_API_KEY
    SEARCH_AGENT_BASE_URL / OPENAI_BASE_URL   # optional
    SEARCH_AGENT_MODEL                        # default model name
    SEARCH_AGENT_LLM_RETRIES                  # transient-failure retries (2)
    SEARCH_AGENT_LLM_JSON_REPAIRS             # JSON repair rounds (1)
    SEARCH_AGENT_LLM_BACKOFF_SEC              # base backoff, doubling (0.5)
    SEARCH_AGENT_LLM_TIMEOUT_SEC              # per-call timeout (60)
    SEARCH_AGENT_RESPONSE_FORMAT              # auto | json_schema | json_object | none
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from threading import Event
from typing import Any, Callable, Protocol

from search_agent.core.budget import BudgetState
from search_agent.core.cancellation import raise_if_cancelled

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_RETRYABLE_NAMES = (
    "APITimeoutError",
    "APIConnectionError",
    "RateLimitError",
    "InternalServerError",
    "ServiceUnavailableError",
    "Timeout",
)
_REPAIR_PROMPT = (
    "上一条回复不是合法的 JSON 对象。只输出一个符合给定 schema 的 JSON 对象："
    "不要解释，不要 markdown 代码块。"
)
_RESPONSE_FORMAT_MODES = ("json_schema", "json_object", "none")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_retryable(exc: BaseException) -> bool:
    """Transient provider failure worth another attempt (timeouts, 429, 5xx)."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status in _RETRYABLE_STATUS:
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return any(token in type(exc).__name__ for token in _RETRYABLE_NAMES)


def is_response_format_unsupported(exc: BaseException) -> bool:
    """Whether a provider rejected ``response_format`` rather than the request overall."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status not in {400, 404, 422}:
        return False
    parts = [str(exc)]
    for attr in ("message", "code", "param", "body"):
        value = getattr(exc, attr, None)
        if value is not None:
            parts.append(repr(value))
    text = " ".join(parts).lower()
    format_marker = any(
        token in text
        for token in ("response_format", "json_schema", "json schema", "structured output")
    )
    unsupported_marker = any(
        token in text
        for token in (
            "unsupported",
            "not supported",
            "does not support",
            "unknown",
            "unrecognized",
            "invalid parameter",
            "extra inputs",
            "not permitted",
            "not allowed",
        )
    )
    return format_marker and unsupported_marker


def parse_json_object(content: str) -> dict[str, Any]:
    """Parse a JSON object out of a model reply, tolerating fences and prose."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"no JSON object in model reply: {exc}") from exc
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as inner:
            raise ValueError(f"invalid JSON in model reply: {inner}") from inner
    if not isinstance(data, dict):
        raise ValueError("model reply is not a JSON object")
    return data


@dataclass
class LlmUsage:
    """Token usage of one call, or an accumulation of calls."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    #: True when the numbers come from a local estimate, not the provider.
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, other: "LlmUsage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.calls += max(1, other.calls)
        self.estimated = self.estimated or other.estimated


def estimate_tokens(text: str) -> int:
    """Rough fallback estimate: CJK chars ~= 1 token, other text ~= 4 chars/token."""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return max(1, cjk + max(0, len(text) - cjk) // 4)


def estimated_usage(system: str, user: str, result: Any) -> LlmUsage:
    """Estimated usage for clients that cannot report real token counts."""
    prompt = estimate_tokens(system) + estimate_tokens(user)
    completion = estimate_tokens(json.dumps(result, ensure_ascii=False, default=str))
    return LlmUsage(prompt_tokens=prompt, completion_tokens=completion, calls=1, estimated=True)


class MeteredLlmClient:
    """Wrap a client so every call's token usage lands in the run budget.

    Bundled clients set ``last_usage`` from the provider response (or from a
    local estimate). A client that reports nothing still gets a
    character-based estimate, so the budget never silently counts zero.
    """

    def __init__(
        self,
        inner: LlmClient,
        budget_state: BudgetState,
        cancel_event: Event | None = None,
    ) -> None:
        self._inner = inner
        self._budget_state = budget_state
        self._cancel_event = cancel_event

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        raise_if_cancelled(self._cancel_event, stage=f"llm:{schema_name}")
        result = self._inner.complete_json(
            system=system, user=user, schema_name=schema_name, schema=schema, model=model
        )
        usage = getattr(self._inner, "last_usage", None) or estimated_usage(system, user, result)
        self._budget_state.record_llm_tokens(
            prompt=usage.prompt_tokens,
            completion=usage.completion_tokens,
            estimated=bool(usage.estimated),
        )
        self._budget_state.record_llm_retries(int(getattr(self._inner, "last_retries", 0) or 0))
        return result

    def __getattr__(self, name: str) -> Any:
        """Expose the wrapped client's attributes (``usage``, ``calls``, ...)."""
        return getattr(self._inner, name)


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
    """Chat Completions client with strict JSON-schema output + resilience.

    Two failure classes are handled without taking the loop down:

    * transient provider errors (timeout / connection reset / 429 / 5xx) are
      retried ``SEARCH_AGENT_LLM_RETRIES`` times with exponential backoff + jitter;
    * a reply that is not a parseable JSON object gets one repair round that
      shows the model its own output and asks for valid JSON only.
    """

    def __init__(
        self,
        default_model: str | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._default_model = default_model or os.environ.get("SEARCH_AGENT_MODEL") or "gpt-4o-mini"
        self._api_key = (
            api_key or os.environ.get("SEARCH_AGENT_API_KEY") or os.environ.get("OPENAI_API_KEY")
        )
        self._base_url = (
            base_url or os.environ.get("SEARCH_AGENT_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        )
        self._timeout = (
            timeout if timeout is not None else _env_float("SEARCH_AGENT_LLM_TIMEOUT_SEC", 60.0)
        )
        self._max_retries = _env_int("SEARCH_AGENT_LLM_RETRIES", 2)
        self._repair_retries = _env_int("SEARCH_AGENT_LLM_JSON_REPAIRS", 1)
        self._backoff_sec = _env_float("SEARCH_AGENT_LLM_BACKOFF_SEC", 0.5)
        self._disable_thinking = _env_bool("SEARCH_AGENT_DISABLE_THINKING", False)
        configured_format = os.environ.get("SEARCH_AGENT_RESPONSE_FORMAT", "auto").strip().lower()
        self._response_format = configured_format or "auto"
        self._sleep = sleep or time.sleep
        self._client: Any | None = None
        self._resolved_format: str | None = None
        self.usage = LlmUsage()
        self.last_usage: LlmUsage | None = None
        #: Cumulative and per-call resilience counters (observability).
        self.retries = 0
        self.last_retries = 0
        self.repairs = 0
        self.last_repairs = 0
        self.format_fallbacks = 0
        self.last_format_fallbacks = 0

    @property
    def is_configured(self) -> bool:
        """Whether an API key was available when this client was constructed."""
        return bool(self._api_key)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                'real LLM calls require the "openai" package: '
                'pip install "search-agent-harness[openai]"'
            ) from exc
        self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client

    def _format_sequence(self) -> list[str]:
        if self._response_format in _RESPONSE_FORMAT_MODES:
            return [self._response_format]
        if self._response_format not in {"auto", "prompt"}:
            logger.warning(
                "Unknown SEARCH_AGENT_RESPONSE_FORMAT=%s; using auto",
                self._response_format,
            )
        if self._resolved_format in _RESPONSE_FORMAT_MODES:
            return [self._resolved_format]
        return list(_RESPONSE_FORMAT_MODES)

    @staticmethod
    def _response_format_payload(mode: str, schema_name: str, schema: dict[str, Any]) -> Any:
        if mode == "json_schema":
            return {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
        if mode == "json_object":
            return {"type": "json_object"}
        return None

    def close(self) -> None:
        client, self._client = self._client, None
        close = getattr(client, "close", None)
        if callable(close):
            close()

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        if not self._api_key:
            raise RuntimeError(
                "missing API key: set SEARCH_AGENT_API_KEY (or OPENAI_API_KEY), "
                "or run with --offline to use the scripted demo LLM"
            )
        client = self._get_client()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        self.last_retries = 0
        self.last_repairs = 0
        self.last_format_fallbacks = 0
        attempt = 0
        repairs = 0
        format_modes = self._format_sequence()
        format_index = 0
        format_fallbacks = 0
        while True:
            mode = format_modes[format_index]
            try:
                request_kwargs: dict[str, Any] = {
                    "model": model or self._default_model,
                    "messages": messages,
                    "timeout": self._timeout,
                }
                response_format = self._response_format_payload(mode, schema_name, schema)
                if response_format is not None:
                    request_kwargs["response_format"] = response_format
                if self._disable_thinking:
                    request_kwargs["extra_body"] = {"enable_thinking": False}
                resp = client.chat.completions.create(**request_kwargs)
            except Exception as exc:  # noqa: BLE001 - classified right below
                if format_index + 1 < len(format_modes) and is_response_format_unsupported(exc):
                    next_mode = format_modes[format_index + 1]
                    logger.warning(
                        "Provider rejected response_format=%s (%s); falling back to %s",
                        mode,
                        type(exc).__name__,
                        next_mode,
                    )
                    format_index += 1
                    format_fallbacks += 1
                    self.format_fallbacks += 1
                    self.last_format_fallbacks = format_fallbacks
                    continue
                if attempt >= self._max_retries or not is_retryable(exc):
                    raise
                attempt += 1
                self.retries += 1
                self.last_retries = attempt
                delay = self._backoff_sec * (2 ** (attempt - 1))
                delay += random.uniform(0, delay / 2)
                logger.warning(
                    "LLM call failed (%s); retry %d/%d in %.2fs",
                    type(exc).__name__,
                    attempt,
                    self._max_retries,
                    delay,
                )
                self._sleep(delay)
                continue

            content = (resp.choices[0].message.content or "{}").strip()
            self._record_response_usage(resp, system, user, content)
            try:
                result = parse_json_object(content)
            except ValueError as exc:
                if repairs >= self._repair_retries:
                    raise
                repairs += 1
                self.repairs += 1
                self.last_repairs = repairs
                logger.warning("LLM reply was not valid JSON (%s); requesting a repair", exc)
                messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": _REPAIR_PROMPT},
                ]
                continue
            if format_index > 0:
                self._resolved_format = mode
            return result

    def _record_response_usage(self, resp: Any, system: str, user: str, content: str) -> None:
        """Count tokens for every provider response, even unparseable ones."""
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.last_usage = LlmUsage(
                prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                calls=1,
            )
        else:  # some OpenAI-compatible gateways omit usage
            self.last_usage = LlmUsage(
                prompt_tokens=estimate_tokens(system) + estimate_tokens(user),
                completion_tokens=estimate_tokens(content),
                calls=1,
                estimated=True,
            )
        self.usage.add(self.last_usage)


class FakeLlmClient:
    """Returns canned JSON by schema_name; records calls. For tests."""

    def __init__(
        self,
        responses: dict[str, Any] | Callable[[str, str], Any] | None = None,
    ) -> None:
        self._responses = responses or {}
        self.calls: list[tuple[str, str]] = []
        self.usage = LlmUsage()
        self.last_usage: LlmUsage | None = None

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
            result = self._responses(schema_name, user)
        elif schema_name in self._responses:
            r = self._responses[schema_name]
            result = r() if callable(r) else r
        else:
            result = {}
        self.last_usage = estimated_usage(system, user, result)
        self.usage.add(self.last_usage)
        return result


__all__ = [
    "FakeLlmClient",
    "LlmClient",
    "LlmUsage",
    "MeteredLlmClient",
    "OpenAILlmClient",
    "estimate_tokens",
    "is_response_format_unsupported",
    "estimated_usage",
    "is_retryable",
    "parse_json_object",
]
