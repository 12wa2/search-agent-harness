"""Tests for the optional OpenAI-compatible LLM gateway.

The upstream is an in-process ``httpx.MockTransport``, so these tests cover
routing, failover, auth and usage accounting without network access.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from search_agent.gateway.app import create_gateway_app  # noqa: E402
from search_agent.gateway.config import GatewayConfig, UpstreamTarget  # noqa: E402

API_KEY = "gateway-test-key"


def _config() -> GatewayConfig:
    return GatewayConfig(
        gateway_api_key_env="TEST_GATEWAY_KEY",
        model_aliases={
            "search-agent-primary": [
                UpstreamTarget(
                    name="primary",
                    base_url="https://primary.test/v1",
                    model="model-primary",
                    api_key="upstream-primary-key",
                ),
                UpstreamTarget(
                    name="fallback",
                    base_url="https://fallback.test/v1",
                    model="model-fallback",
                    api_key="upstream-fallback-key",
                ),
            ]
        },
    )


def _body(model: str = "search-agent-primary") -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "demo", "strict": True, "schema": {"type": "object"}},
        },
    }


def _success(model: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "{}"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    }


def _client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> TestClient:
    monkeypatch.setenv("TEST_GATEWAY_KEY", API_KEY)
    app = create_gateway_app(_config(), transport=httpx.MockTransport(handler))
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}", "X-Tenant-Id": "tenant-a"}


def test_healthz_and_models_do_not_expose_secrets(monkeypatch):
    with _client(monkeypatch, lambda request: httpx.Response(200, json={})) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["authRequired"] is True
        assert "primary" in health.json()["modelAliases"]["search-agent-primary"]
        assert "upstream-primary-key" not in health.text

        models = client.get("/v1/models")
        assert models.status_code == 200
        assert models.json()["data"][0]["id"] == "search-agent-primary"


def test_requires_gateway_bearer_token(monkeypatch):
    with _client(monkeypatch, lambda request: httpx.Response(200, json={})) as client:
        response = client.post("/v1/chat/completions", json=_body())
        assert response.status_code == 401
        assert response.json()["detail"] == "missing gateway bearer token"


def test_routes_alias_to_primary_and_records_usage(monkeypatch):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_success("model-primary"))

    with _client(monkeypatch, handler) as client:
        response = client.post(
            "/v1/chat/completions",
            json=_body(),
            headers=_headers(),
        )
        assert response.status_code == 200
        assert response.headers["x-gateway-upstream-model"] == "model-primary"
        assert response.headers["x-gateway-fallback"] == "false"
        assert response.headers["x-gateway-attempts"] == "1"
        assert json.loads(calls[0].content)["model"] == "model-primary"
        assert calls[0].headers["authorization"] == "Bearer upstream-primary-key"
        assert calls[0].headers["x-request-id"] == response.headers["x-request-id"]

        usage = client.get("/v1/usage", headers=_headers())
        assert usage.status_code == 200
        snapshot = usage.json()
        assert snapshot["totals"]["requests"] == 1
        assert snapshot["totals"]["totalTokens"] == 15
        assert snapshot["byTenant"]["tenant-a"]["totalTokens"] == 15
        assert snapshot["last"]["upstreamModel"] == "model-primary"


def test_upstream_extra_body_is_merged_for_the_selected_target(monkeypatch):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_success("model-primary"))

    monkeypatch.setenv("TEST_GATEWAY_KEY", API_KEY)
    config = GatewayConfig(
        gateway_api_key_env="TEST_GATEWAY_KEY",
        model_aliases={
            "search-agent-primary": [
                UpstreamTarget(
                    name="primary",
                    base_url="https://primary.test/v1",
                    model="model-primary",
                    api_key="upstream-primary-key",
                    extra_body={"enable_thinking": False, "temperature": 0.1},
                )
            ]
        },
    )
    app = create_gateway_app(config, transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json=_body(),
            headers=_headers(),
        )
        assert response.status_code == 200

    body = json.loads(calls[0].content)
    assert body["enable_thinking"] is False
    assert body["temperature"] == 0.1
    assert body["model"] == "model-primary"


def test_extra_body_cannot_override_protocol_fields():
    with pytest.raises(ValueError):
        UpstreamTarget(
            name="primary",
            base_url="https://primary.test/v1",
            model="model-primary",
            extra_body={"messages": []},
        )


def test_retryable_primary_failure_uses_fallback(monkeypatch):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "primary.test":
            return httpx.Response(503, json={"error": {"message": "down"}})
        return httpx.Response(200, json=_success("model-fallback"))

    with _client(monkeypatch, handler) as client:
        response = client.post(
            "/v1/chat/completions",
            json=_body(),
            headers=_headers(),
        )
        assert response.status_code == 200
        assert [request.url.host for request in calls] == ["primary.test", "fallback.test"]
        assert response.headers["x-gateway-upstream-model"] == "model-fallback"
        assert response.headers["x-gateway-fallback"] == "true"
        assert response.headers["x-gateway-attempts"] == "2"

        snapshot = client.get("/v1/usage", headers=_headers()).json()
        assert snapshot["totals"]["fallback"] == 1
        assert snapshot["byAlias"]["search-agent-primary"]["fallback"] == 1


def test_non_retryable_400_does_not_overflow_to_fallback(monkeypatch):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    with _client(monkeypatch, handler) as client:
        response = client.post(
            "/v1/chat/completions",
            json=_body(),
            headers=_headers(),
        )
        assert response.status_code == 400
        assert len(calls) == 1
        assert response.json()["error"]["code"] == "upstream_client_error"


def test_unknown_alias_is_rejected(monkeypatch):
    with _client(monkeypatch, lambda request: httpx.Response(200, json={})) as client:
        response = client.post(
            "/v1/chat/completions",
            json=_body("not-configured"),
            headers=_headers(),
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "unknown_model_alias"


def test_streaming_request_is_rejected(monkeypatch):
    with _client(monkeypatch, lambda request: httpx.Response(200, json={})) as client:
        body = _body()
        body["stream"] = True
        response = client.post("/v1/chat/completions", json=body, headers=_headers())
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "streaming_not_supported"
