"""Optional adapter for a private Hybrid V2 implementation of ``SearchTool``.

The private service is intentionally not bundled with the public package. When
it is installed in the same environment, the CLI can select it with
``--backend hybrid_v2``. The agent only sees the public contract; all private
module names stay inside this adapter.
"""

from __future__ import annotations

import importlib
import os
from typing import Any, Callable

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    SearchToolResult,
)

__all__ = ["HybridV2ToolAdapter", "build_hybrid_v2_tool"]

_NO_GRAPH_ENV = "SEARCH_AGENT_HYBRID_V2_NO_GRAPH"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


class _IdentityExpander:
    """Keep hard-condition terms unchanged when the graph is disabled."""

    def expand(self, term: str) -> list[str]:
        text = str(term).strip()
        return [text] if text else []


class _OnetOnlyPositionExpander:
    """Use the bundled O*NET alias file without falling back to a graph DB."""

    def __init__(self, expand_onet: Callable[[str], list[str] | None]) -> None:
        self._expand_onet = expand_onet

    def expand(self, term: str) -> list[str]:
        text = str(term).strip()
        if not text:
            return []
        expanded = self._expand_onet(text)
        return list(expanded) if expanded else [text]


class HybridV2ToolAdapter:
    """Translate the public contract to an injected private service."""

    def __init__(
        self,
        *,
        service: Any,
        request_model: type[Any],
        rerank_request_model: type[Any],
    ) -> None:
        self._service = service
        self._request_model = request_model
        self._rerank_request_model = rerank_request_model

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult:
        internal_req = self._request_model.model_validate(req.model_dump())
        internal_result = self._service.search_with_intent(internal_req)
        return SearchToolResult.model_validate(internal_result.model_dump())

    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult:
        internal_req = self._rerank_request_model.model_validate(req.model_dump())
        internal_result = self._service.rerank_pool(internal_req)
        return SearchToolResult.model_validate(internal_result.model_dump())


def _load_private_backend() -> tuple[
    type[Any],
    type[Any],
    type[Any],
    type[Any],
    Callable[[str], list[str] | None],
    Any,
]:
    try:
        service_module = importlib.import_module("app.services.candidate_search.hybrid_v2.service")
        contract_module = importlib.import_module(
            "app.services.candidate_search.search_agent.contract.tool"
        )
        expander_module = importlib.import_module("app.services.graph_expander.term_expander")
        onet_module = importlib.import_module("app.services.graph_expander.onet_position_aliases")
        config_module = importlib.import_module("app.core.config")
    except ImportError as exc:
        raise RuntimeError(
            "hybrid_v2 backend requires the private search service to be importable "
            "in this Python environment. Install that package locally or set "
            "PYTHONPATH; the public demo remains available with --backend memory."
        ) from exc
    return (
        service_module.HybridSearchV2Service,
        contract_module.SearchToolRequest,
        contract_module.RerankToolRequest,
        expander_module.CompositeTermExpander,
        onet_module.expand_onet_position_aliases,
        config_module.settings,
    )


def build_hybrid_v2_tool(*, no_graph: bool | None = None) -> HybridV2ToolAdapter:
    """Build the optional adapter; graph expansion is disabled by default."""
    disable_graph = _env_flag(_NO_GRAPH_ENV, True) if no_graph is None else no_graph
    if disable_graph:
        os.environ.setdefault("SEARCH_GRAPH_EXPAND_ENABLED", "false")
        os.environ.setdefault("SEARCH_GRAPH_EXPAND_PEERS_ENABLED", "false")

    (
        service_cls,
        request_cls,
        rerank_request_cls,
        expander_cls,
        expand_onet,
        settings,
    ) = _load_private_backend()

    if disable_graph:
        for name in ("search_graph_expand_enabled", "search_graph_expand_peers_enabled"):
            if hasattr(settings, name):
                setattr(settings, name, False)
        expander = expander_cls(
            skill=_IdentityExpander(),
            company=_IdentityExpander(),
            position=_OnetOnlyPositionExpander(expand_onet),
            college=_IdentityExpander(),
            cert_exam=_IdentityExpander(),
            enabled=True,
        )
        service = service_cls(term_expander=expander)
    else:
        service = service_cls()

    return HybridV2ToolAdapter(
        service=service,
        request_model=request_cls,
        rerank_request_model=rerank_request_cls,
    )
