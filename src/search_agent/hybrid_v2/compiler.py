"""Compile Hybrid V2 IR into the existing HybridQueryResult contract."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .catalogs import (
    get_salary_bands,
    resolve_salary_codes_range_overlap,
    resolve_salary_codes_strict,
    resolve_work_state_codes,
)
from .field_policy import (
    DEGREE_CODE_BY_NAME,
    SEMANTIC_SUPPLEMENT_FIELDS,
    FieldPolicy,
    get_field_policy,
)
from .schemas import (
    CandidateSearchIR,
    ESQueryMeta,
    HardBlock,
    HybridQueryResult,
    SoftCondition,
    StructuredConditionIR,
    StructuredField,
    WhereNode,
)
from .work_stats_compiler import (
    compile_work_stats_condition,
    compile_work_stats_negated,
)

_INTENTION_CITY_HINT = re.compile(
    r"(期望|意向|想去|希望去)(?:.{0,8}(?:工作|城市)|[\u4e00-\u9fff]{2,})|"
    r"求职意向|期望城市"
)
_LIVE_CITY_HINT = re.compile(r"(现居|目前在|人在|常驻)")
_WORK_POSITION_HINT = re.compile(
    r"(做过|担任过|任职|职位为|岗位为|曾任|历任|担任|任过|任职过|"
    r"工作经历|工作过的岗位|任职经历|从事过)"
)
_INTENTION_POSITION_HINT = re.compile(
    r"(期望|意向|想做|想从事|希望做|希望从事).{0,10}(职能|岗位|职位)|"
    r"期望职能|意向岗位|意向职位|期望岗位|意向职能"
)
_INTERNSHIP_SCOPE_HINT = re.compile(
    r"(实习过|曾实习|实习经历|实习于|实习在|实习单位|实习生|"
    r"实习做|实习担任|实习任职|实习岗位|实习职位|有实习经历|无实习)"
)
_WORK_SCOPE_HINT = re.compile(r"(工作过|就职|任职|正式|全职|工作经历|从事过|担任过|曾任|历任)")
_CAREER_CHANGE_HINT = re.compile(r"转岗|转行|转型")
_NEGATED_WORK_EXPERIENCE_RE = re.compile(
    r"(?:没|未|不曾|从未|没有|无).{0,6}做过|"
    r"没做过|未做过|"
    r"之前没做过|从没做过|"
    r"没有.{0,8}(?:工作经历|任职经历)"
)
_ORAL_SALARY_WAN_RE = re.compile(r"([零一二两三四五六七八九十\d\.]+)\s*万")
_ORAL_SALARY_QIAN_RE = re.compile(r"([零一二两三四五六七八九十\d\.]+)\s*千")
_SALARY_GTE_HINT = re.compile(r"(以上|及以上|至少|不少于|不低于|高于|起)")
_SURNAME_HINT = re.compile(r"姓(?!名)|姓氏")
_EXACT_NAME_HINT = re.compile(r"(姓名|名字)(完全是|等于|是|为)|候选人叫|叫做")
_NAME_CONTAINS_HINT = re.compile(r"(姓名|名字).{0,6}(包含|含有|带|中有|里有)")
_BIRTH_YEAR_QUERY_RE = re.compile(r"(\d{4})\s*年(?:出生|的)?")
_EXPLICIT_AGE_QUERY_RE = re.compile(r"\d+\s*[-~～至到]\s*\d+\s*岁|\d+\s*岁")


_EDUCATION_FIELDS: frozenset[StructuredField] = frozenset(
    {"school", "major", "degree", "school_tier"}
)
_WORK_STATS_FIELDS: frozenset[StructuredField] = frozenset(
    {
        "work_segment_count",
        "employment_gap",
        "latest_company",
        "latest_work_job",
        "recent_tenure_1",
        "max_tenure",
        "avg_tenure",
        "short_job_count",
        "job_stability",
        "inter_segment_gap",
        "max_inter_segment_gap",
        "career_discontinuity",
        "role_tenure",
    }
)


def compile_ir_to_hybrid_result(
    ir: CandidateSearchIR,
    *,
    tag_name_to_id: dict[str, int] | None = None,
    query_text: str | None = None,
) -> HybridQueryResult:
    """Compile business IR to the V1-compatible HybridQueryResult model."""

    hard_filter: list[dict[str, Any]] = []
    hard_must_not: list[dict[str, Any]] = []
    positive_leaves = _collect_positive_leaves(ir.where)
    soft: list[SoftCondition] = [
        SoftCondition(text=cond.text, fields=list(cond.fields), weight=cond.weight)
        for cond in ir.semantic
    ]
    notes = list(ir.meta.notes)
    has_intention_position_leaf = any(
        cond.field == "intention_position" for cond in _collect_positive_leaves(ir.where)
    )
    where_clause = _compile_where_node(
        ir.where,
        query_text=query_text or "",
        tag_name_to_id=tag_name_to_id,
        notes=notes,
        has_intention_position_leaf=has_intention_position_leaf,
    )
    if where_clause:
        hard_filter.append(where_clause)

    has_non_company_signal = bool(ir.semantic) or any(
        _resolve_city_field_for_cond(cond, query_text or "") != "company"
        for cond in positive_leaves
    )
    has_latest_company = any(cond.field == "latest_company" for cond in positive_leaves)
    has_latest_work_job = any(cond.field == "latest_work_job" for cond in positive_leaves)
    for cond in positive_leaves:
        field = _resolve_city_field_for_cond(cond, query_text or "")
        if field == "company" and has_latest_company:
            notes.append("structured_skip:company_for_latest_company")
            continue
        if field == "position" and has_latest_work_job:
            notes.append("structured_skip:position_for_latest_work_job")
            continue
        policy = get_field_policy(field)
        cond = cond.model_copy(update={"field": field})
        add_default_soft = not (cond.field == "company" and has_non_company_signal)
        may_supplement_soft = cond.field in SEMANTIC_SUPPLEMENT_FIELDS
        extra_soft = _soft_conditions_for_structured_hint(
            cond,
            policy,
            add_default=add_default_soft and may_supplement_soft,
        )
        soft.extend(extra_soft)
        if (
            add_default_soft
            and may_supplement_soft
            and policy.add_soft_by_default
            and policy.soft_fields
            and not extra_soft
        ):
            soft.append(_make_soft_condition(cond, policy))

    return HybridQueryResult(
        hard=HardBlock(filter=hard_filter, must_not=hard_must_not),
        soft=soft,
        sort=ir.sort,
        meta=ESQueryMeta(
            needClarification=ir.meta.needClarification,
            clarifyQuestions=list(ir.meta.clarifyQuestions),
            notes=notes,
        ),
    )


def _compile_where_node(
    node: WhereNode,
    *,
    query_text: str,
    tag_name_to_id: dict[str, int] | None,
    notes: list[str],
    has_intention_position_leaf: bool = False,
) -> dict[str, Any] | None:
    if node.type == "and":
        same_education_nested, grouped_child_indexes = _compile_same_education_nested_from_and(
            node.children,
            query_text=query_text,
            tag_name_to_id=tag_name_to_id,
            notes=notes,
        )
        same_internship_nested, internship_grouped_child_indexes = (
            _compile_same_internship_nested_from_and(
                node.children,
                query_text=query_text,
                tag_name_to_id=tag_name_to_id,
                notes=notes,
            )
        )
        grouped_child_indexes |= internship_grouped_child_indexes
        compiled_children = [
            same_education_nested,
            same_internship_nested,
            *[
                c
                for c in (
                    _compile_where_node(
                        child,
                        query_text=query_text,
                        tag_name_to_id=tag_name_to_id,
                        notes=notes,
                        has_intention_position_leaf=has_intention_position_leaf,
                    )
                    for index, child in enumerate(node.children)
                    if index not in grouped_child_indexes
                )
                if c is not None
            ],
        ]
        compiled_children = [child for child in compiled_children if child is not None]
        if not compiled_children:
            return None
        if len(compiled_children) == 1:
            return compiled_children[0]
        return {"bool": {"filter": compiled_children}}

    if node.type == "or":
        compiled_children = [
            c
            for c in (
                _compile_where_node(
                    child,
                    query_text=query_text,
                    tag_name_to_id=tag_name_to_id,
                    notes=notes,
                    has_intention_position_leaf=has_intention_position_leaf,
                )
                for child in node.children
            )
            if c is not None
        ]
        if not compiled_children:
            return None
        if len(compiled_children) == 1:
            return compiled_children[0]
        return {
            "bool": {
                "should": compiled_children,
                "minimum_should_match": int(node.minimumShouldMatch or 1),
            }
        }

    if node.type == "not":
        if node.child is None:
            return None
        compiled = _compile_where_node(
            node.child,
            query_text=query_text,
            tag_name_to_id=tag_name_to_id,
            notes=notes,
            has_intention_position_leaf=has_intention_position_leaf,
        )
        if compiled is None:
            return None
        return {"bool": {"must_not": [compiled]}}

    # leaf
    cond = node.condition
    cond = _resolve_birth_year_cond(cond, query_text, notes)
    field = _resolve_city_field_for_cond(cond, query_text)
    cond = cond.model_copy(update={"field": field})
    field = _resolve_company_field_for_cond(cond, query_text)
    cond = cond.model_copy(update={"field": field})
    field = _resolve_position_field_for_cond(cond, query_text)
    cond = cond.model_copy(update={"field": field})

    if cond.field == "intention_position":
        return _compile_intention_position_leaf(cond, notes=notes)

    if cond.field == "internship_job":
        return _compile_internship_position_leaf(cond, notes=notes)

    if cond.field == "position":
        return _compile_work_position_leaf(
            cond,
            query_text=query_text,
            tag_name_to_id=tag_name_to_id,
            notes=notes,
            allow_intention_fallback=not has_intention_position_leaf,
        )

    if cond.field == "internship_company":
        return _compile_internship_company_leaf(
            cond,
            query_text=query_text,
            tag_name_to_id=tag_name_to_id,
            notes=notes,
        )

    if cond.field == "company":
        return _compile_company_leaf(
            cond,
            query_text=query_text,
            tag_name_to_id=tag_name_to_id,
            notes=notes,
        )

    if cond.field in _WORK_STATS_FIELDS:
        if cond.operator == "not":
            clauses = compile_work_stats_negated(cond)
            if not clauses:
                notes.append(f"structured_to_soft:{cond.field}")
                return None
            return {"bool": {"must_not": clauses}}
        clauses = compile_work_stats_condition(cond)
        if not clauses:
            notes.append(f"structured_to_soft:{cond.field}")
            return None
        if len(clauses) == 1:
            return clauses[0]
        return {"bool": {"filter": clauses}}

    policy = get_field_policy(field)

    if cond.operator == "not":
        positive = cond.model_copy(update={"operator": "any"})
        clauses = _compile_hard_condition(
            positive,
            policy,
            tag_name_to_id=tag_name_to_id,
            query_text=query_text,
            notes=notes,
        )
        if not clauses:
            notes.append(f"structured_to_soft:{cond.field}")
            return None
        return {"bool": {"must_not": clauses}}

    if _should_compile_as_hard(cond, policy):
        clauses = _compile_hard_condition(
            cond,
            policy,
            tag_name_to_id=tag_name_to_id,
            query_text=query_text,
            notes=notes,
        )
    else:
        clauses = []
        notes.append(f"structured_to_soft:{cond.field}")
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"bool": {"filter": clauses}}


def _compile_same_internship_nested_from_and(
    children: list[WhereNode],
    *,
    query_text: str,
    tag_name_to_id: dict[str, int] | None,
    notes: list[str],
) -> tuple[dict[str, Any] | None, set[int]]:
    grouped: list[tuple[int, StructuredConditionIR]] = []
    seen_fields: set[StructuredField] = set()

    for index, child in enumerate(children):
        if child.type != "leaf" or child.condition is None:
            continue
        cond = child.condition
        field = _resolve_city_field_for_cond(cond, query_text)
        cond = cond.model_copy(update={"field": field})
        field = _resolve_company_field_for_cond(cond, query_text)
        cond = cond.model_copy(update={"field": field})
        field = _resolve_position_field_for_cond(cond, query_text)
        if field not in {"internship_company", "internship_job"} or cond.operator == "not":
            continue
        cond = cond.model_copy(update={"field": field})
        policy = get_field_policy(field)
        if policy.nested_path != "internship_experiences" or not _should_compile_as_hard(
            cond, policy
        ):
            continue
        grouped.append((index, cond))
        seen_fields.add(field)

    if len(seen_fields) < 2:
        return None, set()

    filters: list[dict[str, Any]] = []
    grouped_indexes: set[int] = set()
    for index, cond in grouped:
        policy = get_field_policy(cond.field)
        clauses = _compile_hard_condition(
            cond,
            policy,
            tag_name_to_id=tag_name_to_id,
            query_text=query_text,
            wrap_nested=False,
        )
        if not clauses:
            continue
        filters.extend(clauses)
        grouped_indexes.add(index)

    if len(grouped_indexes) < 2 or len(filters) < 2:
        return None, set()

    notes.append("internship_scope=same_nested")
    return _wrap_nested("internship_experiences", {"bool": {"filter": filters}}), grouped_indexes


def _compile_same_education_nested_from_and(
    children: list[WhereNode],
    *,
    query_text: str,
    tag_name_to_id: dict[str, int] | None,
    notes: list[str],
) -> tuple[dict[str, Any] | None, set[int]]:
    grouped: list[tuple[int, StructuredConditionIR]] = []
    seen_fields: set[StructuredField] = set()

    for index, child in enumerate(children):
        if child.type != "leaf" or child.condition is None:
            continue
        cond = child.condition
        field = _resolve_city_field_for_cond(cond, query_text)
        if field not in _EDUCATION_FIELDS or cond.operator == "not":
            continue
        policy = get_field_policy(field)
        if policy.nested_path != "educations" or not _should_compile_as_hard(cond, policy):
            continue
        grouped.append((index, cond.model_copy(update={"field": field})))
        seen_fields.add(field)

    if len(seen_fields) < 2:
        return None, set()

    filters: list[dict[str, Any]] = []
    grouped_indexes: set[int] = set()
    for index, cond in grouped:
        policy = get_field_policy(cond.field)
        clauses = _compile_hard_condition(
            cond,
            policy,
            tag_name_to_id=tag_name_to_id,
            query_text=query_text,
            wrap_nested=False,
        )
        if not clauses:
            continue
        filters.extend(clauses)
        grouped_indexes.add(index)

    if len(grouped_indexes) < 2 or len(filters) < 2:
        return None, set()

    notes.append("education_scope=same_nested")
    return _wrap_nested("educations", {"bool": {"filter": filters}}), grouped_indexes


def _collect_positive_leaves(
    node: WhereNode | Any, negated: bool = False
) -> list[StructuredConditionIR]:
    if not isinstance(node, WhereNode):
        return []
    if node.type == "leaf":
        if negated or node.condition is None:
            return []
        return [node.condition]
    if node.type == "not":
        if node.child is None:
            return []
        return _collect_positive_leaves(node.child, not negated)
    out: list[StructuredConditionIR] = []
    for child in node.children:
        if isinstance(child, WhereNode):
            out.extend(_collect_positive_leaves(child, negated))
    return out


_SALARY_HINT_RE = re.compile(r"(薪|月薪|年薪|薪资|工资|k\b|K\b|万)", re.I)
_DEGREE_HINTS = ("学历", "本科", "硕士", "博士", "大专", "专科", "学士", "研究生", "MBA")


def _semantic_echoes_structured_cond(text: str, cond: StructuredConditionIR) -> bool:
    values = _string_values(list(cond.values))
    field = cond.field

    if field == "live_city":
        if not any(token in text for token in ("现居", "居住地", "居住", "所在地", "人在", "常驻")):
            return False
        return any(city and city in text for city in values)

    if field in {"intention_city", "city"}:
        if any(v in text for v in values):
            if any(token in text for token in ("期望", "意向", "城市", "工作地")):
                return True
            if field == "city" and "城市" in text:
                return True
        return False

    if field == "expected_salary":
        return bool(_SALARY_HINT_RE.search(text))

    if field == "degree":
        if any(str(v) in text for v in values):
            return True
        return any(hint in text for hint in _DEGREE_HINTS)

    if field in {"school", "major"}:
        return any(str(v) in text for v in values)

    if field == "talent_tag":
        if any(str(v) in text for v in values):
            return True
        return any(token in text for token in ("985", "211", "双一流", "QS"))

    if field in {"gender", "work_state"}:
        return any(str(v) in text for v in values)

    if field in {"work_years", "age", "birth_year"}:
        return any(str(v) in text for v in values)

    if field == "company":
        return any(str(v) in text for v in values)

    if field == "internship_company":
        return any(str(v) in text for v in values)

    if field == "position":
        if _INTENTION_POSITION_HINT.search(text) and not _WORK_POSITION_HINT.search(text):
            return False
        return any(str(v) in text for v in values)

    if field == "internship_job":
        if _INTENTION_POSITION_HINT.search(text):
            return False
        return any(str(v) in text for v in values)

    if field == "intention_position":
        if _WORK_POSITION_HINT.search(text) and not _INTENTION_POSITION_HINT.search(text):
            return False
        if any(str(v) in text for v in values):
            return True
        return bool(_INTENTION_POSITION_HINT.search(text))

    if field in {"certificate", "language"}:
        return any(str(v) in text for v in _case_fold_values(values))

    return False


def _compile_hard_condition(
    cond: StructuredConditionIR,
    policy: FieldPolicy,
    *,
    tag_name_to_id: dict[str, int] | None = None,
    query_text: str = "",
    wrap_nested: bool = True,
    notes: list[str] | None = None,
) -> list[dict[str, Any]]:
    if cond.field == "name":
        clauses = _compile_name_condition(cond, policy, query_text=query_text)
    elif policy.hard_strategy == "range":
        clauses = _compile_range_condition(cond, policy, query_text=query_text)
    elif policy.hard_strategy == "birth_year":
        clauses = _compile_birth_year_condition(cond, policy)
    elif policy.hard_strategy == "work_state":
        clauses = _compile_work_state_condition(
            cond, policy, notes=notes if notes is not None else []
        )
    elif policy.hard_strategy == "text":
        clauses = _compile_text_condition(cond, policy)
    elif policy.hard_strategy == "talent_tag":
        clauses = _compile_talent_tag_condition(cond, tag_name_to_id=tag_name_to_id)
    elif policy.hard_strategy == "cert_lang":
        clauses = _compile_cert_language_condition(cond, policy)
    elif policy.hard_strategy == "work_stats":
        clauses = compile_work_stats_condition(cond)
    else:
        clauses = _compile_term_condition(cond, policy)
    if wrap_nested and policy.nested_path:
        return [_wrap_nested(policy.nested_path, clause) for clause in clauses]
    return clauses


def _compile_name_condition(
    cond: StructuredConditionIR,
    policy: FieldPolicy,
    *,
    query_text: str = "",
) -> list[dict[str, Any]]:
    values = _string_values(cond.values)
    if not values:
        return []

    field = policy.hard_field
    if _query_mentions_name_contains(query_text):
        clauses = [{"wildcard": {field: f"*{value}*"}} for value in values]
        return _combine_name_pattern_clauses(clauses, cond.operator)

    if _query_mentions_name_suffix(query_text):
        clauses = [{"wildcard": {field: f"*{value}"}} for value in values]
        return _combine_name_pattern_clauses(clauses, cond.operator)

    if any(_name_value_has_single_char_placeholder(value) for value in values):
        clauses = [
            {"wildcard": {field: _normalize_name_wildcard_pattern(value)}} for value in values
        ]
        return _combine_name_pattern_clauses(clauses, cond.operator)

    if _query_mentions_surname(query_text):
        clauses = [{"prefix": {field: value}} for value in values]
        return _combine_name_pattern_clauses(clauses, cond.operator)

    if _name_values_are_single_cjk_chars(values) and not _query_mentions_exact_name(query_text):
        clauses = [{"wildcard": {field: f"*{value}*"}} for value in values]
        return _combine_name_pattern_clauses(clauses, cond.operator)

    return _compile_term_condition(cond, policy)


def _combine_name_pattern_clauses(
    clauses: list[dict[str, Any]],
    operator: str,
) -> list[dict[str, Any]]:
    if not clauses:
        return []
    if operator == "all":
        return clauses
    if len(clauses) == 1:
        return clauses
    return [{"bool": {"should": clauses, "minimum_should_match": 1}}]


def _name_value_has_single_char_placeholder(value: str) -> bool:
    return "_" in value


def _normalize_name_wildcard_pattern(value: str) -> str:
    return value.replace("_", "?")


def _name_values_are_single_cjk_chars(values: list[str]) -> bool:
    return all(re.fullmatch(r"[\u4e00-\u9fff]", value) for value in values)


def _resolve_birth_year_cond(
    cond: StructuredConditionIR,
    query_text: str,
    notes: list[str],
) -> StructuredConditionIR:
    """When query expresses birth year but LLM mapped to age, rewrite to birth_year."""

    if cond.field != "age":
        return cond
    if _EXPLICIT_AGE_QUERY_RE.search(query_text or ""):
        return cond
    match = _BIRTH_YEAR_QUERY_RE.search(query_text or "")
    if not match:
        return cond
    year = int(match.group(1))
    if not 1900 <= year <= 2100:
        return cond
    notes.append(f"birth_year_rewrite:{year}")
    return cond.model_copy(update={"field": "birth_year", "operator": "eq", "values": [year]})


def _city_value_intent(value: str, query_text: str) -> str:
    """Per-city intent from the hint closest to the city token in query."""
    q = query_text or ""
    text = str(value or "").strip()
    if not text or not q:
        return "unknown"
    idx = q.find(text)
    if idx < 0:
        return "unknown"

    def _nearest_hint_distance(pattern: re.Pattern[str]) -> int | None:
        best: int | None = None
        for match in pattern.finditer(q):
            dist = min(abs(match.start() - idx), abs(match.end() - (idx + len(text))))
            if best is None or dist < best:
                best = dist
        return best

    live_dist = _nearest_hint_distance(_LIVE_CITY_HINT)
    intention_dist = _nearest_hint_distance(_INTENTION_CITY_HINT)
    if live_dist is None and intention_dist is None:
        return "unknown"
    if live_dist is None:
        return "intention"
    if intention_dist is None:
        return "live"
    if intention_dist < live_dist:
        return "intention"
    if live_dist < intention_dist:
        return "live"
    return "unknown"


def _resolve_city_field(field: StructuredField, query_text: str) -> StructuredField:
    """将旧版 city 按 query 语义路由到 intention_city / live_city。"""
    if field != "city":
        return field
    has_intention = bool(_INTENTION_CITY_HINT.search(query_text or ""))
    has_live = bool(_LIVE_CITY_HINT.search(query_text or ""))
    if has_intention and not has_live:
        return "intention_city"
    if has_live and not has_intention:
        return "live_city"
    return field


def _resolve_city_field_for_cond(
    cond: StructuredConditionIR,
    query_text: str,
) -> StructuredField:
    """Route generic ``city`` leaves using per-value query context when possible."""
    if cond.field != "city":
        return cond.field
    values = _string_values(cond.values)
    if not values:
        return _resolve_city_field(cond.field, query_text)
    intents = {_city_value_intent(value, query_text) for value in values}
    intents.discard("unknown")
    if len(intents) == 1:
        intent = next(iter(intents))
        return "intention_city" if intent == "intention" else "live_city"
    if len(values) == 1:
        intent = _city_value_intent(values[0], query_text)
        if intent == "intention":
            return "intention_city"
        if intent == "live":
            return "live_city"
    return _resolve_city_field(cond.field, query_text)


def _query_mentions_internship_scope(query_text: str) -> bool:
    return bool(_INTERNSHIP_SCOPE_HINT.search(query_text or ""))


def _query_mentions_work_scope(query_text: str) -> bool:
    return bool(_WORK_SCOPE_HINT.search(query_text or ""))


def _resolve_company_field_for_cond(
    cond: StructuredConditionIR,
    query_text: str,
) -> StructuredField:
    if cond.field != "company":
        return cond.field
    has_internship = _query_mentions_internship_scope(query_text)
    has_work = _query_mentions_work_scope(query_text)
    if has_internship and not has_work:
        return "internship_company"
    return "company"


def _resolve_position_field_for_cond(
    cond: StructuredConditionIR,
    query_text: str,
) -> StructuredField:
    if cond.field != "position":
        return cond.field
    has_internship = _query_mentions_internship_scope(query_text)
    has_work = _query_mentions_work_scope(query_text)
    if has_internship and not has_work:
        return "internship_job"
    return "position"


def _query_mentions_work_position(query_text: str) -> bool:
    q = query_text or ""
    if _NEGATED_WORK_EXPERIENCE_RE.search(q):
        return False
    return bool(_WORK_POSITION_HINT.search(q))


def _query_mentions_intention_position(query_text: str) -> bool:
    q = query_text or ""
    if _INTENTION_POSITION_HINT.search(q):
        return True
    return bool(_CAREER_CHANGE_HINT.search(q))


def _query_mentions_surname(query_text: str) -> bool:
    return bool(_SURNAME_HINT.search(query_text or ""))


def _query_mentions_exact_name(query_text: str) -> bool:
    return bool(_EXACT_NAME_HINT.search(query_text or ""))


def _query_mentions_name_contains(query_text: str) -> bool:
    return bool(_NAME_CONTAINS_HINT.search(query_text or ""))


def _query_mentions_name_suffix(query_text: str) -> bool:
    text = query_text or ""
    return "结尾" in text


def _compile_intention_position_leaf(
    cond: StructuredConditionIR,
    *,
    notes: list[str],
) -> dict[str, Any] | None:
    if not cond.required:
        notes.append("structured_to_soft:intention_position")
        return None
    clauses = _compile_intention_position_condition(cond)
    if not clauses:
        notes.append("structured_to_soft:intention_position")
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"bool": {"filter": clauses}}


def _compile_company_leaf(
    cond: StructuredConditionIR,
    *,
    query_text: str,
    tag_name_to_id: dict[str, int] | None,
    notes: list[str],
) -> dict[str, Any] | None:
    has_internship = _query_mentions_internship_scope(query_text)
    has_work = _query_mentions_work_scope(query_text)
    if has_internship and not has_work:
        return _compile_internship_company_leaf(
            cond.model_copy(update={"field": "internship_company"}),
            query_text=query_text,
            tag_name_to_id=tag_name_to_id,
            notes=notes,
        )
    if has_work and not has_internship:
        return _compile_work_company_leaf(
            cond,
            query_text=query_text,
            tag_name_to_id=tag_name_to_id,
            notes=notes,
        )

    work_clause = _compile_work_company_leaf(
        cond,
        query_text=query_text,
        tag_name_to_id=tag_name_to_id,
        notes=notes,
    )
    internship_clause = _compile_internship_company_leaf(
        cond.model_copy(update={"field": "internship_company"}),
        query_text=query_text,
        tag_name_to_id=tag_name_to_id,
        notes=notes,
    )
    clauses = [c for c in (work_clause, internship_clause) if c is not None]
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"bool": {"should": clauses, "minimum_should_match": 1}}


def _compile_work_company_leaf(
    cond: StructuredConditionIR,
    *,
    query_text: str,
    tag_name_to_id: dict[str, int] | None,
    notes: list[str],
) -> dict[str, Any] | None:
    policy = get_field_policy("company")
    if _should_compile_as_hard(cond, policy):
        clauses = _compile_hard_condition(
            cond,
            policy,
            tag_name_to_id=tag_name_to_id,
            query_text=query_text,
        )
    else:
        clauses = []
        notes.append("structured_to_soft:company")
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"bool": {"filter": clauses}}


def _compile_internship_company_leaf(
    cond: StructuredConditionIR,
    *,
    query_text: str,
    tag_name_to_id: dict[str, int] | None,
    notes: list[str],
) -> dict[str, Any] | None:
    policy = get_field_policy("internship_company")
    if _should_compile_as_hard(cond, policy):
        clauses = _compile_hard_condition(
            cond,
            policy,
            tag_name_to_id=tag_name_to_id,
            query_text=query_text,
        )
    else:
        clauses = []
        notes.append("structured_to_soft:internship_company")
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"bool": {"filter": clauses}}


def _compile_internship_position_leaf(
    cond: StructuredConditionIR,
    *,
    notes: list[str],
) -> dict[str, Any] | None:
    policy = get_field_policy("internship_job")
    if _should_compile_as_hard(cond, policy):
        clauses = _compile_hard_condition(cond, policy, query_text="")
    else:
        clauses = []
        notes.append("structured_to_soft:internship_job")
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"bool": {"filter": clauses}}


def _compile_work_position_leaf(
    cond: StructuredConditionIR,
    *,
    query_text: str,
    tag_name_to_id: dict[str, int] | None,
    notes: list[str],
    allow_intention_fallback: bool,
) -> dict[str, Any] | None:
    """Compile work-experience positions (work_experiences.job).

    When IR has no intention_position leaf, also compile intention filter if query
    mentions expected role (legacy single-position IR).
    """

    policy = get_field_policy("position")
    work_clauses: list[dict[str, Any]] = []
    intention_clauses: list[dict[str, Any]] = []

    want_work = _query_mentions_work_position(query_text)
    want_intention = _query_mentions_intention_position(query_text)
    if not want_work and not want_intention:
        want_work = True
    if not allow_intention_fallback:
        want_intention = False

    if want_work:
        if _should_compile_as_hard(cond, policy):
            work_clauses = _compile_hard_condition(
                cond,
                policy,
                tag_name_to_id=tag_name_to_id,
                query_text=query_text,
            )
        else:
            work_clauses = []
            notes.append("structured_to_soft:position")
    elif want_intention and allow_intention_fallback:
        notes.append("structured_to_soft:position")

    if want_intention and allow_intention_fallback:
        intention_clauses = _compile_intention_position_condition(cond)
        if intention_clauses:
            if want_work:
                notes.append("position_split_work_and_intention")
            else:
                notes.append("position_compiled_as_intention_only")

    clauses = work_clauses + intention_clauses
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"bool": {"filter": clauses}}


def _compile_intention_position_condition(cond: StructuredConditionIR) -> list[dict[str, Any]]:
    values = _string_values(cond.values)
    if not values:
        return []
    field = "intention.positions.name.keyword"
    if cond.operator == "all":
        clauses = [{"term": {field: value}} for value in values]
    elif len(values) == 1:
        clauses = [{"term": {field: values[0]}}]
    else:
        clauses = [{"terms": {field: values}}]
    return [_wrap_nested("intention.positions", clause) for clause in clauses]


def _case_fold_values(values: list[str]) -> list[str]:
    """按 casefold 去重；别名/大小写变体由本地别名展开负责。"""

    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        text = raw.strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _compile_cert_language_condition(
    cond: StructuredConditionIR,
    policy: FieldPolicy,
) -> list[dict[str, Any]]:
    """Match certificates / languages on keyword（与 skill 相同 DSL 形态，便于日后接 cert 图）。"""

    field = policy.keyword_field or policy.hard_field
    values = _case_fold_values(_string_values(cond.values))
    if not values:
        return []

    if cond.operator == "all":
        return [{"bool": {"filter": [{"term": {field: value}} for value in values]}}]

    if len(values) == 1:
        return [{"term": {field: values[0]}}]
    return [{"terms": {field: values}}]


def _compile_term_condition(
    cond: StructuredConditionIR,
    policy: FieldPolicy,
) -> list[dict[str, Any]]:
    field = policy.hard_field
    values = _string_values(cond.values)
    if not values:
        return []

    if cond.operator in {"all"}:
        return [{"term": {field: value}} for value in values]

    if len(values) == 1:
        return [{"term": {field: values[0]}}]
    return [{"terms": {field: values}}]


def _compile_text_condition(
    cond: StructuredConditionIR,
    policy: FieldPolicy,
) -> list[dict[str, Any]]:
    field = policy.es_field
    values = _string_values(cond.values)
    if not values:
        return []

    if cond.operator in {"all"}:
        return [{"match_phrase": {field: value}} for value in values]

    if len(values) == 1:
        return [{"match_phrase": {field: values[0]}}]
    return [
        {
            "bool": {
                "should": [{"match_phrase": {field: value}} for value in values],
                "minimum_should_match": 1,
            }
        }
    ]


def _compile_range_condition(
    cond: StructuredConditionIR,
    policy: FieldPolicy,
    *,
    query_text: str = "",
) -> list[dict[str, Any]]:
    if cond.field == "degree":
        return _compile_degree_condition(cond, policy)
    if cond.field == "expected_salary":
        return _compile_expected_salary_condition(cond, policy, query_text=query_text)

    field = policy.es_field
    if cond.operator == "range" and len(cond.values) == 2:
        return [{"range": {field: {"gte": cond.values[0], "lte": cond.values[1]}}}]
    if cond.operator == "gte":
        return [{"range": {field: {"gte": cond.values[0]}}}]
    if cond.operator == "lte":
        return [{"range": {field: {"lte": cond.values[0]}}}]
    if cond.operator in {"eq", "any", "all"}:
        values = list(cond.values)
        if len(values) == 1:
            return [{"term": {field: values[0]}}]
        return [{"terms": {field: values}}]
    return []


def _birth_year_int(value: str | int | float | bool) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        year = int(value)
    else:
        text = str(value).strip()
        if not text.isdigit() or len(text) != 4:
            return None
        year = int(text)
    return year if 1900 <= year <= 2100 else None


def _birth_year_date_range(year: int) -> dict[str, str]:
    return {"gte": f"{year}-01-01", "lte": f"{year}-12-31"}


def _compile_birth_year_condition(
    cond: StructuredConditionIR,
    policy: FieldPolicy,
) -> list[dict[str, Any]]:
    field = policy.es_field
    years = [_birth_year_int(value) for value in cond.values]
    years = [year for year in years if year is not None]
    if not years:
        return []

    if cond.operator == "range" and len(years) >= 2:
        low, high = sorted(years[:2])
        return [{"range": {field: {"gte": f"{low}-01-01", "lte": f"{high}-12-31"}}}]
    if cond.operator == "gte":
        return [{"range": {field: {"gte": f"{years[0]}-01-01"}}}]
    if cond.operator == "lte":
        return [{"range": {field: {"lte": f"{years[0]}-12-31"}}}]
    if cond.operator in {"eq", "any", "all"}:
        if len(years) == 1:
            return [{"range": {field: _birth_year_date_range(years[0])}}]
        return [
            {
                "bool": {
                    "should": [{"range": {field: _birth_year_date_range(year)}} for year in years],
                    "minimum_should_match": 1,
                }
            }
        ]
    return []


def _compile_work_state_condition(
    cond: StructuredConditionIR,
    policy: FieldPolicy,
    *,
    notes: list[str],
) -> list[dict[str, Any]]:
    values = _string_values(cond.values)
    if not values:
        return []

    codes = resolve_work_state_codes(values)
    field = policy.es_field
    if codes:
        fresh_graduate_code = "5"
        if fresh_graduate_code in codes:
            grad_year = datetime.now().year
            notes.append(f"work_state_redirect:fresh_graduate_to_graduation_year_eq_{grad_year}")
            grad_policy = get_field_policy("graduation_year")
            grad_cond = StructuredConditionIR(
                field="graduation_year",
                operator="eq",
                values=[grad_year],
                required=cond.required,
                weight=cond.weight,
            )
            grad_clauses = _compile_birth_year_condition(grad_cond, grad_policy)
            remaining = [c for c in codes if c != fresh_graduate_code]
            if not remaining:
                return grad_clauses
            notes.append(f"work_state_codes={','.join(remaining)}")
            if len(remaining) == 1:
                return grad_clauses + [{"term": {field: remaining[0]}}]
            return grad_clauses + [{"terms": {field: remaining}}]
        notes.append(f"work_state_codes={','.join(codes)}")
        if len(codes) == 1:
            return [{"term": {field: codes[0]}}]
        return [{"terms": {field: codes}}]

    name_field = "intention.work_state.name"
    notes.append("work_state_fallback=match_phrase")
    if cond.operator in {"all"}:
        return [{"match_phrase": {name_field: value}} for value in values]
    if len(values) == 1:
        return [{"match_phrase": {name_field: values[0]}}]
    return [
        {
            "bool": {
                "should": [{"match_phrase": {name_field: value}} for value in values],
                "minimum_should_match": 1,
            }
        }
    ]


def _compile_expected_salary_condition(
    cond: StructuredConditionIR,
    policy: FieldPolicy,
    *,
    query_text: str = "",
) -> list[dict[str, Any]]:
    field = policy.es_field
    values = list(cond.values)
    if not values:
        return []

    def _as_code(value: str | int | float | bool) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            v = int(value)
            return v if 1 <= v <= 10 else None
        text = str(value).strip()
        if text.isdigit():
            v = int(text)
            return v if 1 <= v <= 10 else None
        return None

    # 允许 LLM 直接给离散 code（1-10）
    direct_code_set: set[int] = set()
    for value in values:
        code = _as_code(value)
        if code is not None:
            direct_code_set.add(code)
    direct_codes = sorted(direct_code_set)
    if direct_codes:
        if len(direct_codes) == 1:
            return [{"term": {field: direct_codes[0]}}]
        return [{"terms": {field: direct_codes}}]

    # 对“期望薪资15K”这类无比较词表达，优先按目标薪资所属档位匹配，
    # 避免 parser 给 gte 时把 25k-35k 等高档位全部放进来。
    if query_text and cond.operator == "gte" and not _SALARY_GTE_HINT.search(query_text):
        anchor = _salary_anchor_codes(values, query_text=query_text)
        if anchor:
            if len(anchor) == 1:
                return [{"term": {field: anchor[0]}}]
            return [{"terms": {field: anchor}}]

    # 其他情况按严格策略把自然语言映射到 salary_code。
    # 对 parser 给出的纯数值（如 15000 + gte）补成表达式，避免被当作裸数字丢失。
    text = _salary_expr_text_from_condition(cond) or " ".join(_string_values(values))
    if cond.operator == "range":
        codes = resolve_salary_codes_range_overlap(text)
    else:
        codes = resolve_salary_codes_strict(text)
    if not codes:
        return []
    if len(codes) == 1:
        return [{"term": {field: codes[0]}}]
    return [{"terms": {field: codes}}]


def _salary_expr_text_from_condition(cond: StructuredConditionIR) -> str | None:
    values = _string_values(list(cond.values))
    if not values:
        return None
    if cond.operator == "gte" and len(values) == 1:
        return f"{values[0]}以上"
    if cond.operator == "lte" and len(values) == 1:
        return f"{values[0]}以下"
    if cond.operator == "range" and len(values) == 2:
        return f"{values[0]}-{values[1]}"
    return None


def _parse_oral_salary_count(text: str) -> float | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.isdigit() or re.fullmatch(r"\d+(?:\.\d+)?", raw):
        return float(raw)
    cn_map = {
        "零": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    if raw == "十":
        return 10.0
    if raw.startswith("十") and len(raw) > 1:
        tail = cn_map.get(raw[1:])
        return float(10 + (tail or 0))
    if raw.endswith("十"):
        head = cn_map.get(raw[:-1])
        return float((head or 1) * 10)
    if "十" in raw:
        left, _, right = raw.partition("十")
        hi = cn_map.get(left) if left else 1
        lo = cn_map.get(right) if right else 0
        if hi is None:
            return None
        return float(hi * 10 + (lo or 0))
    if len(raw) == 1 and raw in cn_map:
        return float(cn_map[raw])
    return None


def _oral_salary_amount_from_query(query_text: str) -> int | None:
    q = query_text or ""
    match = _ORAL_SALARY_WAN_RE.search(q)
    if match:
        count = _parse_oral_salary_count(match.group(1))
        if count is not None:
            return int(round(count * 10000))
    match = _ORAL_SALARY_QIAN_RE.search(q)
    if match:
        count = _parse_oral_salary_count(match.group(1))
        if count is not None:
            return int(round(count * 1000))
    return None


def _salary_anchor_codes(
    values: list[str | int | float | bool],
    *,
    query_text: str = "",
) -> list[int]:
    if len(values) != 1:
        return []
    oral = _oral_salary_amount_from_query(query_text)
    target = oral if oral is not None else _to_salary_amount(values[0], query_text=query_text)
    if target is None:
        return []
    bands = get_salary_bands()
    out = [band.code for band in bands if band.min_salary <= target <= band.max_salary]
    return sorted(set(out))


def _to_salary_amount(
    value: str | int | float | bool,
    *,
    query_text: str = "",
) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
    else:
        text = str(value).strip().lower()
        if not text:
            return None
        m = re.fullmatch(r"(\d+(?:\.\d+)?)(k|w|万)?", text)
        if not m:
            return None
        v = float(m.group(1))
        unit = (m.group(2) or "").lower()
        if unit == "k":
            return int(round(v * 1000))
        if unit in {"w", "万"}:
            return int(round(v * 10000))
        if v <= 1000:
            return int(round(v * 1000))
        if (
            1000 < v < 10000
            and float(v).is_integer()
            and int(v) % 1000 == 0
            and _oral_salary_amount_from_query(query_text) is None
        ):
            return int((v / 1000) * 10000)
        return int(round(v))
    if v <= 1000:
        return int(round(v * 1000))
    if (
        1000 < v < 10000
        and float(v).is_integer()
        and int(v) % 1000 == 0
        and _oral_salary_amount_from_query(query_text) is None
    ):
        return int((v / 1000) * 10000)
    return int(round(v))


def _compile_degree_condition(
    cond: StructuredConditionIR,
    policy: FieldPolicy,
) -> list[dict[str, Any]]:
    codes = [_degree_code(value) for value in cond.values]
    codes = [code for code in codes if code is not None]
    if not codes:
        return []

    if cond.operator == "gte":
        allowed = _degree_codes_gte(min(codes))
        return [{"terms": {policy.es_field: allowed}}]
    if cond.operator == "lte":
        allowed = [code for code in range(1, max(codes) + 1)]
        return [{"terms": {policy.es_field: allowed}}]
    if cond.operator == "range" and len(codes) == 2:
        low, high = sorted(codes)
        return [{"terms": {policy.es_field: [code for code in range(low, high + 1)]}}]
    if len(codes) == 1:
        return [{"term": {policy.es_field: codes[0]}}]
    return [{"terms": {policy.es_field: codes}}]


def _compile_talent_tag_condition(
    cond: StructuredConditionIR,
    *,
    tag_name_to_id: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Map canonical tag names to ES nested ``talent_tags.tag_id`` terms.

    Label resolution (prefix/query fallback) belongs in ``talent_tag_resolver``;
    this function only performs exact dictionary lookup plus explicit numeric ids.
    """
    values = _string_values(cond.values)
    if not values:
        return []

    tag_map = {
        str(name).strip().casefold(): int(tid) for name, tid in (tag_name_to_id or {}).items()
    }
    tag_ids: list[int] = []
    for value in values:
        raw = value.strip()
        if not raw:
            continue
        # 先按租户标签字典 name->id 解析（"985"/"211" 是常见“数字名”标签）。
        # 仅当字典无匹配时，才把纯数字当作显式 tag_id。
        mapped = tag_map.get(raw.casefold())
        if mapped is not None:
            tag_ids.append(mapped)
            continue
        if raw.isdigit():
            tag_ids.append(int(raw))

    uniq_ids = sorted(set(tag_ids))
    if not uniq_ids:
        return []

    field = "talent_tags.tag_id"
    if cond.operator == "all":
        return [{"term": {field: tag_id}} for tag_id in uniq_ids]
    if len(uniq_ids) == 1:
        return [{"term": {field: uniq_ids[0]}}]
    return [{"terms": {field: uniq_ids}}]


def _wrap_nested(path: str, query: dict[str, Any]) -> dict[str, Any]:
    return {"nested": {"path": path, "query": query}}


def _make_soft_condition(cond: StructuredConditionIR, policy: FieldPolicy) -> SoftCondition:
    text = cond.softText or (policy.soft_template or "{values}").format(
        values="或".join(_string_values(cond.values))
    )
    return SoftCondition(text=text, fields=list(policy.soft_fields), weight=cond.weight)


def _looks_like_cert_or_language_skill(value: str) -> bool:
    text = value.strip()
    lower = text.lower()
    if not text:
        return True
    blocked = {
        "ielts",
        "toefl",
        "cet-4",
        "cet4",
        "cet-6",
        "cet6",
        "tem-4",
        "tem4",
        "tem-8",
        "tem8",
        "gpa",
        "雅思",
        "托福",
        "英语四级",
        "英语六级",
        "专四",
        "专八",
    }
    return lower in blocked or text in blocked


def _should_compile_as_hard(cond: StructuredConditionIR, policy: FieldPolicy) -> bool:
    """Conservative hardening gate.

    Parser output is intentionally high-level. If it labels a broad direction
    as a structured field, the compiler should prefer recall over false hard
    filters.
    """

    if not policy.precise or not cond.required:
        return False
    values = _string_values(cond.values)
    if not values:
        return False
    if cond.field == "skill":
        # 技能统一走 soft + 向量/文本语义；简历技能标签不做 hard terms 过滤。
        return False
    return True


def _soft_conditions_for_structured_hint(
    cond: StructuredConditionIR,
    policy: FieldPolicy,
    *,
    add_default: bool = True,
) -> list[SoftCondition]:
    values = _string_values(cond.values)
    if not values:
        return []

    out: list[SoftCondition] = []
    if add_default and policy.add_soft_by_default and policy.soft_fields:
        out.append(_make_soft_condition(cond, policy))
    return out


def _is_atomic_skill(value: str) -> bool:
    text = value.strip()
    lower = text.lower()
    if not text:
        return False
    if any(token in lower for token in ("&", "/", " and ", "management", "contracting")):
        return False
    if " " in text and lower not in {"machine learning", "deep learning"}:
        return False
    return len(text) <= 32


def _string_values(values: list[str | int | float | bool]) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()]


def _degree_code(value: str | int | float | bool) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    return DEGREE_CODE_BY_NAME.get(text)


def _degree_codes_gte(code: int) -> list[int]:
    _LEVEL_CODES = (1, 2, 3, 4, 5, 6, 7, 10)
    return [c for c in _LEVEL_CODES if c >= code]


__all__ = [
    "compile_ir_to_hybrid_result",
]
