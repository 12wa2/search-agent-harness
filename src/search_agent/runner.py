"""HarnessRunner: pluggable execution backend.

LoopRunner (default) delegates to ``Harness.run`` -- a plain-Python loop.
LangGraphRunner (future work) compiles the same stages into a StateGraph with
``Send`` for parallel execute and reducer-based merge; harness logic stays
unchanged because stages already return full values (not deltas).
"""

from __future__ import annotations

from typing import Any, Protocol

from search_agent.harness import Harness
from search_agent.stages.base import Deps


class HarnessRunner(Protocol):
    def run(self, initial_state: dict[str, Any], deps: Deps) -> dict[str, Any]: ...


class LoopRunner:
    """Default backend: plain-Python loop via Harness."""

    def run(self, initial_state: dict[str, Any], deps: Deps) -> dict[str, Any]:
        return Harness(deps).run(initial_state)


__all__ = ["HarnessRunner", "LoopRunner"]
