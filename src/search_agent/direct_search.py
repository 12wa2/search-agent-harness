'''Direct-search routing and deterministic fallback for explicit queries.

The router recognises a narrow set of recruiting queries with explicit
structured signals. Those queries bypass the agent harness and use the Hybrid
V2 NL-to-IR parser before a single ``SearchTool`` call. The deterministic plan
in this module is still useful for the routing gate and as an observable
fallback when the parser provider is unavailable.

The gate is intentionally conservative: ambiguous, preference-heavy, or
transferable-ability queries stay on the reflective agent route.
'''

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any

from search_agent.models.artifacts import JobAbility, JobProfile, TalentProfile
from search_agent.models.ir import (
    CandidateSearchIR,
    IRMeta,
    SemanticConditionIR,
    StructuredConditionIR,
    WhereNode,
)

DIRECT_SEARCH_ROUTE = "direct_search"

_BLOCK_RE = re.compile(
    r"可迁移|跨行业|跨领域|行业不限|背景不限|转型|转岗|"
    r"高并发|分布式|微服务|架构设计|系统设计|重点考察|项目经验|落地经验|"
    r"数据平台|批流|推荐系统|召回排序|模型训练|"
    r"最好|优先|加分|或者|做过|看过",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(
    r"[?？]|什么|是什么|怎么|如何|为什么|哪些|介绍|区别|解释",
    re.IGNORECASE,
)
_SEARCH_ACTION_RE = re.compile(
    r"找|寻找|搜索|搜|查询|招聘|招|推荐|筛选|匹配|物色|寻访|"
    r"find|search|look\s*for|hire|recruit|source",
    re.IGNORECASE,
)
_ROLE_RE = re.compile(
    r"(?:(?:后端|前端|全栈|服务端|客户端|数据|算法|测试|运维|安全|"
    r"机器学习|深度学习|AI|推荐|搜索|大数据|游戏|音视频|产品|项目)\s*)?"
    r"(?:开发工程师|工程师|架构师|设计师|制作人|剪辑师|导演|研究员|"
    r"科学家|分析师|经理|主管|专家|顾问|运营|销售|财务|人事)",
    re.IGNORECASE,
)

_SKILL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        ("Python", r"(?<![a-z0-9])python(?![a-z0-9])"),
        ("Java", r"(?<![a-z0-9])java(?![a-z0-9])"),
        ("JavaScript", r"(?<![a-z0-9])javascript(?![a-z0-9])"),
        ("TypeScript", r"(?<![a-z0-9])typescript(?![a-z0-9])"),
        ("Go", r"(?<![a-z0-9])(?:golang|go)(?![a-z0-9])"),
        ("C++", r"c\+\+"),
        ("C#", r"c#"),
        ("Rust", r"(?<![a-z0-9])rust(?![a-z0-9])"),
        ("SQL", r"(?<![a-z0-9])sql(?![a-z0-9])"),
        ("React", r"(?<![a-z0-9])react(?![a-z0-9])"),
        ("Vue", r"(?<![a-z0-9])vue(?![a-z0-9])"),
        ("Node.js", r"(?<![a-z0-9])node(?:\.js)?(?![a-z0-9])"),
        ("Django", r"(?<![a-z0-9])django(?![a-z0-9])"),
        ("Flask", r"(?<![a-z0-9])flask(?![a-z0-9])"),
        ("FastAPI", r"(?<![a-z0-9])fastapi(?![a-z0-9])"),
        ("Spring", r"(?<![a-z0-9])spring(?![a-z0-9])"),
        ("Kubernetes", r"(?<![a-z0-9])kubernetes(?![a-z0-9])"),
        ("Docker", r"(?<![a-z0-9])docker(?![a-z0-9])"),
        ("Spark", r"(?<![a-z0-9])spark(?![a-z0-9])"),
        ("Flink", r"(?<![a-z0-9])flink(?![a-z0-9])"),
        ("PyTorch", r"(?<![a-z0-9])pytorch(?![a-z0-9])"),
        ("TensorFlow", r"(?<![a-z0-9])tensorflow(?![a-z0-9])"),
        ("MySQL", r"(?<![a-z0-9])mysql(?![a-z0-9])"),
        ("Redis", r"(?<![a-z0-9])redis(?![a-z0-9])"),
        ("Kafka", r"(?<![a-z0-9])kafka(?![a-z0-9])"),
        ("Hadoop", r"(?<![a-z0-9])hadoop(?![a-z0-9])"),
        ("Hive", r"(?<![a-z0-9])hive(?![a-z0-9])"),
        ("ClickHouse", r"(?<![a-z0-9])clickhouse(?![a-z0-9])"),
    )
)

_CITY_NAMES = (
    "北京", "上海", "深圳", "广州", "杭州", "成都", "南京", "武汉",
    "西安", "苏州", "天津", "重庆", "长沙", "郑州", "青岛", "厦门",
    "合肥", "福州", "济南", "大连", "沈阳", "宁波", "无锡", "东莞",
    "佛山", "珠海", "香港", "新加坡",
)

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_YEAR_RANGE_RE = re.compile(
    r"(?P<low>\d+(?:\.\d+)?|[一二两三四五六七八九十]+)\s*"
    r"[-~～至到]\s*(?P<high>\d+(?:\.\d+)?|[一二两三四五六七八九十]+)\s*年"
)
_YEAR_MIN_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?|[一二两三四五六七八九十]+)\s*"
    r"(?:年(?:以上|及以上|经验|工作经验|工作经历|起)?|years?|yrs?)",
    re.IGNORECASE,
)

@dataclass(frozen=True)
class DirectSearchPlan:
    '''Everything needed to execute one deterministic search.'''

    query: str
    title: str
    skills: tuple[str, ...]
    role: str | None
    cities: tuple[str, ...]
    years: tuple[float, float] | None
    intent: CandidateSearchIR
    job_profile: JobProfile
    talent_profile: TalentProfile
    ir_source: str = "local_rules"
    parser_attempts: int = 0
    parser_error: str | None = None

    def with_intent(
        self,
        intent: CandidateSearchIR,
        *,
        ir_source: str,
        parser_attempts: int = 0,
        parser_error: str | None = None,
    ) -> "DirectSearchPlan":
        """Return the presentation plan backed by the IR actually searched."""
        conditions = _flatten_conditions(intent.where)
        title = _title_from_ir(intent) or self.title
        skills = _values_for_field(intent.where, "skill")
        role_values = _values_for_fields(
            intent.where, "position", "intention_position", "latest_work_job"
        )
        role = role_values[0] if role_values else self.role
        city_values = _values_for_fields(intent.where, "city", "intention_city", "live_city")
        cities = tuple(city_values) if city_values else self.cities
        abilities = [
            JobAbility(name=skill, level="must", evidence_hints=[skill])
            for skill in skills
        ]
        if not abilities:
            abilities = list(self.job_profile.must_abilities)
        job = self.job_profile.model_copy(
            update={
                "title": title,
                "must_abilities": abilities,
                "hard_constraints": conditions,
                "difficulty_reason": (
                    "Hybrid V2 parser produced CandidateSearchIR; the agent "
                    "orchestration stages were bypassed."
                ),
            }
        )
        talent = self.talent_profile.model_copy(
            update={
                "must_keywords": list(dict.fromkeys([*skills, *self.talent_profile.must_keywords])),
                "soft_signals": list(intent.semantic),
            }
        )
        return replace(
            self,
            title=title,
            skills=tuple(skills),
            role=role,
            cities=cities,
            intent=intent,
            job_profile=job,
            talent_profile=talent,
            ir_source=ir_source,
            parser_attempts=parser_attempts,
            parser_error=parser_error,
        )

    def event_payload(self) -> dict[str, Any]:
        return {
            "jobTitle": self.title,
            "mustAbilities": [ability.name for ability in self.job_profile.must_abilities],
            "hardConstraints": len(self.job_profile.hard_constraints),
            "hardConstraintDetails": [
                condition.model_dump() for condition in self.job_profile.hard_constraints
            ],
            "skills": list(self.skills),
            "role": self.role,
            "cities": list(self.cities),
            "years": list(self.years) if self.years else None,
            "ir": self.intent.model_dump(),
            "irSource": self.ir_source,
            "parserAttempts": self.parser_attempts,
            "parserError": self.parser_error,
        }


def _flatten_conditions(node: WhereNode) -> list[StructuredConditionIR]:
    if node.type == "leaf" and node.condition is not None:
        return [node.condition]
    conditions: list[StructuredConditionIR] = []
    if node.child is not None:
        conditions.extend(_flatten_conditions(node.child))
    for child in node.children:
        conditions.extend(_flatten_conditions(child))
    return conditions


def _values_for_field(node: WhereNode, field: str) -> list[str]:
    values: list[str] = []
    for condition in _flatten_conditions(node):
        if condition.field != field:
            continue
        for value in condition.values:
            text = str(value).strip()
            if text and text not in values:
                values.append(text)
    return values


def _values_for_fields(node: WhereNode, *fields: str) -> list[str]:
    values: list[str] = []
    for condition in _flatten_conditions(node):
        if condition.field not in fields:
            continue
        for value in condition.values:
            text = str(value).strip()
            if text and text not in values:
                values.append(text)
    return values


def _title_from_ir(intent: CandidateSearchIR) -> str | None:
    for field in ("position", "intention_position", "latest_work_job"):
        values = _values_for_field(intent.where, field)
        if values:
            return values[0]
    skills = _values_for_field(intent.where, "skill")
    if skills:
        return f"{skills[0]} 工程师"
    return None


def _normalize(query: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(query or "")).split())


def _chinese_number(raw: str) -> float | None:
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        return float(raw)
    if raw == "十":
        return 10.0
    if "十" in raw:
        tens, ones = raw.split("十", 1)
        tens_value = _CN_DIGITS.get(tens, 1) if tens else 1
        ones_value = _CN_DIGITS.get(ones, 0) if ones else 0
        return float(tens_value * 10 + ones_value)
    return float(_CN_DIGITS[raw]) if raw in _CN_DIGITS else None


def _extract_years(text: str) -> tuple[float, float] | None:
    match = _YEAR_RANGE_RE.search(text)
    if match:
        low = _chinese_number(match.group("low"))
        high = _chinese_number(match.group("high"))
        if low is not None and high is not None and 0 <= low <= high <= 50:
            return low, high
    match = _YEAR_MIN_RE.search(text)
    if match:
        value = _chinese_number(match.group("value"))
        if value is not None and 0 <= value <= 50:
            return value, value
    return None


def _extract_skills(text: str) -> tuple[str, ...]:
    hits: list[tuple[int, str]] = []
    for name, pattern in _SKILL_PATTERNS:
        for match in pattern.finditer(text):
            hits.append((match.start(), name))
    ordered: list[str] = []
    seen: set[str] = set()
    for _, name in sorted(hits):
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            ordered.append(name)
    return tuple(ordered)


def _extract_role(text: str) -> str | None:
    match = _ROLE_RE.search(text)
    return match.group(0).strip() if match else None


def _extract_cities(text: str) -> tuple[str, ...]:
    return tuple(city for city in _CITY_NAMES if city in text)


def should_use_direct_search(query: str) -> bool:
    '''Return True only for explicit, structured, low-uncertainty searches.'''
    text = _normalize(query)
    if not text or len(text) > 160:
        return False
    if _QUESTION_RE.search(text) or _BLOCK_RE.search(text):
        return False
    skills = _extract_skills(text)
    role = _extract_role(text)
    cities = _extract_cities(text)
    years = _extract_years(text)
    has_signal = bool(skills or role or cities or years)
    if not has_signal:
        return False
    has_action = bool(_SEARCH_ACTION_RE.search(text))
    # A bare role / tech phrase such as "Python 工程师" is still a clear search.
    # A location-only follow-up such as "那北京的呢" must stay in the agent so
    # it can use conversation history.
    return has_action or bool(role and (skills or years or cities))

def build_direct_search_plan(query: str) -> DirectSearchPlan | None:
    '''Parse a supported query into IR; return None when the agent should run.'''
    normalized = _normalize(query)
    if not should_use_direct_search(normalized):
        return None

    skills = _extract_skills(normalized)
    role = _extract_role(normalized)
    cities = _extract_cities(normalized)
    years = _extract_years(normalized)

    conditions: list[StructuredConditionIR] = []
    for skill in skills:
        conditions.append(
            StructuredConditionIR(field="skill", operator="any", values=[skill])
        )
    if years is not None:
        low, high = years
        if low == high:
            conditions.append(
                StructuredConditionIR(field="work_years", operator="gte", values=[low])
            )
        else:
            conditions.append(
                StructuredConditionIR(
                    field="work_years", operator="range", values=[low, high]
                )
            )
    if cities:
        conditions.append(
            StructuredConditionIR(field="city", operator="any", values=list(cities))
        )
    if not conditions:
        conditions.append(
            StructuredConditionIR(field="work_years", operator="gte", values=[0])
        )

    leaves = [WhereNode(type="leaf", condition=condition) for condition in conditions]
    where = leaves[0] if len(leaves) == 1 else WhereNode(type="and", children=leaves)

    semantic: list[SemanticConditionIR] = []
    seen_semantic: set[str] = set()

    def add_semantic(text: str, fields: list[str], weight: float) -> None:
        value = text.strip()
        key = value.casefold()
        if value and key not in seen_semantic:
            seen_semantic.add(key)
            semantic.append(SemanticConditionIR(text=value, fields=fields, weight=weight))

    add_semantic(normalized, ["work_vector", "project_vector", "skills_vector"], 1.0)
    if role:
        add_semantic(role, ["work_vector", "project_vector"], 1.2)
    for skill in skills:
        add_semantic(skill, ["skills_vector", "project_vector"], 1.3)

    title = role or (f"{skills[0]} 工程师" if skills else "候选人")
    if title == "工程师" and skills:
        title = f"{skills[0]} 工程师"
    abilities = [
        JobAbility(name=skill, level="must", evidence_hints=[skill]) for skill in skills
    ]
    if not abilities and role:
        abilities = [JobAbility(name=role, level="must", evidence_hints=[role])]

    job = JobProfile(
        raw_query=normalized,
        title=title,
        must_abilities=abilities,
        hard_constraints=conditions,
        subjective_difficulty="low",
        difficulty_reason="Deterministic direct-search parser matched explicit fields.",
    )
    talent = TalentProfile(
        must_keywords=list(dict.fromkeys([*skills, *([role] if role else [])])),
        soft_signals=semantic,
    )
    intent = CandidateSearchIR(
        where=where,
        semantic=semantic,
        meta=IRMeta(notes=["direct_search:deterministic_parse"]),
    )
    return DirectSearchPlan(
        query=normalized,
        title=title,
        skills=skills,
        role=role,
        cities=cities,
        years=years,
        intent=intent,
        job_profile=job,
        talent_profile=talent,
    )


__all__ = [
    "DIRECT_SEARCH_ROUTE",
    "DirectSearchPlan",
    "build_direct_search_plan",
    "should_use_direct_search",
]
