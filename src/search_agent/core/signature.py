"""Intent signature for dedup / anti-retry.

signature = sha1(canonical_json(normalize_where, normalize_semantic, meta))
Drops weight / softText / required (don't change the recall intent). Two paths
with the same signature are the same recall and must not be re-tried.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from search_agent.models.ir import (
    CandidateSearchIR,
    SemanticConditionIR,
    WhereNode,
)


def _normalize_values(values: list[Any]) -> list[Any]:
    out: list[Any] = []
    for v in values:
        if isinstance(v, str):
            s = v.strip().casefold()
            if s:
                out.append(s)
        else:
            out.append(v)
    return sorted(out, key=lambda x: str(x))


def normalize_where(node: WhereNode) -> dict[str, Any]:
    """Canonical form of a boolean tree; order-independent for and/or children."""
    t = node.type
    if t == "leaf" and node.condition is not None:
        c = node.condition
        return {
            "type": "leaf",
            "field": c.field,
            "operator": c.operator,
            "values": _normalize_values(list(c.values)),
        }
    if t == "not" and node.child is not None:
        return {"type": "not", "child": normalize_where(node.child)}
    # and / or
    children = [normalize_where(ch) for ch in node.children]
    children.sort(key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))
    node_d: dict[str, Any] = {"type": t, "children": children}
    if t == "or" and node.minimumShouldMatch is not None:
        node_d["minShouldMatch"] = node.minimumShouldMatch
    return node_d


def normalize_semantic(cond: SemanticConditionIR) -> dict[str, Any]:
    return {"text": cond.text.strip().casefold(), "fields": sorted(cond.fields)}


def intent_signature(intent: CandidateSearchIR) -> str:
    payload = {
        "where": normalize_where(intent.where),
        "semantic": sorted(
            (normalize_semantic(s) for s in intent.semantic),
            key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True),
        ),
        "meta": {
            "expandScope": intent.meta.expandScope,
            "peerHint": intent.meta.peerHint,
            "tenureIntent": intent.meta.tenureIntent,
        },
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


__all__ = ["intent_signature", "normalize_semantic", "normalize_where"]
