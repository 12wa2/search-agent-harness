"""Bounded, process-local admission control for agent runs.

The limiter protects the HTTP process and downstream LLM/ES capacity from an
unbounded number of simultaneous searches. It is deliberately small and
in-process: one uvicorn worker owns one limiter. A deployment with multiple
workers therefore applies the configured limit per worker; a global limit
would need a shared gateway or a distributed lease.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Literal

from search_agent.config import (
    max_concurrent_runs_from_env,
    max_queued_runs_from_env,
    run_queue_timeout_sec_from_env,
)

RejectionReason = Literal["queue_full", "timeout"]


class ConcurrencyLimitError(RuntimeError):
    """A request could not be admitted into the bounded run slot."""

    def __init__(
        self,
        reason: RejectionReason,
        retry_after_sec: int,
        snapshot: dict[str, Any],
    ) -> None:
        self.reason = reason
        self.retry_after_sec = max(1, int(retry_after_sec))
        self.snapshot = snapshot
        message = (
            "Agent 并发队列已满，请稍后重试。"
            if reason == "queue_full"
            else "Agent 并发等待超时，请稍后重试。"
        )
        super().__init__(message)

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-safe error body for HTTP and SSE clients."""
        return {
            "code": "server_busy",
            "reason": self.reason,
            "message": str(self),
            "retryAfterSec": self.retry_after_sec,
            "concurrency": self.snapshot,
        }


class RunLease:
    """An idempotent lease for one admitted agent run."""

    def __init__(self, limiter: BoundedRunLimiter) -> None:
        self._limiter = limiter
        self._release_lock = threading.Lock()
        self._released = False

    def release(self) -> None:
        with self._release_lock:
            if self._released:
                return
            self._released = True
        self._limiter._release()

    def __enter__(self) -> RunLease:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.release()


class BoundedRunLimiter:
    """Bound active runs and queued waiters with a timeout.

    ``max_queued_runs=0`` means fail immediately once all active slots are
    occupied. A queued caller receives a lease as soon as a slot is released;
    otherwise it receives ``ConcurrencyLimitError`` after the configured wait
    timeout. The limiter does not guarantee FIFO ordering.
    """

    def __init__(
        self,
        *,
        max_concurrent_runs: int = 4,
        max_queued_runs: int = 16,
        queue_timeout_sec: float = 3.0,
    ) -> None:
        if max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs must be at least 1")
        if max_queued_runs < 0:
            raise ValueError("max_queued_runs must be non-negative")
        if queue_timeout_sec < 0:
            raise ValueError("queue_timeout_sec must be non-negative")
        self._limit = max_concurrent_runs
        self._max_queued = max_queued_runs
        self._queue_timeout = queue_timeout_sec
        self._retry_after = max(1, math.ceil(queue_timeout_sec))
        self._condition = threading.Condition()
        self._active = 0
        self._peak_active = 0
        self._queued = 0
        self._accepted = 0
        self._completed = 0
        self._rejected = 0
        self._timeouts = 0
        self._queue_wait_ms_total = 0.0
        self._queue_wait_samples = 0

    @classmethod
    def from_env(cls) -> BoundedRunLimiter:
        return cls(
            max_concurrent_runs=max_concurrent_runs_from_env(),
            max_queued_runs=max_queued_runs_from_env(),
            queue_timeout_sec=run_queue_timeout_sec_from_env(),
        )

    def acquire(self) -> RunLease:
        """Acquire a slot or raise ``ConcurrencyLimitError`` after waiting."""
        started_at = time.monotonic()
        deadline = started_at + self._queue_timeout
        with self._condition:
            if self._active < self._limit:
                self._active += 1
                self._peak_active = max(self._peak_active, self._active)
                self._accepted += 1
                self._queue_wait_samples += 1
                return RunLease(self)

            if self._queued >= self._max_queued:
                self._rejected += 1
                raise ConcurrencyLimitError(
                    "queue_full",
                    self._retry_after,
                    self._snapshot_locked(),
                )

            self._queued += 1
            try:
                while self._active >= self._limit:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._timeouts += 1
                        raise ConcurrencyLimitError(
                            "timeout",
                            self._retry_after,
                            self._snapshot_locked(),
                        )
                    self._condition.wait(remaining)

                self._active += 1
                self._peak_active = max(self._peak_active, self._active)
                self._accepted += 1
                self._queue_wait_ms_total += (time.monotonic() - started_at) * 1000
                self._queue_wait_samples += 1
                return RunLease(self)
            finally:
                self._queued -= 1

    def _release(self) -> None:
        with self._condition:
            if self._active > 0:
                self._active -= 1
            self._completed += 1
            self._condition.notify()

    def _snapshot_locked(self) -> dict[str, Any]:
        average_wait = (
            self._queue_wait_ms_total / self._queue_wait_samples
            if self._queue_wait_samples
            else 0.0
        )
        return {
            "limit": self._limit,
            "active": self._active,
            "peakActive": self._peak_active,
            "queued": self._queued,
            "maxQueued": self._max_queued,
            "queueTimeoutSec": self._queue_timeout,
            "retryAfterSec": self._retry_after,
            "accepted": self._accepted,
            "completed": self._completed,
            "rejected": self._rejected,
            "timeouts": self._timeouts,
            "avgQueueWaitMs": round(average_wait, 1),
        }

    def snapshot(self) -> dict[str, Any]:
        """Return a thread-safe point-in-time snapshot for health/debug."""
        with self._condition:
            return self._snapshot_locked()


__all__ = ["BoundedRunLimiter", "ConcurrencyLimitError", "RunLease"]
