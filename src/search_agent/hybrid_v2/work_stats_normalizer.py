"""Normalize work_stats-related query cues into structured IR leaves."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .schemas import (
    CandidateSearchIR,
    SemanticConditionIR,
    StructuredConditionIR,
    StructuredField,
    WhereNode,
)
from .work_stats_config import (
    AVG_TENURE_LONG_MIN_MONTHS,
    MAX_TENURE_STRONG_MIN_MONTHS,
)
from .work_stats_ir_validator import validate_work_stats_where
from .work_stats_scope_literals import (
    query_has_latest_company_scope,
    query_has_latest_job_scope,
)
from .work_tenure_intent import (
    WorkTenureTarget,
    classify_work_tenure_intent,
    extract_work_tenure_target,
    parse_tenure_role_phrase,
)

_SEGMENT_COUNT_IN_QUERY_RE = re.compile(
    r"(?:工作经历|工作履历|任职经历).{0,8}(?:超过|多于|至少|≥|>=?)\s*(\d+)\s*段|"
    r"(\d+)\s*段(?:以上)?(?:工作|任职)?经历|"
    r"(?:超过|多于|至少)\s*(\d+)\s*段"
)
_GAP_OVER_YEARS_RE = re.compile(
    r"(?:工作)?空窗(?:期|时间|阶段)?.{0,6}(?:超过|大于|≥|>=?)\s*(\d+)\s*年|"
    r"空窗(?:期|时间|阶段)?.{0,6}(?:超过|大于|≥|>=?)\s*(两|二)\s*年"
)
_GAP_OVER_MONTHS_RE = re.compile(
    r"(?:工作)?空窗(?:期|时间|阶段)?.{0,6}(?:超过|大于|≥|>=?)\s*(\d+)\s*(?:个)?月"
)
_GAP_ABOUT_YEAR_RE = re.compile(
    r"(?:最近)?(?:工作)?空窗(?:期|时间|阶段)?.{0,6}(?:约|大约|左右)?\s*一\s*年"
)
_GAP_SHORT_RE = re.compile(r"短期空窗|空窗(?:期|时间|阶段)?.{0,6}(?:不超过|≤|<=?)\s*6\s*(?:个)?月")
_GAP_LONG_RE = re.compile(r"长期空窗|空窗(?:期|时间|阶段)?.{0,6}(?:超过|大于)\s*6\s*(?:个)?月")
_RECENT_TENURE_1_RE = re.compile(
    r"最近(?:一份|一段|一次)?(?:工作)?.{0,8}(?:任职|在职|工作).{0,8}"
    r"(?:超过|不少于|至少|≥|>=?)?\s*(两|二|\d+)\s*年"
)
_RECENT_WORK_ACTIVITY_RE = re.compile(r"近(?:一|\d+)年(?:内)?(?:有)?(?:工作)?(?:经历|经验)")
_STABILITY_RE = re.compile(r"稳定性(?:高|强)|稳定(?:性)?(?:高|强)的候选人")
_MULTI_GAP_RE = re.compile(r"多段间断空档|多次间断空档|多段空档")
_DISCONTINUITY_RE = re.compile(
    r"(?:就业)?履历(?:有)?(?:断层|断裂)|工作履历(?:有)?(?:断层|断裂)|经历(?:有)?断层"
)
_CONTINUOUS_CAREER_RE = re.compile(
    r"(?:履历|经历)?(?:连续|段间无大空档|无大空档|段间无(?:明显|较大)?空档)"
)
_MID_CAREER_GAP_RE = re.compile(
    r"(?:生涯|履历|经历)(?:中间|中)?.{0,8}(?:有过|存在|有).{0,8}"
    r"(?:一?年(?:及)?以上|超过.{0,4}年|≥?\s*12\s*(?:个)?月).{0,8}(?:空窗|空档)"
)
_MAX_TENURE_RE = re.compile(r"单公司长期深耕|长期深耕(?:的)?候选人")
_AVG_TENURE_RE = re.compile(r"平均司龄长(?:的)?候选人|平均司龄(?:较)?长")
_CURRENT_JOB_RE = re.compile(r"目前在职|当前在职|现职|在职候选人")

_GRADUATION_YEAR_HINT_RE = re.compile(
    r"(?:应届|应届生|校招|春招|秋招|校园招聘|刚毕业|毕业生|(?P<year>20\d{2})\s*届)"
)


def normalize_work_stats_ir(ir: CandidateSearchIR, query_text: str) -> CandidateSearchIR:
    query = str(query_text or "").strip()
    notes = list(ir.meta.notes)

    tenure_intent = classify_work_tenure_intent(query)
    tenure_target = extract_work_tenure_target(query)
    domain_tags: list[str] = []
    existing_fields = {leaf.field for leaf in _collect_leaves(ir.where) if leaf is not None}
    where = _map_where_node(ir.where, query=query, notes=notes, existing_fields=existing_fields)
    where = _prune_latest_scope_conflicts(where, query=query, notes=notes)
    where = _rewrite_work_tenure_intent(
        where,
        query=query,
        notes=notes,
        tenure_intent=tenure_intent,
        tenure_target=tenure_target,
        domain_tags=domain_tags,
    )
    where = _prune_work_years_for_role_tenure(where, notes=notes, tenure_intent=tenure_intent)
    injected = _inject_from_query(
        query,
        where=where,
        notes=notes,
        tenure_target=tenure_target,
        domain_tags=domain_tags,
    )
    validated = validate_work_stats_where(injected, query=query, notes=notes)
    if validated is not None:
        injected = validated
    semantic = _strip_semantic_for_latest_scope(
        list(ir.semantic), query=query, where=injected, notes=notes
    )
    semantic = _ensure_domain_semantic(semantic, domain_tags=domain_tags, notes=notes)
    meta = ir.meta.model_copy(update={"notes": notes, "tenureIntent": tenure_intent})
    return ir.model_copy(update={"where": injected, "meta": meta, "semantic": semantic})


def _collect_domain_tags(*tags: str, into: list[str]) -> None:
    for tag in tags:
        text = str(tag or "").strip()
        if text and text not in into:
            into.append(text)


def _role_tenure_title_key(soft_text: str | None) -> str:
    title, _domains = parse_tenure_role_phrase(str(soft_text or ""))
    return title


def _ensure_domain_semantic(
    semantic: list[SemanticConditionIR],
    *,
    domain_tags: list[str],
    notes: list[str],
) -> list[SemanticConditionIR]:
    if not domain_tags:
        return semantic
    corpus = " ".join(cond.text for cond in semantic)
    missing = [tag for tag in domain_tags if tag and tag not in corpus]
    if not missing:
        return semantic
    text = " ".join(f"{tag}领域 {tag}行业 {tag} pharmaceutical healthcare" for tag in missing)
    notes.append(f"work_stats_semantic:domain_tags({','.join(missing)})")
    return [
        *semantic,
        SemanticConditionIR(text=text.strip(), fields=["work_vector"], weight=0.8),
    ]


def _rewrite_work_tenure_intent(
    node: WhereNode,
    *,
    query: str,
    notes: list[str],
    tenure_intent: str,
    tenure_target: WorkTenureTarget | None,
    domain_tags: list[str],
) -> WhereNode:
    if tenure_target is not None:
        _collect_domain_tags(*tenure_target.domain_tags, into=domain_tags)

    def walk(current: WhereNode) -> WhereNode | None:
        if current.type == "leaf" and current.condition is not None:
            cond = current.condition
            if tenure_intent == "generic" and cond.field == "role_tenure":
                years = tenure_target.years if tenure_target is not None else None
                month_vals = [_month_value(v) for v in cond.values]
                month_vals = [v for v in month_vals if v is not None]
                if years is None and month_vals:
                    years = max(1, month_vals[0] // 12)
                if years is None:
                    return current
                notes.append("work_stats_rewrite:role_tenure_to_work_years")
                return current.model_copy(
                    update={
                        "condition": cond.model_copy(
                            update={"field": "work_years", "values": [years], "softText": None}
                        )
                    }
                )
            if tenure_intent == "title" and cond.field == "role_tenure":
                title_key = _role_tenure_title_key(cond.softText)
                if not title_key and tenure_target is not None:
                    title_key = tenure_target.title
                _, domains = parse_tenure_role_phrase(cond.softText or "")
                _collect_domain_tags(*domains, into=domain_tags)
                if title_key and cond.softText != title_key:
                    notes.append("work_stats_normalize:role_tenure_title_slot")
                    cond = cond.model_copy(update={"softText": title_key})
                    return current.model_copy(update={"condition": cond})
                return current
            if tenure_intent == "title" and cond.field == "work_years":
                if tenure_target is None or not tenure_target.title:
                    return current
                notes.append("work_stats_rewrite:work_years_to_role_tenure")
                return WhereNode(
                    type="leaf",
                    condition=StructuredConditionIR(
                        field="role_tenure",
                        operator=cond.operator
                        if cond.operator in {"gte", "lte", "eq", "range"}
                        else "gte",
                        values=[tenure_target.years * 12],
                        required=cond.required,
                        weight=cond.weight,
                        softText=tenure_target.title,
                    ),
                )
            return current
        if current.type == "not" and current.child is not None:
            child = walk(current.child)
            if child is None:
                return None
            return current.model_copy(update={"child": child})
        if current.type in {"and", "or"}:
            children = [
                child
                for child in (
                    walk(item) for item in current.children if isinstance(item, WhereNode)
                )
                if child is not None
            ]
            if not children:
                return None
            if len(children) == 1 and current.type == "and":
                return children[0]
            return current.model_copy(update={"children": children})
        return current

    rewritten = walk(node)
    return rewritten if rewritten is not None else node


def _where_negates_field(node: WhereNode, field: str) -> bool:
    """Check if the where tree contains a ``not`` node that negates *field*.

    Returns True when the IR has ``not(leaf)`` where leaf.condition.field == *field*,
    anywhere in the tree (including nested inside and/or).
    """
    if node.type == "not" and node.child is not None:
        child = node.child
        if child.type == "leaf" and child.condition is not None:
            return child.condition.field == field
        return _where_negates_field(child, field)
    if node.type in ("and", "or"):
        children = [c for c in node.children if isinstance(c, WhereNode)]
        return any(_where_negates_field(c, field) for c in children)
    return False


def _where_negates_fresh_graduate(node: WhereNode) -> bool:
    """Check if the where tree negates fresh-graduate conditions via IR not nodes.

    True when: ``not(graduation_year)`` OR ``not(work_state)`` with values containing "应届".
    """
    if _where_negates_field(node, "graduation_year"):
        return True
    # Also check not(work_state) with fresh-graduate hint in values
    if node.type == "not" and node.child is not None:
        child = node.child
        if child.type == "leaf" and child.condition is not None:
            cond = child.condition
            if cond.field == "work_state":
                values = [str(v or "").casefold() for v in (cond.values or [])]
                return any("应届" in v for v in values)
        return _where_negates_fresh_graduate(child)
    if node.type in ("and", "or"):
        children = [c for c in node.children if isinstance(c, WhereNode)]
        return any(_where_negates_fresh_graduate(c) for c in children)
    return False


def _month_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def _inject_from_query(
    query: str,
    *,
    where: WhereNode,
    notes: list[str],
    tenure_target: WorkTenureTarget | None,
    domain_tags: list[str],
) -> WhereNode:
    leaves = _collect_leaves(where)
    existing_fields = {leaf.field for leaf in leaves if leaf is not None}

    extra: list[WhereNode] = []

    if (
        _SEGMENT_COUNT_IN_QUERY_RE.search(query)
        and "work_segment_count" not in existing_fields
        and not _where_negates_field(where, "work_segment_count")
    ):
        match = _SEGMENT_COUNT_IN_QUERY_RE.search(query)
        if match:
            count = next(group for group in match.groups() if group)
            threshold = int(count) + 1 if "超过" in query or "多于" in query else int(count)
            extra.append(_leaf("work_segment_count", "gte", [threshold]))
            notes.append(f"work_stats_inject:segment_count>={threshold}")

    if (
        _GAP_OVER_YEARS_RE.search(query)
        and "employment_gap" not in existing_fields
        and not _where_negates_field(where, "employment_gap")
    ):
        match = _GAP_OVER_YEARS_RE.search(query)
        years_raw = match.group(1) if match and match.group(1) else "2"
        years = 2 if years_raw in {"两", "二"} else int(years_raw)
        extra.append(_leaf("employment_gap", "gte", [years * 12]))
        notes.append(f"work_stats_inject:gap_gte_{years}y")

    if (
        _GAP_OVER_MONTHS_RE.search(query)
        and "employment_gap" not in existing_fields
        and not _where_negates_field(where, "employment_gap")
    ):
        match = _GAP_OVER_MONTHS_RE.search(query)
        months = int(match.group(1)) if match else 6
        extra.append(_leaf("employment_gap", "gte", [months]))
        notes.append(f"work_stats_inject:gap_gte_{months}m")

    if (
        _GAP_ABOUT_YEAR_RE.search(query)
        and "employment_gap" not in existing_fields
        and not _where_negates_field(where, "employment_gap")
    ):
        extra.append(_leaf("employment_gap", "eq", ["approx_12m"]))
        notes.append("work_stats_inject:gap_approx_12m")

    if (
        _GAP_SHORT_RE.search(query)
        and "employment_gap" not in existing_fields
        and not _where_negates_field(where, "employment_gap")
    ):
        extra.append(_leaf("employment_gap", "eq", ["trailing_le_6m"]))
        notes.append("work_stats_inject:gap_trailing_le_6m")

    if (
        _GAP_LONG_RE.search(query)
        and "employment_gap" not in existing_fields
        and not _where_negates_field(where, "employment_gap")
    ):
        extra.append(_leaf("employment_gap", "eq", ["trailing_gt_6m"]))
        notes.append("work_stats_inject:gap_trailing_gt_6m")

    if (
        _RECENT_WORK_ACTIVITY_RE.search(query)
        and "employment_gap" not in existing_fields
        and not _where_negates_field(where, "employment_gap")
    ):
        extra.append(_leaf("employment_gap", "lte", [12]))
        notes.append("work_stats_inject:recent_work_within_12m")

    if (
        _STABILITY_RE.search(query)
        and "job_stability" not in existing_fields
        and not _where_negates_field(where, "job_stability")
    ):
        extra.append(_leaf("job_stability", "eq", ["strong"]))
        notes.append("work_stats_inject:stability_strong_v1")

    if (
        _MAX_TENURE_RE.search(query)
        and "max_tenure" not in existing_fields
        and not _where_negates_field(where, "max_tenure")
    ):
        extra.append(_leaf("max_tenure", "gte", [MAX_TENURE_STRONG_MIN_MONTHS]))
        notes.append("work_stats_inject:max_tenure_gte_36")

    if (
        _AVG_TENURE_RE.search(query)
        and "avg_tenure" not in existing_fields
        and not _where_negates_field(where, "avg_tenure")
    ):
        extra.append(_leaf("avg_tenure", "gte", [AVG_TENURE_LONG_MIN_MONTHS]))
        notes.append("work_stats_inject:avg_tenure_gte_24")

    if (
        _MULTI_GAP_RE.search(query)
        and "inter_segment_gap" not in existing_fields
        and not _where_negates_field(where, "inter_segment_gap")
    ):
        extra.append(_leaf("inter_segment_gap", "gte", [2]))
        notes.append("work_stats_inject:inter_segment_gap_count_gte_2")

    if (
        _DISCONTINUITY_RE.search(query)
        and "career_discontinuity" not in existing_fields
        and not _where_negates_field(where, "career_discontinuity")
    ):
        extra.append(_leaf("career_discontinuity", "eq", [True]))
        notes.append("work_stats_inject:career_discontinuity_v1")

    if _CONTINUOUS_CAREER_RE.search(query):
        if "max_inter_segment_gap" not in existing_fields and not _where_negates_field(
            where, "max_inter_segment_gap"
        ):
            extra.append(_leaf("max_inter_segment_gap", "lte", [6]))
            notes.append("work_stats_inject:max_inter_segment_gap_lte_6")
        if "inter_segment_gap" not in existing_fields and not _where_negates_field(
            where, "inter_segment_gap"
        ):
            extra.append(_leaf("inter_segment_gap", "lte", [0]))
            notes.append("work_stats_inject:inter_segment_gap_lte_0")
        if "employment_gap" not in existing_fields and not _where_negates_field(
            where, "employment_gap"
        ):
            extra.append(_leaf("employment_gap", "lte", [6]))
            notes.append("work_stats_inject:continuous_trailing_le_6m")

    if (
        _MID_CAREER_GAP_RE.search(query)
        and "max_inter_segment_gap" not in existing_fields
        and not _where_negates_field(where, "max_inter_segment_gap")
    ):
        extra.append(_leaf("max_inter_segment_gap", "gte", [12]))
        notes.append("work_stats_inject:max_inter_segment_gap_gte_12")

    if (
        _CURRENT_JOB_RE.search(query)
        and "employment_gap" not in existing_fields
        and not _where_negates_field(where, "employment_gap")
    ):
        extra.append(_leaf("employment_gap", "lte", [0]))
        notes.append("work_stats_inject:currently_employed")

    grad_match = _GRADUATION_YEAR_HINT_RE.search(query)
    if (
        grad_match
        and "graduation_year" not in existing_fields
        and not _where_negates_fresh_graduate(where)
    ):
        explicit_year = grad_match.group("year")
        if explicit_year:
            target_year = int(explicit_year)
        else:
            target_year = datetime.now().year
        extra.append(_leaf("graduation_year", "eq", [target_year]))
        notes.append(f"work_stats_inject:graduation_year_eq_{target_year}")

    tenure_intent = classify_work_tenure_intent(query)
    tenure_match = _RECENT_TENURE_1_RE.search(query)
    if (
        tenure_match
        and "recent_tenure_1" not in existing_fields
        and not _where_negates_field(where, "recent_tenure_1")
    ):
        years_raw = tenure_match.group(1)
        years = 2 if years_raw in {"两", "二"} else int(years_raw)
        extra.append(_leaf("recent_tenure_1", "gte", [years * 12]))
        notes.append(f"work_stats_inject:recent_tenure_1_gte_{years}y")

    if tenure_target is not None:
        _collect_domain_tags(*tenure_target.domain_tags, into=domain_tags)
        if (
            tenure_intent == "title"
            and tenure_target.title
            and "role_tenure" not in existing_fields
            and not _where_negates_field(where, "role_tenure")
        ):
            extra.append(
                WhereNode(
                    type="leaf",
                    condition=StructuredConditionIR(
                        field="role_tenure",
                        operator="gte",
                        values=[tenure_target.years * 12],
                        softText=tenure_target.title,
                    ),
                )
            )
            notes.append(
                f"work_stats_inject:role_tenure({tenure_target.title})>={tenure_target.years}y"
            )
        elif (
            tenure_intent == "generic"
            and "work_years" not in existing_fields
            and not _where_negates_field(where, "work_years")
        ):
            extra.append(_leaf("work_years", "gte", [tenure_target.years]))
            notes.append(
                f"work_stats_inject:work_years_gte_{tenure_target.years}y_from_generic_tenure"
            )

    if not extra:
        return where
    if where.type == "and":
        return where.model_copy(update={"children": [*where.children, *extra]})
    return WhereNode(type="and", children=[where, *extra])


def _tree_has_field(node: WhereNode, field: str) -> bool:
    """Return True if any leaf in the tree has condition.field == field."""
    if node.type == "leaf" and node.condition is not None:
        return node.condition.field == field
    if node.type == "not" and node.child is not None:
        return _tree_has_field(node.child, field)
    if node.type in {"and", "or"}:
        for child in node.children:
            if isinstance(child, WhereNode) and _tree_has_field(child, field):
                return True
    return False


def _prune_work_years_for_role_tenure(
    node: WhereNode,
    *,
    notes: list[str],
    tenure_intent: str,
) -> WhereNode:
    if tenure_intent != "title":
        return node

    # Only prune work_years if role_tenure actually exists in the tree.
    # Otherwise _rewrite_work_tenure_intent may have been unable to extract
    # the target, and silently dropping work_years would lose the constraint.
    if not _tree_has_field(node, "role_tenure"):
        notes.append("work_stats_prune:skip_work_years_prune_missing_role_tenure")
        return node

    def walk(current: WhereNode) -> WhereNode | None:
        if current.type == "leaf" and current.condition is not None:
            if current.condition.field == "work_years":
                notes.append("work_stats_prune:work_years_for_role_tenure")
                return None
            return current
        if current.type == "not" and current.child is not None:
            child = walk(current.child)
            if child is None:
                return None
            return current.model_copy(update={"child": child})
        if current.type in {"and", "or"}:
            children: list[WhereNode] = []
            for child in current.children:
                if not isinstance(child, WhereNode):
                    continue
                mapped = walk(child)
                if mapped is not None:
                    children.append(mapped)
            if not children:
                return None
            if len(children) == 1 and current.type == "and":
                return children[0]
            return current.model_copy(update={"children": children})
        return current

    pruned = walk(node)
    return pruned if pruned is not None else node


def _strip_semantic_for_latest_scope(
    semantic: list[SemanticConditionIR],
    *,
    query: str,
    where: WhereNode,
    notes: list[str],
) -> list[SemanticConditionIR]:
    leaves = [leaf for leaf in _collect_leaves(where) if leaf is not None]
    has_latest_company = any(leaf.field == "latest_company" for leaf in leaves)
    has_latest_job = any(leaf.field == "latest_work_job" for leaf in leaves)
    if not semantic:
        return semantic
    kept: list[SemanticConditionIR] = []
    for cond in semantic:
        fields = {str(field) for field in (cond.fields or [])}
        if (
            has_latest_company
            and query_has_latest_company_scope(query)
            and fields.intersection({"work_vector"})
        ):
            notes.append("work_stats_prune:company_semantic_for_latest_scope")
            continue
        if (
            has_latest_job
            and query_has_latest_job_scope(query)
            and fields.intersection({"work_vector"})
        ):
            notes.append("work_stats_prune:position_semantic_for_latest_scope")
            continue
        kept.append(cond)
    return kept


def _prune_latest_scope_conflicts(
    node: WhereNode,
    *,
    query: str,
    notes: list[str],
) -> WhereNode:
    latest_company_scope = query_has_latest_company_scope(query)
    latest_job_scope = query_has_latest_job_scope(query)
    if not latest_company_scope and not latest_job_scope:
        return node

    def walk(current: WhereNode) -> WhereNode | None:
        if current.type == "leaf" and current.condition is not None:
            field = current.condition.field
            if latest_company_scope and field == "company":
                notes.append("work_stats_prune:company_for_latest_scope")
                return None
            if latest_job_scope and field == "position":
                notes.append("work_stats_prune:position_for_latest_scope")
                return None
            return current
        if current.type == "not" and current.child is not None:
            child = walk(current.child)
            if child is None:
                return None
            return current.model_copy(update={"child": child})
        if current.type in {"and", "or"}:
            children: list[WhereNode] = []
            for child in current.children:
                if not isinstance(child, WhereNode):
                    continue
                mapped = walk(child)
                if mapped is not None:
                    children.append(mapped)
            if not children:
                return None
            if len(children) == 1 and current.type == "and":
                return children[0]
            return current.model_copy(update={"children": children})
        return current

    pruned = walk(node)
    return pruned if pruned is not None else node


def _map_where_node(
    node: WhereNode,
    *,
    query: str,
    notes: list[str],
    existing_fields: set[str],
) -> WhereNode:
    if node.type == "and":
        return node.model_copy(
            update={
                "children": [
                    _map_where_node(
                        child, query=query, notes=notes, existing_fields=existing_fields
                    )
                    for child in node.children
                    if isinstance(child, WhereNode)
                ]
            }
        )
    if node.type == "or":
        return node.model_copy(
            update={
                "children": [
                    _map_where_node(
                        child, query=query, notes=notes, existing_fields=existing_fields
                    )
                    for child in node.children
                    if isinstance(child, WhereNode)
                ]
            }
        )
    if node.type == "not" and node.child is not None:
        return node.model_copy(
            update={
                "child": _map_where_node(
                    node.child, query=query, notes=notes, existing_fields=existing_fields
                )
            }
        )
    if node.type != "leaf" or node.condition is None:
        return node

    cond = node.condition
    rewritten = _rewrite_condition(cond, query=query, notes=notes, existing_fields=existing_fields)
    return node.model_copy(update={"condition": rewritten})


def _rewrite_condition(
    cond: StructuredConditionIR,
    *,
    query: str,
    notes: list[str],
    existing_fields: set[str],
) -> StructuredConditionIR:
    if cond.field == "work_segment_count" and cond.operator == "gte":
        cond = _adjust_segment_count_exceed_threshold(cond, query=query, notes=notes)

    if cond.field == "work_years":
        segment_match = _SEGMENT_COUNT_IN_QUERY_RE.search(query)
        if (
            segment_match
            and "work_segment_count" not in existing_fields
            and cond.operator in {"gte", "gt", "lte", "eq", "range"}
        ):
            values = [int(value) for value in cond.values if isinstance(value, (int, float))]
            if values:
                count = next(int(group) for group in segment_match.groups() if group)
                match_text = segment_match.group(0)
                notes.append("work_stats_rewrite:work_years_to_segment_count")
                op = "gte" if cond.operator == "gte" else cond.operator
                threshold = count + (1 if re.search(r"超过|多于", match_text) else 0)
                return cond.model_copy(
                    update={"field": "work_segment_count", "operator": op, "values": [threshold]}
                )

    if cond.field == "company" and query_has_latest_company_scope(query):
        notes.append("work_stats_rewrite:company_to_latest_company")
        return cond.model_copy(update={"field": "latest_company"})

    if cond.field == "position" and query_has_latest_job_scope(query):
        notes.append("work_stats_rewrite:position_to_latest_work_job")
        return cond.model_copy(update={"field": "latest_work_job"})

    if cond.field == "role_tenure":
        title_key = _role_tenure_title_key(cond.softText)
        if title_key and title_key != (cond.softText or ""):
            notes.append("work_stats_normalize:role_tenure_title_slot")
            return cond.model_copy(update={"softText": title_key})

    return cond


def _adjust_segment_count_exceed_threshold(
    cond: StructuredConditionIR,
    *,
    query: str,
    notes: list[str],
) -> StructuredConditionIR:
    """「超过/多于 N 段」严格大于 N → ``gte N+1``（#2132：超过5段 → gte 6）。"""
    values = [
        int(v) for v in cond.values if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    if not values:
        return cond
    threshold = values[0]
    exceed_match = re.search(
        r"(?:超过|多于)\s*(\d+)\s*段|"
        r"(\d+)\s*段以上",
        query,
    )
    if not exceed_match:
        return cond
    count = int(next(group for group in exceed_match.groups() if group))
    if count == threshold:
        notes.append(f"work_stats_rewrite:segment_exceed_{count}_to_gte_{count + 1}")
        return cond.model_copy(update={"values": [count + 1]})
    return cond


def _leaf(field: StructuredField, operator: str, values: list[Any]) -> WhereNode:
    return WhereNode(
        type="leaf",
        condition=StructuredConditionIR(field=field, operator=operator, values=values),
    )


def _collect_leaves(node: WhereNode) -> list[StructuredConditionIR | None]:
    if node.type == "leaf":
        return [node.condition]
    if node.type == "not":
        return _collect_leaves(node.child) if node.child is not None else []
    out: list[StructuredConditionIR | None] = []
    for child in node.children:
        out.extend(_collect_leaves(child))
    return out


__all__ = ["normalize_work_stats_ir"]
