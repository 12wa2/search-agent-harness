"""In-process token and request accounting for the gateway.

This is intentionally process-local and dependency-free. A production
deployment should persist these counters in Redis or a database if multiple
gateway replicas must share quotas.
"""

from __future__ import annotations

from threading import Lock
from typing import Any


def extract_usage(body: dict[str, Any]) -> tuple[int, int, int, bool]:
    """Return (prompt, completion, total, usage_missing)."""
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return 0, 0, 0, True
    prompt = _as_int(usage.get("prompt_tokens"))
    completion = _as_int(usage.get("completion_tokens"))
    total = _as_int(usage.get("total_tokens"))
    if total <= 0:
        total = prompt + completion
    return prompt, completion, total, False


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


class UsageLedger:
    """Thread-safe aggregate counters for requests, tokens and fallbacks."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._totals: dict[str, int] = {
            "requests": 0,
            "successful": 0,
            "failed": 0,
            "fallback": 0,
            "usageMissing": 0,
            "promptTokens": 0,
            "completionTokens": 0,
            "totalTokens": 0,
        }
        self._by_alias: dict[str, dict[str, int]] = {}
        self._by_model: dict[str, dict[str, int]] = {}
        self._by_tenant: dict[str, dict[str, int]] = {}
        self._last: dict[str, Any] | None = None

    def record_success(
        self,
        *,
        alias: str,
        upstream_model: str,
        tenant: str | None,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        usage_missing: bool,
        fallback_used: bool,
        attempts: int,
        latency_ms: float,
        request_id: str,
    ) -> None:
        with self._lock:
            self._bump(
                self._totals,
                requests=1,
                successful=1,
                fallback=int(fallback_used),
                usageMissing=int(usage_missing),
                promptTokens=prompt_tokens,
                completionTokens=completion_tokens,
                totalTokens=total_tokens,
            )
            self._bump(
                self._bucket(self._by_alias, alias),
                requests=1,
                promptTokens=prompt_tokens,
                completionTokens=completion_tokens,
                totalTokens=total_tokens,
                fallback=int(fallback_used),
            )
            self._bump(
                self._bucket(self._by_model, upstream_model),
                requests=1,
                promptTokens=prompt_tokens,
                completionTokens=completion_tokens,
                totalTokens=total_tokens,
            )
            if tenant:
                self._bump(
                    self._bucket(self._by_tenant, tenant),
                    requests=1,
                    promptTokens=prompt_tokens,
                    completionTokens=completion_tokens,
                    totalTokens=total_tokens,
                )
            self._last = {
                "requestId": request_id,
                "alias": alias,
                "upstreamModel": upstream_model,
                "tenant": tenant,
                "promptTokens": prompt_tokens,
                "completionTokens": completion_tokens,
                "totalTokens": total_tokens,
                "fallbackUsed": fallback_used,
                "attempts": attempts,
                "latencyMs": round(latency_ms, 3),
                "status": "ok",
            }

    def record_failure(
        self,
        *,
        alias: str,
        tenant: str | None,
        attempts: int,
        latency_ms: float,
        request_id: str,
        status_code: int,
    ) -> None:
        with self._lock:
            self._bump(self._totals, requests=1, failed=1)
            self._bump(self._bucket(self._by_alias, alias), requests=1, failed=1)
            if tenant:
                self._bump(self._bucket(self._by_tenant, tenant), requests=1, failed=1)
            self._last = {
                "requestId": request_id,
                "alias": alias,
                "tenant": tenant,
                "attempts": attempts,
                "latencyMs": round(latency_ms, 3),
                "status": "error",
                "statusCode": status_code,
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "totals": dict(self._totals),
                "byAlias": self._copy_buckets(self._by_alias),
                "byModel": self._copy_buckets(self._by_model),
                "byTenant": self._copy_buckets(self._by_tenant),
                "last": dict(self._last) if self._last else None,
            }

    @staticmethod
    def _bucket(table: dict[str, dict[str, int]], key: str) -> dict[str, int]:
        return table.setdefault(key, {})

    @staticmethod
    def _bump(bucket: dict[str, int], **values: int) -> None:
        for key, value in values.items():
            bucket[key] = bucket.get(key, 0) + value

    @staticmethod
    def _copy_buckets(table: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
        return {key: dict(value) for key, value in table.items()}


__all__ = ["UsageLedger", "extract_usage"]
