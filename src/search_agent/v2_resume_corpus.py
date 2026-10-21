"""Deterministic V2-native synthetic resume corpus.

The original benchmark corpus intentionally kept resumes short. This module
owns the richer benchmark that exercises every text channel used by Hybrid V2:
work descriptions, project descriptions, self evaluations, skills, education,
and intention fields. Labels still come from generator blueprints, never from
keyword scans of the finished text.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

from search_agent.corpus import (
    ABILITY_SKILLS,
    DEFAULT_QUERIES,
    EVIDENCE_HINTS,
    IMPLICIT_PROJECTS,
    QuerySpec,
)

SCHEMA_VERSION = 2
_CORPUS_END_DATE = date(2026, 10, 1)

_SURNAMES = (
    "赵",
    "钱",
    "孙",
    "李",
    "周",
    "吴",
    "郑",
    "王",
    "冯",
    "陈",
    "褚",
    "卫",
    "蒋",
    "沈",
    "韩",
    "杨",
    "朱",
    "秦",
    "尤",
    "许",
    "何",
    "吕",
    "施",
    "张",
    "孔",
)

_GIVEN_NAMES = (
    "子墨",
    "雨桐",
    "浩然",
    "思远",
    "嘉宁",
    "宇航",
    "欣怡",
    "明轩",
    "若曦",
    "俊杰",
    "晨曦",
    "一鸣",
    "泽宇",
    "佳琪",
    "博文",
    "语嫣",
    "嘉豪",
    "梦瑶",
    "天宇",
    "诗涵",
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
    "某在线教育公司",
    "某智能制造企业",
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
    "东南大学",
    "厦门大学",
)

_MAJORS = (
    "计算机科学与技术",
    "软件工程",
    "信息工程",
    "自动化",
    "通信工程",
    "数据科学",
)

_CITIES = ("北京", "上海", "深圳", "杭州", "广州", "成都", "武汉", "南京")

_BASE_SKILLS = ("MySQL", "Linux", "Git", "Docker", "Redis")

_NEUTRAL_CLAUSES = (
    "有完整的项目交付与跨团队协作经验",
    "熟悉从需求拆解、方案设计到上线的完整流程",
    "具备复杂业务系统的工程化落地经验",
    "有较强的技术方案推进和问题定位能力",
)

_WORK_OPENERS = (
    "负责核心业务系统的研发与迭代",
    "参与平台能力建设和日常需求交付",
    "承担业务模块的设计、开发与上线支持",
)

_EXPLICIT_PHRASES: dict[str, tuple[str, ...]] = {
    "高并发系统设计": (
        "负责大促流量保障与 QPS 容量评估，持续推动性能优化",
        "主导高并发链路的压测和性能优化，保障峰值流量平稳",
    ),
    "微服务架构": (
        "推动单体服务拆分为微服务，落地 Spring Cloud 与服务治理规范",
        "负责微服务接口治理和 Service Mesh 接入，降低服务耦合",
    ),
    "分布式系统": (
        "负责 Kafka 消息队列与分布式事务，解决跨系统一致性问题",
        "设计分布式任务调度和消息补偿机制，提升跨机房可用性",
    ),
    "推荐系统": (
        "负责推荐召回与排序策略，持续迭代 CTR 模型和特征工程",
        "搭建推荐系统的离线评估与在线排序链路",
    ),
    "数据工程": (
        "使用 Spark 与 Flink 搭建数据仓库和 ETL 调度链路",
        "负责数据平台建设、指标加工和批流任务治理",
    ),
    "机器学习": (
        "使用 PyTorch 完成模型训练与深度学习效果评估",
        "负责机器学习模型训练、TensorFlow 推理服务和特征迭代",
    ),
    "系统架构设计": (
        "负责系统架构设计与技术选型，推进容量规划和稳定性治理",
        "主导核心平台的架构演进和稳定性方案评审",
    ),
    "服务端开发": (
        "负责后端服务端开发，使用 Java、Go 与 Python 完成业务交付",
        "承担 Java 服务端接口设计和业务系统研发",
    ),
}

_PROJECT_NAMES: dict[str, tuple[str, ...]] = {
    "高并发系统设计": ("核心交易链路优化", "支付网关质量提升"),
    "微服务架构": ("订单域服务拆分", "服务注册发现平台建设"),
    "分布式系统": ("跨机房数据同步", "消息链路可靠性建设"),
    "推荐系统": ("内容分发策略优化", "用户兴趣服务建设"),
    "数据工程": ("批流一体链路建设", "指标口径治理项目"),
    "机器学习": ("点击率预估迭代", "在线推理服务建设"),
    "系统架构设计": ("核心平台演进规划", "技术方案评审体系"),
    "服务端开发": ("交易链路重构", "业务系统研发迭代"),
}

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

_ALL_ABILITIES = tuple(EVIDENCE_HINTS)


@dataclass(frozen=True)
class V2ResumeCorpus:
    """Rich resume records plus the exact oracle used by the benchmark."""

    seed: int
    candidates: tuple[dict[str, Any], ...]
    queries: tuple[QuerySpec, ...]
    abilities: dict[str, frozenset[str]]
    implicit: dict[str, frozenset[str]]
    visible: dict[str, frozenset[str]]
    years: dict[str, float]

    @property
    def size(self) -> int:
        return len(self.candidates)

    def records(self) -> list[dict[str, Any]]:
        return [dict(record) for record in self.candidates]

    def relevant(self, spec: QuerySpec) -> tuple[str, ...]:
        required = set(spec.required_abilities)
        return tuple(
            doc_id
            for doc_id, abilities in self.abilities.items()
            if required <= abilities and self.years[doc_id] >= spec.min_years
        )


def generate_v2_resume_corpus(
    size: int = 100,
    seed: int = 2026,
    queries: Sequence[QuerySpec] | None = None,
) -> V2ResumeCorpus:
    """Generate deterministic V2-native resumes round-robin over role families."""

    if size < 1:
        raise ValueError("size must be >= 1")
    specs = tuple(queries) if queries else DEFAULT_QUERIES
    if not specs:
        raise ValueError("at least one query spec is required")

    rng = random.Random(seed)
    names = [
        f"{_SURNAMES[index % len(_SURNAMES)]}{_GIVEN_NAMES[(index * 7 + 3) % len(_GIVEN_NAMES)]}"
        for index in range(size)
    ]
    if len(set(names)) != len(names):
        raise ValueError("generated candidate names are not unique")
    rng.shuffle(names)

    candidates: list[dict[str, Any]] = []
    abilities_map: dict[str, frozenset[str]] = {}
    implicit_map: dict[str, frozenset[str]] = {}
    visible_map: dict[str, frozenset[str]] = {}
    years_map: dict[str, float] = {}

    for index in range(size):
        archetype = _ARCHETYPES[index % len(_ARCHETYPES)]
        doc_id = f"v{index + 1:03d}"
        record, abilities, implicit, years = _make_candidate(
            rng,
            doc_id=doc_id,
            name=names[index],
            archetype=archetype,
            queries=specs,
        )
        candidates.append(record)
        abilities_map[doc_id] = abilities
        implicit_map[doc_id] = implicit
        visible_map[doc_id] = visible_abilities(record, abilities)
        years_map[doc_id] = years

    return V2ResumeCorpus(
        seed=seed,
        candidates=tuple(candidates),
        queries=specs,
        abilities=abilities_map,
        implicit=implicit_map,
        visible=visible_map,
        years=years_map,
    )


def visible_abilities(record: dict[str, Any], abilities: Iterable[str]) -> frozenset[str]:
    """Re-scan the finished record instead of trusting expression labels."""

    blob = _text_blob(record).lower()
    return frozenset(
        ability
        for ability in abilities
        if any(hint in blob for hint in EVIDENCE_HINTS.get(ability, ()))
    )


def write_v2_resume_corpus(corpus: V2ResumeCorpus, directory: str | Path) -> tuple[Path, Path]:
    """Write ``candidates.jsonl`` and ``oracle.json`` for the rich corpus."""

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    candidates_path = target / "candidates.jsonl"
    oracle_path = target / "oracle.json"

    with candidates_path.open("w", encoding="utf-8") as handle:
        for record in corpus.candidates:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "generator": "v2_resume_corpus",
        "seed": corpus.seed,
        "size": corpus.size,
        "coverage": _coverage(corpus.candidates),
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
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return candidates_path, oracle_path


def _make_candidate(
    rng: random.Random,
    *,
    doc_id: str,
    name: str,
    archetype: Sequence[str],
    queries: Sequence[QuerySpec],
) -> tuple[dict[str, Any], frozenset[str], frozenset[str], float]:
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
    for ability in sorted(abilities):
        if rng.random() < 0.42:
            implicit.add(ability)
        else:
            skills.extend(ABILITY_SKILLS[ability])
    skills.extend(_BASE_SKILLS if rng.random() < 0.7 else ())
    deduped = list(dict.fromkeys(skills))
    if not deduped:
        deduped = list(_BASE_SKILLS)
    rng.shuffle(deduped)

    title = _pick_title(rng, _base_title(abilities), implicit)
    degree = "硕士" if rng.random() < 0.32 else "本科"
    city = rng.choice(_CITIES)
    age = int(22 + years + (2 if degree == "硕士" else 0) + rng.randint(0, 3))

    work_periods = _experience_periods(rng, years, _job_count(rng, years))
    work_experiences: list[dict[str, Any]] = []
    companies = rng.sample(_COMPANIES, len(work_periods))
    ordered_abilities = sorted(abilities)
    for index, ((start, end), company) in enumerate(zip(work_periods, companies, strict=True)):
        selected = [
            ordered_abilities[(index + offset) % len(ordered_abilities)]
            for offset in range(min(2, len(ordered_abilities)))
        ]
        sentences = [rng.choice(_WORK_OPENERS)]
        sentences.extend(_ability_phrase(rng, ability, ability in implicit) for ability in selected)
        work_experiences.append(
            {
                "company_name": company,
                "job_title": title,
                "description": "；".join(sentences) + "。",
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
            }
        )

    projects: list[dict[str, Any]] = []
    for index, ability in enumerate(ordered_abilities):
        start, end = work_periods[index % len(work_periods)]
        project_start = _shift_months(start, min(3, _month_span(start, end) // 3))
        project_end = _shift_months(project_start, max(6, min(18, _month_span(start, end) - 3)))
        if project_end > end:
            project_end = end
        projects.append(
            {
                "project_name": rng.choice(_PROJECT_NAMES[ability]),
                "description": _ability_phrase(rng, ability, ability in implicit),
                "startDate": project_start.isoformat(),
                "endDate": project_end.isoformat(),
            }
        )

    summary_phrases = [
        _ability_phrase(rng, ability, ability in implicit) for ability in ordered_abilities[:2]
    ]
    summary = (
        f"{years:g} 年{title}经验，{rng.choice(_NEUTRAL_CLAUSES)}；"
        + "；".join(summary_phrases)
        + "。"
    )
    self_evaluation = (
        f"长期负责{title}方向，{rng.choice(_NEUTRAL_CLAUSES)}；"
        f"{_ability_phrase(rng, ordered_abilities[-1], ordered_abilities[-1] in implicit)}。"
    )

    record: dict[str, Any] = {
        "docId": doc_id,
        "candidateProfileId": doc_id,
        "name": name,
        "workYears": years,
        "currentResidence": city,
        "age": age,
        "gender": "男" if rng.random() < 0.55 else "女",
        "summary": summary,
        "overview": {"self_evaluation": self_evaluation},
        "skills": [
            {"name": skill, "level": round(rng.uniform(0.55, 0.95), 2)} for skill in deduped[:10]
        ],
        "workExperiences": work_experiences,
        "projectExperiences": projects,
        "educations": _education_rows(rng, years=years, degree=degree),
        "certificates": _certificate_rows(rng),
        "languages": [{"name": "英语"}],
        "tags": ["技术岗"],
        "expectedPositions": _expected_positions(title),
        "expectedCities": list(dict.fromkeys([city, rng.choice(_CITIES)])),
        "expectedSalary": _salary_band(years),
        "expectedWorkType": "全职",
        "workState": rng.choice(("在职，看看新机会", "在职，急寻新工作", "离职，正在找工作")),
    }
    return record, frozenset(abilities), frozenset(implicit), years


def _ability_phrase(rng: random.Random, ability: str, implicit: bool) -> str:
    bank = IMPLICIT_PROJECTS[ability] if implicit else _EXPLICIT_PHRASES[ability]
    return rng.choice(bank)


def _base_title(abilities: set[str]) -> str:
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
    if not _leaks(base_title, implicit):
        return base_title
    fallback = (
        "业务系统研发工程师",
        "交易链路开发工程师",
        "平台研发负责人",
        "技术方案负责人",
        "研发工程师",
    )
    safe = [title for title in fallback if not _leaks(title, implicit)]
    return rng.choice(safe or ["研发工程师"])


def _leaks(text: str, abilities: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(hint in lowered for ability in abilities for hint in EVIDENCE_HINTS.get(ability, ()))


def _job_count(rng: random.Random, years: float) -> int:
    if years < 2:
        return 1
    if years < 5:
        return 2
    return rng.choice((2, 3)) if rng.random() < 0.55 else 2


def _experience_periods(rng: random.Random, years: float, count: int) -> list[tuple[date, date]]:
    total_months = max(count * 6, int(round(years * 12)))
    base, remainder = divmod(total_months, count)
    spans = [base + (1 if index < remainder else 0) for index in range(count)]
    periods: list[tuple[date, date]] = []
    cursor = _shift_months(_CORPUS_END_DATE, -total_months)
    for span in spans:
        end = _shift_months(cursor, span)
        periods.append((cursor, end))
        cursor = end
    if periods:
        periods[-1] = (periods[-1][0], _CORPUS_END_DATE)
    return periods


def _shift_months(anchor: date, months: int) -> date:
    month_index = anchor.year * 12 + anchor.month - 1 + months
    year, month = divmod(month_index, 12)
    return date(year, month + 1, 1)


def _month_span(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


def _education_rows(rng: random.Random, *, years: float, degree: str) -> list[dict[str, Any]]:
    graduation = _CORPUS_END_DATE.year - int(years)
    school = rng.choice(_SCHOOLS)
    major = rng.choice(_MAJORS)
    rows: list[dict[str, Any]] = []
    if degree == "硕士":
        rows.append(
            {
                "school": school,
                "major": major,
                "degree": "本科",
                "startDate": f"{graduation - 6}-09-01",
                "endDate": f"{graduation - 3}-06-30",
            }
        )
        rows.append(
            {
                "school": rng.choice(_SCHOOLS),
                "major": major,
                "degree": "硕士",
                "startDate": f"{graduation - 3}-09-01",
                "endDate": f"{graduation}-06-30",
            }
        )
    else:
        rows.append(
            {
                "school": school,
                "major": major,
                "degree": "本科",
                "startDate": f"{graduation - 4}-09-01",
                "endDate": f"{graduation}-06-30",
            }
        )
    return rows


def _certificate_rows(rng: random.Random) -> list[dict[str, str]]:
    if rng.random() >= 0.3:
        return []
    return [{"name": rng.choice(("PMP 项目管理认证", "CKA 认证", "软件设计师"))}]


def _expected_positions(title: str) -> list[str]:
    return list(dict.fromkeys([title, "研发工程师"]))


def _salary_band(years: float) -> str:
    if years < 3:
        return "10k-15k"
    if years < 5:
        return "15k-25k"
    if years < 8:
        return "25k-35k"
    return "35k-50k"


def _text_blob(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_text_blob(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_text_blob(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _coverage(records: Sequence[dict[str, Any]]) -> dict[str, int]:
    return {
        "workExperiences": sum(bool(row.get("workExperiences")) for row in records),
        "workDescriptions": sum(
            any(work.get("description") for work in row.get("workExperiences", []))
            for row in records
        ),
        "projectExperiences": sum(bool(row.get("projectExperiences")) for row in records),
        "selfEvaluations": sum(
            bool((row.get("overview") or {}).get("self_evaluation")) for row in records
        ),
        "expectedCities": sum(bool(row.get("expectedCities")) for row in records),
        "languages": sum(bool(row.get("languages")) for row in records),
    }


__all__ = [
    "SCHEMA_VERSION",
    "V2ResumeCorpus",
    "generate_v2_resume_corpus",
    "visible_abilities",
    "write_v2_resume_corpus",
]
