"""Normalize exact O*NET/SOC occupation queries into stable position IR."""

from __future__ import annotations

from .onet import (
    OnetPositionAliasMatch,
    resolve_onet_position_alias,
)
from .schemas import (
    CandidateSearchIR,
    SemanticConditionIR,
    StructuredConditionIR,
    WhereNode,
)

NOTE_PREFIX = "onet_position_normalized:"


def normalize_onet_position_intent(
    ir: CandidateSearchIR,
    query_text: str,
) -> CandidateSearchIR:
    """Override unstable parser position output for exact O*NET/SOC titles.

    The raw query is the source of truth. When it exactly matches a curated
    O*NET/SOC code/title, replace parser-generated position hard and work-vector
    soft text with controlled recruiting aliases. Non-O*NET queries are returned
    unchanged.
    """
    match = resolve_onet_position_alias(query_text, strict=True)
    if match is None:
        return ir

    replacement = _position_leaf(match)
    where, replaced = _replace_position_leaves(ir.where, replacement)
    if not replaced:
        where = WhereNode(type="and", children=[ir.where, replacement])

    semantic = [
        cond for cond in list(ir.semantic or []) if "work_vector" not in list(cond.fields or [])
    ]
    semantic.append(_stable_position_semantic(match, query_text))

    notes = list(ir.meta.notes or [])
    note = f"{NOTE_PREFIX}{match.code}"
    if note not in notes:
        notes.append(note)

    meta = ir.meta.model_copy(
        update={
            "needClarification": False,
            "clarifyQuestions": [],
            "notes": notes,
        }
    )
    return ir.model_copy(update={"where": where, "semantic": semantic, "meta": meta})


def has_onet_position_normalized(meta_notes: list[str] | None) -> bool:
    return any(str(note).startswith(NOTE_PREFIX) for note in list(meta_notes or []))


def _position_leaf(match: OnetPositionAliasMatch) -> WhereNode:
    return WhereNode(
        type="leaf",
        condition=StructuredConditionIR(
            field="position",
            operator="any",
            values=list(match.terms),
            required=True,
            weight=1.0,
        ),
    )


def _stable_position_semantic(
    match: OnetPositionAliasMatch,
    query_text: str,
) -> SemanticConditionIR:
    text_parts = [
        query_text.strip(),
        match.title_en,
        match.title_zh,
        *list(match.terms),
    ]
    text = " ".join(_dedup_keep_order([part for part in text_parts if part]))
    return SemanticConditionIR(text=text, fields=["work_vector"], weight=1.0)


def _replace_position_leaves(
    node: WhereNode,
    replacement: WhereNode,
) -> tuple[WhereNode, bool]:
    if node.type == "leaf":
        cond = node.condition
        if cond is not None and cond.field in {"position", "intention_position"}:
            return replacement, True
        return node, False

    if node.type == "not":
        return node, False

    if node.type in {"and", "or"}:
        changed = False
        children: list[WhereNode] = []
        for child in node.children:
            rewritten, child_changed = _replace_position_leaves(child, replacement)
            children.append(rewritten)
            changed = changed or child_changed
        if not changed:
            return node, False
        return node.model_copy(update={"children": children}), True

    return node, False


def _dedup_keep_order(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        text = str(term).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


__all__ = [
    "NOTE_PREFIX",
    "has_onet_position_normalized",
    "normalize_onet_position_intent",
]
