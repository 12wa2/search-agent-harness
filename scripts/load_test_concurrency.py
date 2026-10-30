"""Small reproducible HTTP load probe for the search-agent server.

The script uses only the Python standard library so it can run in the quick
demo environment without extra dependencies. It samples ``/healthz`` while
requests are in flight to record the observed peak number of active runs.

Example::

    python scripts/load_test_concurrency.py \
        --url http://127.0.0.1:8000 \
        --requests 100 \
        --concurrency 25 \
        --json-out concurrency.json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_QUERY = "帮我找 5 年以上 Python 后端工程师，上海优先"


def _request_json(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int | None, dict[str, Any] | None, str | None]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=headers, method="POST" if data else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            try:
                parsed = json.loads(body.decode("utf-8")) if body else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed = None
            return response.status, parsed, None
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            parsed = json.loads(body.decode("utf-8")) if body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        return exc.code, parsed, None
    except Exception as exc:  # noqa: BLE001 - report network failures as data
        return None, None, str(exc)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _health(base_url: str, timeout: float) -> dict[str, Any] | None:
    status, payload, _error = _request_json(f"{base_url}/healthz", timeout=timeout)
    return payload if status == 200 and isinstance(payload, dict) else None


def _run_one(base_url: str, query: str, top_k: int, timeout: float) -> dict[str, Any]:
    started = time.perf_counter()
    status, payload, error = _request_json(
        f"{base_url}/search",
        payload={"query": query, "topK": top_k},
        timeout=timeout,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {"status": status, "payload": payload, "error": error, "elapsedMs": elapsed_ms}


def _sample_health(
    base_url: str,
    *,
    stop: threading.Event,
    interval_sec: float,
    timeout: float,
    observed: dict[str, int | None],
    lock: threading.Lock,
) -> None:
    while not stop.wait(interval_sec):
        health = _health(base_url, timeout)
        concurrency = health.get("concurrency") if health else None
        if not isinstance(concurrency, dict):
            continue
        active = int(concurrency.get("active") or 0)
        queued = int(concurrency.get("queued") or 0)
        peak_active = int(concurrency.get("peakActive") or 0)
        with lock:
            observed["maxActive"] = max(int(observed["maxActive"] or 0), active)
            observed["maxQueued"] = max(int(observed["maxQueued"] or 0), queued)
            observed["peakActive"] = max(int(observed["peakActive"] or 0), peak_active)


def run(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.url.rstrip("/")
    before = _health(base_url, args.timeout)
    if before is None:
        raise SystemExit(f"health check failed: {base_url}/healthz")

    for _ in range(args.warmup):
        _run_one(base_url, args.query, args.top_k, args.timeout)

    observed: dict[str, int | None] = {
        "maxActive": 0,
        "maxQueued": 0,
        "peakActive": 0,
    }
    observed_lock = threading.Lock()
    stop = threading.Event()
    sampler = threading.Thread(
        target=_sample_health,
        kwargs={
            "base_url": base_url,
            "stop": stop,
            "interval_sec": args.health_interval,
            "timeout": args.timeout,
            "observed": observed,
            "lock": observed_lock,
        },
        daemon=True,
    )
    sampler.start()

    started_at = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(
                pool.map(
                    lambda _index: _run_one(base_url, args.query, args.top_k, args.timeout),
                    range(args.requests),
                )
            )
    finally:
        stop.set()
        sampler.join(timeout=args.timeout)
    duration_sec = time.perf_counter() - started_at

    successes = [item for item in results if item["status"] == 200]
    busy = [item for item in results if item["status"] == 429]
    failures = [item for item in results if item["status"] is None]
    latencies = [float(item["elapsedMs"]) for item in successes]
    after = _health(base_url, args.timeout)
    after_concurrency = (after or {}).get("concurrency") or {}
    before_concurrency = (before or {}).get("concurrency") or {}

    return {
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "baseUrl": base_url,
        "query": args.query,
        "topK": args.top_k,
        "requests": args.requests,
        "clientConcurrency": args.concurrency,
        "timeoutSec": args.timeout,
        "durationSec": round(duration_sec, 3),
        "rps": round(args.requests / duration_sec, 2) if duration_sec else None,
        "successfulRps": round(len(successes) / duration_sec, 2) if duration_sec else None,
        "counts": {
            "success": len(successes),
            "http429": len(busy),
            "networkErrors": len(failures),
            "otherHttpErrors": args.requests - len(successes) - len(busy) - len(failures),
        },
        "latencyMs": {
            "min": round(min(latencies), 1) if latencies else None,
            "mean": round(statistics.fmean(latencies), 1) if latencies else None,
            "p50": round(_percentile(latencies, 0.50), 1) if latencies else None,
            "p95": round(_percentile(latencies, 0.95), 1) if latencies else None,
            "p99": round(_percentile(latencies, 0.99), 1) if latencies else None,
            "max": round(max(latencies), 1) if latencies else None,
        },
        "observed": {
            "maxActive": int(observed["maxActive"] or 0),
            "maxQueued": int(observed["maxQueued"] or 0),
            "peakActive": int(observed["peakActive"] or 0),
        },
        "healthBefore": before_concurrency,
        "healthAfter": after_concurrency,
        "errors": failures[:5],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--health-interval", type=float, default=0.05)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error("requests and concurrency must be at least 1")
    result = run(args)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
