"""Standalone Hybrid V2 retrieval engine.

The package is deliberately self-contained: it speaks to a user-provided
Elasticsearch 8 cluster, reads credentials only from environment variables, and
contains no host-specific infrastructure adapters.
"""

from .config import HybridV2Config, hybrid_v2_configured
from .indexing import HybridV2Indexer
from .parser import IrParseResult, parse_query_to_ir
from .service import HybridV2SearchService

__all__ = [
    "HybridV2Config",
    "HybridV2Indexer",
    "HybridV2SearchService",
    "IrParseResult",
    "hybrid_v2_configured",
    "parse_query_to_ir",
]
