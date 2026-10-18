"""Deterministic synthetic corpus with exact oracle labels.

The bundled 16-candidate demo proves the loop *runs*; it cannot prove the loop
finds the right people, because "qualified" is decided by the agent's own judge
-- a circular metric. Real candidate data cannot ship, so the evaluation corpus
is generated instead: the generator knows every candidate's true ability set, so
ground truth is free, exact and reproducible.

Two expression styles are generated on purpose, because the project's premise is
that keyword search misses people whose ability shows up as project work rather
than as a skill tag:

- ``explicit``: the ability's evidence keywords sit on the resume (as skills).
- ``implicit``: the ability only shows up as project language that deliberately
  avoids that ability's own evidence keywords.

Keyword visibility is never *assumed* from the style label: :func:`visible_abilities`
re-scans the finished resume with the same evidence vocabulary the scripted judge
uses, so a phrase that accidentally leaks a keyword gets reclassified instead of
silently corrupting the benchmark.

Everything derives from one ``random.Random(seed)``: the same seed yields the same
corpus, so the reported numbers are reproducible offline with no API key.

Usage::

    from search_agent.corpus import generate_corpus
    from search_agent.tools.memory import InMemorySearchTool

    corpus = generate_corpus(size=120, seed=7)
    tool = InMemorySearchTool(corpus.records())
    for spec in corpus.queries:
        relevant = corpus.relevant(spec)         # ground truth
        semantic = corpus.semantic_only(spec)    # keyword search cannot reach these
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

from search_agent.demo import ABILITY_LIBRARY

_CORPUS_END_DATE = date(2026, 9, 1)

__all__ = [
    "ABILITY_SKILLS",
    "DEFAULT_QUERIES",
    "EVIDENCE_HINTS",
    "EXPLICIT",
    "IMPLICIT",
    "QuerySpec",
    "SyntheticCorpus",
    "generate_corpus",
    "load_corpus",
    "visible_abilities",
    "write_corpus",
]

EXPLICIT = "explicit"
IMPLICIT = "implicit"

#: Evidence keywords per ability, taken from the scripted LLM's own library so the
#: generator, the benchmark and the offline judge agree on what "keyword evidence" is.
EVIDENCE_HINTS: dict[str, tuple[str, ...]] = {
    str(entry["name"]): tuple(str(hint).lower() for hint in entry["hints"])
    for entry in ABILITY_LIBRARY
}

#: Skill tags attached when an ability is expressed explicitly. Every tuple carries at
#: least one of *its own* evidence keywords, and none of another ability's -- otherwise
#: an explicit skill would leak an ability that some other resume expresses implicitly.
#: ``tests/test_corpus.py`` pins both halves of that invariant.
ABILITY_SKILLS: dict[str, tuple[str, ...]] = {
    "高并发系统设计": ("高并发", "Redis", "性能优化"),
    "微服务架构": ("微服务", "Spring Cloud", "服务治理"),
    "分布式系统": ("分布式", "Kafka", "分布式事务"),
    "推荐系统": ("召回", "排序", "CTR"),
    "数据工程": ("Spark", "Flink", "数据仓库"),
    "机器学习": ("机器学习", "PyTorch", "模型训练"),
    "系统架构设计": ("架构设计", "容量规划", "高可用"),
    "服务端开发": ("Java", "Go", "Python"),
}

#: Project language used for implicit expression. Each phrase describes the ability
#: without containing any of its evidence keywords (asserted by tests).
IMPLICIT_PROJECTS: dict[str, tuple[str, ...]] = {
    "高并发系统设计": (
        "主导订单交易链路的容量治理与洪峰压测，支撑日均十亿级请求",
        "负责支付网关的重构与压测体系搭建，线上抖动显著下降",
    ),
    "微服务架构": (
        "推动订单域从单体拆分到独立部署的多个服务，统一了接口契约",
        "负责服务注册发现与链路追踪体系的落地",
    ),
    "分布式系统": (
        "负责跨机房数据同步与故障切换演练，保障机房级容灾能力",
        "主导链路追踪与幂等补偿机制的设计",
    ),
    "推荐系统": (
        "负责信息流内容分发策略的迭代，人均使用时长提升明显",
        "搭建用户兴趣画像与在线打分服务，支撑亿级请求的实时决策",
    ),
    "数据工程": (
        "搭建离线与准实时一体的加工链路，支撑每日百亿条记录的处理",
        "负责指标口径治理与批流一体任务调度体系",
    ),
    "机器学习": (
        "负责点击率预估模型的迭代与离线评估体系",
        "主导特征体系与在线推理链路的工程化落地",
    ),
    "系统架构设计": (
        "主导核心链路的容灾演练与资源评估体系建设",
        "负责跨团队技术方案评审与中长期演进路线设计",
    ),
    "服务端开发": (
        "负责核心业务系统的研发与迭代",
        "主导交易链路的重构与日常需求交付",
    ),
}

#: Job titles used when a role's own title would leak an implicit ability.
IMPLICIT_TITLES: dict[str, tuple[str, ...]] = {
    "服务端开发": ("业务系统研发工程师", "交易链路开发工程师"),
    "系统架构设计": ("技术方案负责人", "平台研发负责人"),
}

_NEUTRAL_CLAUSES = (
    "有完整的项目交付与协作经验",
    "熟悉从需求拆解到上线的完整流程",
    "具备跨团队推进与技术方案落地的经验",
)

_BASE_SKILLS = ("MySQL", "Linux", "Git", "Docker")

_NAMES = (
    "张伟",
    "李娜",
    "王强",
    "刘洋",
    "陈杰",
    "杨敏",
    "赵磊",
    "黄婷",
    "周涛",
    "吴静",
    "徐鹏",
    "孙丽",
    "马超",
    "朱琳",
    "胡军",
    "郭敏",
    "何勇",
    "高翔",
    "林芳",
    "罗晨",
    "郑凯",
    "梁雪",
    "谢峰",
    "许诺",
    "韩磊",
    "唐悦",
    "冯雪",
    "曹阳",
    "彭飞",
    "董晴",
    "袁野",
    "苏航",
    "蒋欣",
    "蔡明",
    "潘婷",
    "杜鹏",
    "戴宁",
    "范鑫",
    "汪洋",
    "石磊",
)

_COMPANIES = (
    "字节跳动",
    "腾讯",
    "阿里",
    "美团",
    "百度",
    "京东",
    "快手",
    "网易",
    "滴滴",
    "小米",
    "某头部电商平台",
    "某金融科技公司",
    "某 SaaS 公司",
    "某出行平台",
    "某物流公司",
    "某创业公司",
    "某证券机构",
    "某零售集团",
)

_SCHOOLS = (
    "清华大学",
    "北京大学",
    "上海交通大学",
    "浙江大学",
    "复旦大学",
    "南京大学",
    "华中科技大学",
    "武汉大学",
    "中山大学",
    "同济大学",
    "北京邮电大学",
    "电子科技大学",
    "华南理工大学",
    "北京理工大学",
    "西安电子科技大学",
    "深圳大学",
)

_MAJORS = ("计算机科学与技术", "软件工程", "信息工程", "自动化", "通信工程", "数据科学")

_CITIES = ("北京", "上海", "深圳", "杭州", "广州", "成都", "武汉", "南京")

#: Fields scanned for keyword evidence -- a superset of what the judge reads, so
#: "keyword visible" stays a conservative upper bound.
_BLOB_FIELDS = (
    "name",
    "summary",
    "skills",
    "workExperiences",
    "educations",
    "certificates",
    "expectedPositions",
)


@dataclass(frozen=True)
class QuerySpec:
    """One benchmark query plus the abilities a perfect system must find.

    ``required_abilities`` is asserted against the scripted LLM's understand stage
    in the tests, so the oracle and the offline agent cannot drift apart unnoticed.
    """

    text: str
    required_abilities: tuple[str, ...]
    min_years: int = 0
    role_title: str = "工程师"


DEFAULT_QUERIES: tuple[QuerySpec, ...] = (
    QuerySpec(
        "找 3 年以上经验的后端工程师，做过高并发系统",
        ("服务端开发", "高并发系统设计"),
        3,
        "后端工程师",
    ),
    QuerySpec(
        "招聘 5 年以上的后端工程师，熟悉微服务与分布式系统",
        ("服务端开发", "微服务架构", "分布式系统"),
        5,
        "后端工程师",
    ),
    QuerySpec(
        "推荐算法工程师，要求 3 年以上推荐系统与机器学习经验",
        ("推荐系统", "机器学习"),
        3,
        "算法工程师",
    ),
    QuerySpec(
        "数据工程师，熟悉 Spark 和 Flink，做过数据平台架构，3 年以上经验",
        ("数据工程", "系统架构设计"),
        3,
        "数据工程师",
    ),
    QuerySpec(
        "系统架构师，8 年以上经验，负责过分布式系统与高并发系统",
        ("系统架构设计", "分布式系统", "高并发系统设计"),
        8,
        "系统架构师",
    ),
    QuerySpec(
        "找后端工程师，做过微服务，最好来自大厂",
        ("服务端开发", "微服务架构"),
        0,
        "后端工程师",
    ),
)

#: Candidate archetypes. The first six mirror a benchmark query exactly; the rest are
#: near misses (one ability short, or a different mix) so relevance stays a small slice
#: of the corpus and precision means something -- a corpus where a third of everyone is
#: relevant would make every recall number look fine.
_ARCHETYPES: tuple[tuple[str, ...], ...] = (
    ("服务端开发", "高并发系统设计"),
    ("服务端开发", "微服务架构", "分布式系统"),
    ("推荐系统", "机器学习"),
    ("数据工程", "系统架构设计"),
    ("系统架构设计", "分布式系统", "高并发系统设计"),
    ("服务端开发", "微服务架构"),
    ("服务端开发",),
    ("推荐系统",),
    ("机器学习",),
    ("微服务架构",),
    ("分布式系统", "高并发系统设计"),
    ("数据工程",),
    ("系统架构设计", "微服务架构"),
    ("服务端开发", "分布式系统"),
)

_ALL_ABILITIES: tuple[str, ...] = tuple(EVIDENCE_HINTS)


def _resume_blob(record: dict[str, Any]) -> str:
    """Every text field of a resume, lowercased."""
    return " ".join(str(record.get(field, "")) for field in _BLOB_FIELDS).lower()


def visible_abilities(record: dict[str, Any], abilities: Iterable[str]) -> frozenset[str]:
    """Abilities that carry keyword evidence on the finished resume.

    Deliberately scans *more* fields than the judge does, so anything a keyword or
    skill filter could reach counts as visible.
    """
    blob = _resume_blob(record)
    return frozenset(
        ability
        for ability in abilities
        if any(hint in blob for hint in EVIDENCE_HINTS.get(ability, ()))
    )


@dataclass(frozen=True)
class SyntheticCorpus:
    """A generated corpus plus the labels no real dataset can hand you."""

    seed: int
    candidates: tuple[dict[str, Any], ...]
    queries: tuple[QuerySpec, ...]
    #: doc_id -> true abilities (the oracle)
    abilities: dict[str, frozenset[str]]
    #: doc_id -> abilities the generator expressed in project language
    implicit: dict[str, frozenset[str]]
    #: doc_id -> abilities a keyword filter can actually reach
    visible: dict[str, frozenset[str]]
    #: doc_id -> years of experience
    years: dict[str, float]

    @property
    def size(self) -> int:
        return len(self.candidates)

    def records(self) -> list[dict[str, Any]]:
        """Candidate records in the shape every ``SearchTool`` backend accepts."""
        return [dict(record) for record in self.candidates]

    def relevant(self, spec: QuerySpec) -> tuple[str, ...]:
        """Oracle: everyone who truly has the abilities and meets the stated bar."""
        required = set(spec.required_abilities)
        return tuple(
            doc_id
            for doc_id, abilities in self.abilities.items()
            if required <= abilities and self.years[doc_id] >= spec.min_years
        )

    def keyword_visible(self, spec: QuerySpec) -> tuple[str, ...]:
        """Relevant candidates whose required abilities all carry keyword evidence."""
        required = set(spec.required_abilities)
        return tuple(doc_id for doc_id in self.relevant(spec) if required <= self.visible[doc_id])

    def semantic_only(self, spec: QuerySpec) -> tuple[str, ...]:
        """Relevant candidates that a keyword filter structurally cannot reach."""
        visible = set(self.keyword_visible(spec))
        return tuple(doc_id for doc_id in self.relevant(spec) if doc_id not in visible)


def _base_title(abilities: set[str]) -> str:
    """Human-readable role title derived from the ability mix."""
    if abilities & {"推荐系统", "机器学习"}:
        return "算法工程师"
    if "数据工程" in abilities:
        return "数据工程师"
    if "系统架构设计" in abilities:
        return "系统架构师"
    if "服务端开发" in abilities:
        return "后端工程师"
    return "研发工程师"


def _pick_title(rng: random.Random, base_title: str, implicit: set[str]) -> str:
    """The role title must not leak an ability this resume expresses implicitly."""
    lowered = base_title.lower()
    leaks = any(hint in lowered for ability in implicit for hint in EVIDENCE_HINTS.get(ability, ()))
    if leaks:
        safe = [title for ability in sorted(implicit) for title in IMPLICIT_TITLES.get(ability, ())]
        if safe:
            return rng.choice(safe)
    return base_title


def _shift_months(anchor: date, months: int) -> date:
    month_index = anchor.year * 12 + anchor.month - 1 + months
    year, month = divmod(month_index, 12)
    return date(year, month + 1, 1)


def _experience_dates(years: float, count: int) -> list[tuple[str, str]]:
    total_months = max(0, int(round(years * 12)))
    if count <= 1:
        start = _shift_months(_CORPUS_END_DATE, -total_months)
        return [(start.isoformat(), _CORPUS_END_DATE.isoformat())]
    older_months = total_months // 2
    newer_months = total_months - older_months
    older_start = _shift_months(_CORPUS_END_DATE, -total_months)
    boundary = _shift_months(_CORPUS_END_DATE, -newer_months)
    return [
        (older_start.isoformat(), boundary.isoformat()),
        (boundary.isoformat(), _CORPUS_END_DATE.isoformat()),
    ]


def _make_candidate(
    rng: random.Random,
    doc_id: str,
    name: str,
    archetype: tuple[str, ...],
    queries: Sequence[QuerySpec],
) -> tuple[dict[str, Any], frozenset[str], frozenset[str], float]:
    """Build one resume; returns (record, true abilities, implicit abilities, years)."""
    abilities = set(archetype)
    if rng.random() < 0.15:
        abilities.add(rng.choice(_ALL_ABILITIES))

    matching = [spec for spec in queries if set(spec.required_abilities) <= abilities]
    floor = max((spec.min_years for spec in matching), default=0)
    if matching and rng.random() < 0.8:
        years = float(floor + rng.randint(0, 4))
    else:
        years = float(rng.randint(1, 12))

    implicit: set[str] = set()
    skills: list[str] = []
    phrases: list[str] = []
    for ability in sorted(abilities):
        if rng.random() < 0.42:
            implicit.add(ability)
            phrases.append(rng.choice(IMPLICIT_PROJECTS[ability]))
        else:
            skills.extend(ABILITY_SKILLS[ability])

    title = _pick_title(rng, _base_title(abilities), implicit)
    if rng.random() < 0.6:
        skills.extend(_BASE_SKILLS)
    deduped = list(dict.fromkeys(skills))
    rng.shuffle(deduped)

    clauses = [rng.choice(_NEUTRAL_CLAUSES), *phrases]
    summary = f"{years:g} 年{title}经验，" + "；".join(clauses) + "。"

    degree = "硕士" if rng.random() < 0.45 else "本科"
    companies = rng.sample(_COMPANIES, rng.randint(1, 2))
    dates = _experience_dates(years, len(companies))
    record: dict[str, Any] = {
        "docId": doc_id,
        "name": name,
        "workYears": years,
        "currentResidence": rng.choice(_CITIES),
        "age": int(22 + years + (2 if degree == "硕士" else 0) + rng.randint(0, 3)),
        "summary": summary,
        "skills": [{"name": skill} for skill in deduped[:8]],
        "workExperiences": [{"company_name": company, "job_title": title} for company in companies],
        "educations": [
            {"school": rng.choice(_SCHOOLS), "major": rng.choice(_MAJORS), "degree": degree}
        ],
        "certificates": [{"name": "PMP 项目管理认证"}] if rng.random() < 0.15 else [],
        "expectedPositions": [title],
    }
    for work, (start_date, end_date) in zip(record["workExperiences"], dates, strict=True):
        work["startDate"] = start_date
        work["endDate"] = end_date
    return record, frozenset(abilities), frozenset(implicit), years


def generate_corpus(
    size: int = 120,
    seed: int = 7,
    queries: Sequence[QuerySpec] | None = None,
) -> SyntheticCorpus:
    """Generate ``size`` candidates round-robin over ``queries`` (deterministic)."""
    if size < 1:
        raise ValueError("size must be >= 1")
    specs = tuple(queries) if queries else DEFAULT_QUERIES
    if not specs:
        raise ValueError("at least one query spec is required")

    rng = random.Random(seed)
    names = list(_NAMES)
    rng.shuffle(names)

    candidates: list[dict[str, Any]] = []
    abilities_map: dict[str, frozenset[str]] = {}
    implicit_map: dict[str, frozenset[str]] = {}
    visible_map: dict[str, frozenset[str]] = {}
    years_map: dict[str, float] = {}

    for index in range(size):
        archetype = _ARCHETYPES[index % len(_ARCHETYPES)]
        doc_id = f"g{index + 1:03d}"
        record, abilities, implicit, years = _make_candidate(
            rng, doc_id, names[index % len(names)], archetype, specs
        )
        candidates.append(record)
        abilities_map[doc_id] = abilities
        implicit_map[doc_id] = implicit
        visible_map[doc_id] = visible_abilities(record, abilities)
        years_map[doc_id] = years

    return SyntheticCorpus(
        seed=seed,
        candidates=tuple(candidates),
        queries=specs,
        abilities=abilities_map,
        implicit=implicit_map,
        visible=visible_map,
        years=years_map,
    )


def write_corpus(corpus: SyntheticCorpus, directory: str | Path) -> tuple[Path, Path]:
    """Write ``candidates.jsonl`` + ``oracle.json``; returns both paths."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    candidates_path = target / "candidates.jsonl"
    oracle_path = target / "oracle.json"

    with candidates_path.open("w", encoding="utf-8") as handle:
        for record in corpus.candidates:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    payload = {
        "seed": corpus.seed,
        "size": corpus.size,
        "queries": [
            {
                "text": spec.text,
                "requiredAbilities": list(spec.required_abilities),
                "minYears": spec.min_years,
                "roleTitle": spec.role_title,
            }
            for spec in corpus.queries
        ],
        "labels": {
            doc_id: {
                "abilities": sorted(corpus.abilities[doc_id]),
                "implicit": sorted(corpus.implicit[doc_id]),
                "visible": sorted(corpus.visible[doc_id]),
                "years": corpus.years[doc_id],
            }
            for doc_id in corpus.abilities
        },
    }
    oracle_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return candidates_path, oracle_path


def load_corpus(directory: str | Path) -> SyntheticCorpus:
    """Read back a corpus written by :func:`write_corpus`."""
    target = Path(directory)
    records = tuple(
        json.loads(line)
        for line in (target / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    payload = json.loads((target / "oracle.json").read_text(encoding="utf-8"))
    specs = tuple(
        QuerySpec(
            text=str(entry["text"]),
            required_abilities=tuple(entry["requiredAbilities"]),
            min_years=int(entry.get("minYears") or 0),
            role_title=str(entry.get("roleTitle") or "工程师"),
        )
        for entry in payload["queries"]
    )
    labels = payload["labels"]
    return SyntheticCorpus(
        seed=int(payload["seed"]),
        candidates=records,
        queries=specs,
        abilities={key: frozenset(value["abilities"]) for key, value in labels.items()},
        implicit={key: frozenset(value["implicit"]) for key, value in labels.items()},
        visible={key: frozenset(value["visible"]) for key, value in labels.items()},
        years={key: float(value["years"]) for key, value in labels.items()},
    )
