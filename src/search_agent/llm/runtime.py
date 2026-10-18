"""Runtime-switchable LLM routing for the HTTP demo.

The app keeps both an offline scripted client and an online provider client.
One search run may use several LLM calls, so changing the active client while
a run is in flight would make its trace impossible to attribute. The router
therefore rejects mode changes until all active runs have finished.
"""

from __future__ import annotations

import threading
from typing import Any, Literal

from search_agent.llm.client import LlmClient

RuntimeMode = Literal["offline", "online"]
_VALID_MODES = frozenset({"offline", "online"})


class RuntimeModeError(RuntimeError):
    """A runtime mode change that cannot be applied safely."""


class RuntimeLlmRouter:
    """Route ``complete_json`` calls to the currently selected LLM client."""

    def __init__(
        self,
        offline_client: LlmClient,
        online_client: LlmClient,
        *,
        initial_mode: RuntimeMode = "offline",
        online_configured: bool | None = None,
    ) -> None:
        if initial_mode not in _VALID_MODES:
            raise ValueError(f"unknown runtime mode: {initial_mode!r}")
        self._clients: dict[str, LlmClient] = {
            "offline": offline_client,
            "online": online_client,
        }
        self._mode = initial_mode
        if online_configured is None:
            configured = getattr(online_client, "is_configured", True)
            online_configured = bool(configured)
        self._online_configured = online_configured
        self._active_runs = 0
        self._lock = threading.Lock()

    @property
    def mode(self) -> RuntimeMode:
        with self._lock:
            return self._mode

    @property
    def online_configured(self) -> bool:
        return self._online_configured

    @property
    def active_runs(self) -> int:
        with self._lock:
            return self._active_runs

    @property
    def active_client_name(self) -> str:
        return type(self._client()).__name__

    @property
    def last_usage(self) -> Any:
        return getattr(self._client(), "last_usage", None)

    @property
    def last_retries(self) -> int:
        return int(getattr(self._client(), "last_retries", 0) or 0)

    @property
    def last_repairs(self) -> int:
        return int(getattr(self._client(), "last_repairs", 0) or 0)

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        return self._client().complete_json(
            system=system,
            user=user,
            schema_name=schema_name,
            schema=schema,
            model=model,
        )

    def begin_run(self) -> None:
        """Mark a search run active so the mode cannot change mid-trace."""
        with self._lock:
            self._active_runs += 1

    def end_run(self) -> None:
        """Release a search run; safe if cleanup runs more than once."""
        with self._lock:
            if self._active_runs > 0:
                self._active_runs -= 1

    def switch_mode(self, mode: RuntimeMode) -> None:
        """Switch the LLM used by subsequent search calls."""
        if mode not in _VALID_MODES:
            raise RuntimeModeError(f"不支持的模型模式：{mode}")
        with self._lock:
            if self._active_runs:
                raise RuntimeModeError("Agent 正在执行搜索，完成或停止后再切换模型模式。")
            if mode == "online" and not self._online_configured:
                raise RuntimeModeError(
                    "在线模型尚未配置。请设置 SEARCH_AGENT_API_KEY（或 OPENAI_API_KEY），"
                    "并重启服务后再切换。"
                )
            self._mode = mode

    def _client(self) -> LlmClient:
        with self._lock:
            return self._clients[self._mode]


__all__ = ["RuntimeLlmRouter", "RuntimeMode", "RuntimeModeError"]
