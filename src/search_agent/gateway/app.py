"""FastAPI app exposing an OpenAI-compatible gateway endpoint."""

from __future__ import annotations

import hmac
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from search_agent import __version__

from .config import GatewayConfig
from .router import GatewayError, GatewayRouter
from .usage import UsageLedger, extract_usage

logger = logging.getLogger(__name__)


def _error_response(
    *,
    status_code: int,
    message: str,
    code: str,
    request_id: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": "gateway_error",
                "code": code,
                "param": None,
            },
            "requestId": request_id,
        },
    )


def _authorize(config: GatewayConfig, authorization: str | None) -> None:
    expected = config.resolve_gateway_api_key()
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing gateway bearer token")
    supplied = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid gateway bearer token")


def create_gateway_app(
    config: GatewayConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    ledger: UsageLedger | None = None,
) -> FastAPI:
    """Build the gateway app with an injectable upstream transport for tests."""
    router = GatewayRouter(config, transport=transport)
    usage = ledger or UsageLedger()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await router.aclose()

    app = FastAPI(
        title="search-agent-llm-gateway",
        version=__version__,
        description="Thin OpenAI-compatible model router with fallback and usage accounting.",
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.router = router
    app.state.usage = usage

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "search-agent-llm-gateway",
            "version": __version__,
            **config.public_summary(),
        }

    @app.get("/v1/models")
    def models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": alias,
                    "object": "model",
                    "owned_by": "search-agent-llm-gateway",
                }
                for alias in config.model_aliases
            ],
        }

    @app.get("/v1/usage")
    def usage_snapshot(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _authorize(config, authorization)
        if not config.expose_usage:
            raise HTTPException(status_code=404, detail="usage endpoint is disabled")
        return usage.snapshot()

    @app.post("/v1/chat/completions")
    async def chat_completions(
        payload: Annotated[dict[str, Any], Body(...)],
        authorization: str | None = Header(default=None),
        x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    ) -> JSONResponse:
        _authorize(config, authorization)
        request_id = x_request_id or uuid.uuid4().hex
        alias = str(
            payload.get("model") or config.default_model_alias or next(iter(config.model_aliases))
        )
        if payload.get("stream"):
            return _error_response(
                status_code=400,
                message="streaming is not supported by this gateway; send stream=false",
                code="streaming_not_supported",
                request_id=request_id,
            )

        started = time.perf_counter()
        try:
            result = await router.complete(
                alias=alias,
                payload=payload,
                request_id=request_id,
                tenant=x_tenant_id,
            )
        except GatewayError as exc:
            usage.record_failure(
                alias=alias,
                tenant=x_tenant_id,
                attempts=len(exc.attempts),
                latency_ms=(time.perf_counter() - started) * 1000,
                request_id=request_id,
                status_code=exc.status_code,
            )
            logger.warning(
                "gateway request failed request_id=%s alias=%s status=%s attempts=%s",
                request_id,
                alias,
                exc.status_code,
                len(exc.attempts),
            )
            return _error_response(
                status_code=exc.status_code,
                message=str(exc),
                code=exc.code,
                request_id=request_id,
            )

        prompt_tokens, completion_tokens, total_tokens, usage_missing = extract_usage(result.body)
        usage.record_success(
            alias=alias,
            upstream_model=result.upstream.model,
            tenant=x_tenant_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            usage_missing=usage_missing,
            fallback_used=result.fallback_used,
            attempts=len(result.attempts),
            latency_ms=result.latency_ms,
            request_id=request_id,
        )
        logger.info(
            "gateway request ok request_id=%s alias=%s model=%s fallback=%s "
            "tokens=%s latency_ms=%.1f",
            request_id,
            alias,
            result.upstream.model,
            result.fallback_used,
            total_tokens,
            result.latency_ms,
        )
        return JSONResponse(
            content=result.body,
            headers={
                "X-Request-Id": request_id,
                "X-Gateway-Alias": alias,
                "X-Gateway-Upstream-Model": result.upstream.model,
                "X-Gateway-Fallback": str(result.fallback_used).lower(),
                "X-Gateway-Attempts": str(len(result.attempts)),
            },
        )

    return app


__all__ = ["create_gateway_app"]
