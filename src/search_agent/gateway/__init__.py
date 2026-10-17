"""Thin OpenAI-compatible LLM gateway.

The gateway owns provider routing, failover, request metadata and usage
accounting. The agent keeps its own per-request budget and reasoning-level
degradation policy.

Keep this package initializer free of FastAPI imports: the gateway is an
optional extra and ``search_agent`` itself must remain importable without it.
"""

from __future__ import annotations

__all__: list[str] = []
