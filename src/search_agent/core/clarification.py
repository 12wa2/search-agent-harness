"""Deterministic clarification for underspecified recruitment queries.

The LLM can extract constraints from a concrete job brief, but it reasonably
treats a phrase such as "find someone who knows Python" as a valid search. This
module adds a narrow, predictable guard for one missing decision: a bare skill
that is not accompanied by an explicit search action. A request such as
"帮我找会 Python 的" is already actionable for the search tool and must not be
interrupted; turn-level follow-ups are never interrupted either.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ClarificationOption:
    label: str
    description: str
    query: str

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "description": self.description, "query": self.query}


@dataclass(frozen=True)
class Clarification:
    questions: list[str]
    options: list[ClarificationOption]


_SKILL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Python", re.compile(r"(?<![a-z0-9])python(?![a-z0-9])", re.IGNORECASE)),
    ("Java", re.compile(r"(?<![a-z0-9])java(?![a-z0-9])", re.IGNORECASE)),
    ("JavaScript", re.compile(r"(?<![a-z0-9])javascript(?![a-z0-9])", re.IGNORECASE)),
    ("TypeScript", re.compile(r"(?<![a-z0-9])typescript(?![a-z0-9])", re.IGNORECASE)),
    ("Go", re.compile(r"(?<![a-z0-9])golang(?![a-z0-9])", re.IGNORECASE)),
    ("Go", re.compile(r"(?<![a-z0-9])go(?![a-z0-9])", re.IGNORECASE)),
    ("C++", re.compile(r"(?<![a-z0-9])c\+\+(?![a-z0-9])", re.IGNORECASE)),
    ("C#", re.compile(r"(?<![a-z0-9])c#(?![a-z0-9])", re.IGNORECASE)),
    ("Rust", re.compile(r"(?<![a-z0-9])rust(?![a-z0-9])", re.IGNORECASE)),
    ("SQL", re.compile(r"(?<![a-z0-9])sql(?![a-z0-9])", re.IGNORECASE)),
    ("React", re.compile(r"(?<![a-z0-9])react(?![a-z0-9])", re.IGNORECASE)),
    ("Vue", re.compile(r"(?<![a-z0-9])vue(?![a-z0-9])", re.IGNORECASE)),
    ("Node.js", re.compile(r"(?<![a-z0-9])node(?:\.js|js)?(?![a-z0-9])", re.IGNORECASE)),
    ("Django", re.compile(r"(?<![a-z0-9])django(?![a-z0-9])", re.IGNORECASE)),
    ("Flask", re.compile(r"(?<![a-z0-9])flask(?![a-z0-9])", re.IGNORECASE)),
    ("FastAPI", re.compile(r"(?<![a-z0-9])fastapi(?![a-z0-9])", re.IGNORECASE)),
    ("Spring", re.compile(r"(?<![a-z0-9])spring(?![a-z0-9])", re.IGNORECASE)),
    ("Kubernetes", re.compile(r"(?<![a-z0-9])kubernetes(?![a-z0-9])", re.IGNORECASE)),
    ("Docker", re.compile(r"(?<![a-z0-9])docker(?![a-z0-9])", re.IGNORECASE)),
    ("Spark", re.compile(r"(?<![a-z0-9])spark(?![a-z0-9])", re.IGNORECASE)),
    ("Flink", re.compile(r"(?<![a-z0-9])flink(?![a-z0-9])", re.IGNORECASE)),
    ("PyTorch", re.compile(r"(?<![a-z0-9])pytorch(?![a-z0-9])", re.IGNORECASE)),
    ("TensorFlow", re.compile(r"(?<![a-z0-9])tensorflow(?![a-z0-9])", re.IGNORECASE)),
)

_ROLE_SIGNAL = re.compile(
    r"后端|前端|全栈|服务端|客户端|工程师|开发|研发|算法|数据|测试|运维|架构|"
    r"机器学习|深度学习|人工智能|产品|设计|分析|研究|科学家|经理|主管|专家|"
    r"技术负责人|负责人|实习生|候选人|人选|岗位|职位|招聘|"
    r"backend|frontend|full[- ]?stack|engineer|developer|architect|data|machine learning|"
    r"ml|devops|qa|analyst|scientist|manager|lead|recruit|candidate|role|position",
    re.IGNORECASE,
)

_CONDITION_SIGNAL = re.compile(
    r"\d+\s*年|年以上|年以内|工作年限|经验|应届|毕业|本科|硕士|博士|学历|"
    r"北京|上海|深圳|广州|杭州|成都|南京|武汉|西安|苏州|重庆|天津|远程|城市|"
    r"薪资|年薪|年龄|语言|证书|大厂|名企|公司",
    re.IGNORECASE,
)

_SEARCH_ACTION = re.compile(
    r"找|寻找|搜|搜索|查|查询|招|招聘|推荐|筛选|匹配|看看|看一下|"
    r"find|search|look\s*for|hire|recruit",
    re.IGNORECASE,
)


def is_actionable_recruitment_query(query: str) -> bool:
    """Return True when the query already has enough context to search.

    The LLM occasionally asks for a role or skill clarification even when the
    query already names a role together with a condition, or names a skill
    together with a search action. Those requests would unnecessarily stop a
    valid search, so the deterministic harness ignores them.
    """
    text = str(query or "").strip()
    if not text:
        return False

    has_role = bool(_ROLE_SIGNAL.search(text))
    has_condition = bool(_CONDITION_SIGNAL.search(text))
    has_action = bool(_SEARCH_ACTION.search(text))
    has_skill = bool(_matched_skills(text))
    return (has_role and has_condition) or (has_skill and (has_role or has_action))


def _matched_skills(query: str) -> list[str]:
    skills: list[str] = []
    for label, pattern in _SKILL_PATTERNS:
        if pattern.search(query) and label not in skills:
            skills.append(label)
    return skills


def detect_skill_only_clarification(
    query: str,
    *,
    has_history: bool = False,
) -> Clarification | None:
    """Return clarification options only for an underspecified skill-only query.

    Role, seniority, location, education, and explicit recruitment signals are
    treated as enough context to let the normal Agent pipeline decide what to
    do. An explicit search action also makes a skill-only brief actionable:
    the SearchTool can retrieve on skill alone, so asking for a role first
    would add latency without changing the query.
    """
    if has_history:
        return None
    text = str(query or "").strip()
    if not text:
        return None

    skills = _matched_skills(text)
    if (
        not skills
        or _ROLE_SIGNAL.search(text)
        or _CONDITION_SIGNAL.search(text)
        or _SEARCH_ACTION.search(text)
    ):
        return None

    skill = " / ".join(skills[:2])
    question = f"你希望寻找哪一类 {skill} 候选人？"
    options = [
        ClarificationOption(
            label="后端开发",
            description="API、服务端系统、微服务",
            query=f"寻找 {skill} 后端工程师，重点考察服务端 API、微服务和系统设计经验",
        ),
        ClarificationOption(
            label="机器学习 / AI 工程",
            description="模型训练、推理服务、算法应用",
            query=f"寻找 {skill} 机器学习 / AI 工程师，重点考察模型训练、推理服务或算法落地经验",
        ),
        ClarificationOption(
            label="数据工程",
            description="ETL、数据平台、批流处理",
            query=f"寻找 {skill} 数据工程师，重点考察 ETL、数据平台和批流处理经验",
        ),
    ]
    return Clarification(questions=[question], options=options)


__all__ = [
    "Clarification",
    "ClarificationOption",
    "detect_skill_only_clarification",
    "is_actionable_recruitment_query",
]
