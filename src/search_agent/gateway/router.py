"""OpenAI-compatible upstream routing and failover."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import GatewayConfig, UpstreamTarget

#: These failures are worth trying against the next deployment. 400/422 are
#: deliberately excluded: retrying an invalid request only multiplies cost.
RETRYABLE_STATUS = frozenset({401, 403, 404, 408, 409, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class GatewayCall:
    body: dict[str, Any]
    alias: str
    upstream: UpstreamTarget
    status_code: int
    attempts: tuple[dict[str, Any], ...]
    fallback_used: bool
    latency_ms: float


class GatewayError(Exception):
    """A request could not be completed by any configured upstream."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        code: str = "upstream_unavailable",
        attempts: tuple[dict[str, Any], ...] = (),
        upstream_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.attempts = attempts
        self.upstream_body = upstream_body


class GatewayRouter:
    """Send one Chat Completions request through an alias's route chain."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._client = httpx.AsyncClient(transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        *,
        alias: str,
        payload: dict[str, Any],
        request_id: str,
        tenant: str | None = None,
    ) -> GatewayCall:
        targets = self._config.model_aliases.get(alias)
        if not targets:
            raise GatewayError(
                f"unknown model alias: {alias}",
                status_code=400,
                code="unknown_model_alias",
            )

        started = time.perf_counter()
        attempts: list[dict[str, Any]] = []
        for index, target in enumerate(targets):
            attempt_started = time.perf_counter()
            headers = {
                "Content-Type": "application/json",
                "X-Request-Id": request_id,
                "X-Gateway-Alias": alias,
                **target.headers,
            }
            if tenant:
                headers["X-Tenant-Id"] = tenant
            api_key = target.resolve_api_key()
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            body = {**payload, **target.extra_body}
            body["model"] = target.model
            try:
                response = await self._client.post(
                    f"{target.base_url}/chat/completions",
                    json=body,
                    headers=headers,
                    timeout=target.timeout_sec,
                )
            except httpx.HTTPError as exc:
                attempts.append(
                    _attempt(
                        target,
                        index,
                        status_code=None,
                        elapsed_ms=(time.perf_counter() - attempt_started) * 1000,
                        error=type(exc).__name__,
                    )
                )
                continue

            attempt = _attempt(
                target,
                index,
                status_code=response.status_code,
                elapsed_ms=(time.perf_counter() - attempt_started) * 1000,
            )
            if 200 <= response.status_code < 300:
                try:
                    body_json = response.json()
                except ValueError as exc:
                    raise GatewayError(
                        "upstream returned a non-JSON response",
                        status_code=502,
                        code="invalid_upstream_response",
                        attempts=tuple(attempts + [attempt]),
                    ) from exc
                if not isinstance(body_json, dict):
                    raise GatewayError(
                        "upstream JSON response must be an object",
                        status_code=502,
                        code="invalid_upstream_response",
                        attempts=tuple(attempts + [attempt]),
                    )
                attempts.append(attempt)
                return GatewayCall(
                    body=body_json,
                    alias=alias,
                    upstream=target,
                    status_code=response.status_code,
                    attempts=tuple(attempts),
                    fallback_used=index > 0,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )

            attempts.append(attempt)
            if response.status_code not in RETRYABLE_STATUS:
                raise GatewayError(
                    f"upstream rejected the request with status {response.status_code}",
                    status_code=response.status_code,
                    code="upstream_client_error",
                    attempts=tuple(attempts),
                    upstream_body=_safe_json(response),
                )

        raise GatewayError(
            "all upstream targets failed",
            status_code=502,
            code="upstream_unavailable",
            attempts=tuple(attempts),
        )


def _attempt(
    target: UpstreamTarget,
    index: int,
    *,
    status_code: int | None,
    elapsed_ms: float,
    error: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "target": target.name,
        "model": target.model,
        "order": index,
        "statusCode": status_code,
        "latencyMs": round(elapsed_ms, 3),
    }
    if error:
        result["error"] = error
    return result


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:500]


__all__ = ["GatewayCall", "GatewayError", "GatewayRouter", "RETRYABLE_STATUS"]
