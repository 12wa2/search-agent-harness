"""Cooperative cancellation for one search run.

The signal is checked at stage boundaries and before every LLM call. An
in-flight provider request is deliberately not force-killed: after it returns,
the harness stops at the next boundary. This keeps cancellation deterministic
without relying on provider-specific request-abort behavior.
"""

from __future__ import annotations

from threading import Event
from typing import Any


class SearchCancelled(RuntimeError):
    """Raised when a run observes its cancellation signal."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        super().__init__(f"search cancelled at {stage}")


def is_cancelled(cancel_event: Event | None) -> bool:
    """True when the caller has requested cancellation."""
    return cancel_event is not None and cancel_event.is_set()


def raise_if_cancelled(cancel_event: Event | None, *, stage: str) -> None:
    """Stop before starting new work once cancellation has been requested."""
    if is_cancelled(cancel_event):
        raise SearchCancelled(stage)


def mark_cancelled(state: dict[str, Any], *, stage: str) -> None:
    """Record a terminal, partial run without misreporting it as a failure."""
    state["cancelled"] = True
    state["cancelled_at"] = stage
    state["degraded_reason"] = "cancelled"
    state["phase"] = "done"


__all__ = [
    "SearchCancelled",
    "is_cancelled",
    "mark_cancelled",
    "raise_if_cancelled",
]
