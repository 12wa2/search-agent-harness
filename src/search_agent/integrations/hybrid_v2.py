"""Adapter for the bundled Hybrid V2 ``SearchTool`` implementation.

Hybrid V2 now lives in ``search_agent.hybrid_v2`` and uses the same public
contract as the other backends. No host-service modules are discovered at
runtime, and no out-of-process endpoint is required.
"""

from __future__ import annotations

from typing import Any

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    SearchToolResult,
)

__all__ = ["HybridV2ToolAdapter", "build_hybrid_v2_tool"]


class HybridV2ToolAdapter:
    """Compatibility adapter for callers that inject their own service."""

    def __init__(
        self,
        *,
        service: Any,
        request_model: type[Any] | None = None,
        rerank_request_model: type[Any] | None = None,
    ) -> None:
        self._service = service
        self._request_model = request_model or SearchToolRequest
        self._rerank_request_model = rerank_request_model or RerankToolRequest

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult:
        service_req = self._request_model.model_validate(req.model_dump())
        service_result = self._service.search_with_intent(service_req)
        return SearchToolResult.model_validate(service_result.model_dump())

    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult:
        service_req = self._rerank_request_model.model_validate(req.model_dump())
        service_result = self._service.rerank_pool(service_req)
        return SearchToolResult.model_validate(service_result.model_dump())


def build_hybrid_v2_tool(
    *,
    no_graph: bool | None = None,
    service: Any | None = None,
    config: Any | None = None,
) -> Any:
    """Build the standalone engine; ``no_graph`` is accepted for compatibility."""

    del no_graph
    if service is not None:
        return service
    from search_agent.hybrid_v2 import HybridV2Config, HybridV2SearchService

    return HybridV2SearchService(config=config or HybridV2Config.from_env())
