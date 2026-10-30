"""Tests for bounded agent-run admission control."""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from search_agent.config import (  # noqa: E402
    max_concurrent_runs_from_env,
    max_queued_runs_from_env,
    run_queue_timeout_sec_from_env,
)
from search_agent.core.concurrency import (  # noqa: E402
    BoundedRunLimiter,
    ConcurrencyLimitError,
)
from search_agent.demo import DEMO_QUERIES  # noqa: E402
from search_agent.server import create_app  # noqa: E402

QUERY = DEMO_QUERIES[0]


def test_limiter_rejects_when_active_slots_and_queue_are_full() -> None:
    limiter = BoundedRunLimiter(
        max_concurrent_runs=2,
        max_queued_runs=0,
        queue_timeout_sec=0,
    )
    first = limiter.acquire()
    second = limiter.acquire()

    with pytest.raises(ConcurrencyLimitError) as raised:
        limiter.acquire()

    assert raised.value.reason == "queue_full"
    assert raised.value.retry_after_sec == 1
    assert limiter.snapshot()["rejected"] == 1

    first.release()
    second.release()
    snapshot = limiter.snapshot()
    assert snapshot["active"] == 0
    assert snapshot["peakActive"] == 2
    assert snapshot["completed"] == 2


def test_limiter_times_out_waiting_for_an_active_slot() -> None:
    limiter = BoundedRunLimiter(
        max_concurrent_runs=1,
        max_queued_runs=1,
        queue_timeout_sec=0.05,
    )
    lease = limiter.acquire()
    started_at = time.monotonic()

    with pytest.raises(ConcurrencyLimitError) as raised:
        limiter.acquire()

    assert raised.value.reason == "timeout"
    assert time.monotonic() - started_at >= 0.04
    assert limiter.snapshot()["timeouts"] == 1
    lease.release()


def test_queued_lease_acquires_after_release() -> None:
    limiter = BoundedRunLimiter(
        max_concurrent_runs=1,
        max_queued_runs=1,
        queue_timeout_sec=0.5,
    )
    lease = limiter.acquire()
    timer = threading.Timer(0.05, lease.release)
    timer.start()
    try:
        next_lease = limiter.acquire()
    finally:
        timer.join()

    assert limiter.snapshot()["active"] == 1
    next_lease.release()
    snapshot = limiter.snapshot()
    assert snapshot["active"] == 0
    assert snapshot["completed"] == 2
    assert snapshot["avgQueueWaitMs"] >= 1


def test_search_rejects_with_429_and_releases_runtime_lock() -> None:
    limiter = BoundedRunLimiter(
        max_concurrent_runs=1,
        max_queued_runs=0,
        queue_timeout_sec=0,
    )
    held = limiter.acquire()
    app = create_app(offline=True, run_limiter=limiter)

    with TestClient(app) as client:
        response = client.post("/search", json={"query": QUERY, "topK": 5})

        assert response.status_code == 429
        assert response.headers["retry-after"] == "1"
        detail = response.json()["detail"]
        assert detail["code"] == "server_busy"
        assert detail["reason"] == "queue_full"
        assert detail["concurrency"]["active"] == 1
        assert app.state.runtime_llm.active_runs == 0

    held.release()


def test_stream_rejects_with_429_when_queue_is_full() -> None:
    limiter = BoundedRunLimiter(
        max_concurrent_runs=1,
        max_queued_runs=0,
        queue_timeout_sec=0,
    )
    held = limiter.acquire()
    app = create_app(offline=True, run_limiter=limiter)

    with TestClient(app) as client:
        response = client.post("/search/stream", json={"query": QUERY, "topK": 5})

        assert response.status_code == 429
        assert response.headers["retry-after"] == "1"
        assert response.json()["detail"]["code"] == "server_busy"
        assert app.state.runtime_llm.active_runs == 0

    held.release()


def test_successful_stream_releases_slot_and_stays_streamable() -> None:
    limiter = BoundedRunLimiter(
        max_concurrent_runs=1,
        max_queued_runs=1,
        queue_timeout_sec=1,
    )
    app = create_app(offline=True, run_limiter=limiter)

    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/search/stream",
            json={"query": QUERY, "topK": 5, "details": True},
        ) as response:
            assert response.status_code == 200
            text = "".join(response.iter_text())

        assert "event: result" in text
        assert limiter.snapshot()["active"] == 0
        assert limiter.snapshot()["completed"] == 1
        assert app.state.runtime_llm.active_runs == 0


def test_healthz_exposes_concurrency_snapshot() -> None:
    limiter = BoundedRunLimiter(
        max_concurrent_runs=3,
        max_queued_runs=2,
        queue_timeout_sec=1.5,
    )
    app = create_app(offline=True, run_limiter=limiter)

    with TestClient(app) as client:
        health = client.get("/healthz").json()

    assert health["concurrency"]["limit"] == 3
    assert health["concurrency"]["maxQueued"] == 2
    assert health["concurrency"]["queueTimeoutSec"] == 1.5


def test_concurrency_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_AGENT_MAX_CONCURRENT_RUNS", "7")
    monkeypatch.setenv("SEARCH_AGENT_MAX_QUEUED_RUNS", "9")
    monkeypatch.setenv("SEARCH_AGENT_RUN_QUEUE_TIMEOUT_SEC", "2.5")

    assert max_concurrent_runs_from_env() == 7
    assert max_queued_runs_from_env() == 9
    assert run_queue_timeout_sec_from_env() == 2.5
