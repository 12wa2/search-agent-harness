"""Configuration for the optional OpenAI-compatible LLM gateway."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class UpstreamTarget(BaseModel):
    """One upstream deployment behind a gateway model alias."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key_env: str | None = None
    api_key: str | None = Field(
        default=None,
        description="Prefer api_key_env; literal keys are for local tests only.",
    )
    timeout_sec: float = Field(default=60.0, gt=0)
    headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError("base_url must not be empty")
        return normalized

    @field_validator("extra_body")
    @classmethod
    def _validate_extra_body(cls, value: dict[str, Any]) -> dict[str, Any]:
        reserved = {"model", "messages", "stream"} & value.keys()
        if reserved:
            names = ", ".join(sorted(reserved))
            raise ValueError(f"extra_body must not override protocol fields: {names}")
        return value

    def resolve_api_key(self) -> str | None:
        if self.api_key_env:
            value = os.environ.get(self.api_key_env)
            if value:
                return value
        return self.api_key


class GatewayConfig(BaseModel):
    """Alias-to-upstream mapping used by the gateway."""

    model_config = ConfigDict(extra="forbid")

    model_aliases: dict[str, list[UpstreamTarget]]
    default_model_alias: str | None = None
    #: Incoming bearer token is read from this env var. Unset means local/dev auth-off.
    gateway_api_key_env: str = "SEARCH_AGENT_GATEWAY_API_KEY"
    #: Expose aggregate usage at GET /v1/usage.
    expose_usage: bool = True

    @model_validator(mode="after")
    def _validate_aliases(self) -> "GatewayConfig":
        if not self.model_aliases:
            raise ValueError("model_aliases must contain at least one alias")
        for alias, targets in self.model_aliases.items():
            if not alias.strip():
                raise ValueError("model alias must not be empty")
            if not targets:
                raise ValueError(f"model alias {alias!r} has no upstream targets")
        if self.default_model_alias and self.default_model_alias not in self.model_aliases:
            raise ValueError("default_model_alias must be one of model_aliases")
        return self

    @classmethod
    def from_file(cls, path: str | Path) -> "GatewayConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("gateway config must be a JSON object")
        return cls.model_validate(raw)

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        path = os.environ.get("SEARCH_AGENT_GATEWAY_CONFIG")
        if not path:
            raise ValueError(
                "SEARCH_AGENT_GATEWAY_CONFIG is required (path to a gateway JSON config)"
            )
        return cls.from_file(path)

    def resolve_gateway_api_key(self) -> str | None:
        if not self.gateway_api_key_env:
            return None
        return os.environ.get(self.gateway_api_key_env)

    def public_summary(self) -> dict[str, object]:
        """Non-secret metadata suitable for health checks."""
        return {
            "defaultAlias": self.default_model_alias or next(iter(self.model_aliases), None),
            "modelAliases": {
                alias: [target.name for target in targets]
                for alias, targets in self.model_aliases.items()
            },
            "authRequired": bool(self.resolve_gateway_api_key()),
            "usageExposed": self.expose_usage,
        }


__all__ = ["GatewayConfig", "UpstreamTarget"]
