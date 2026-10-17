"""Deterministic routing for requests that should not start the search loop.

The router is intentionally conservative: only explicit greetings, help
questions and clearly unrelated tasks are handled before the Harness. Anything
ambiguous falls through to SEARCH so a valid recruiting query is never rejected
because it lacks a familiar job title.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Intent(str, Enum):
    GREETING = "GREETING"
    HELP = "HELP"
    SEARCH = "SEARCH"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


@dataclass(frozen=True)
class Suggestion:
    label: str
    query: str


@dataclass(frozen=True)
class RouteDecision:
    intent: Intent
    message: str = ""
    suggestions: tuple[Suggestion, ...] = ()
    rule: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": "assistant_reply",
            "intent": self.intent.value,
            "message": self.message,
            "suggestions": [
                {"label": item.label, "query": item.query} for item in self.suggestions
            ],
            "searchStarted": False,
        }


SUGGESTIONS: tuple[Suggestion, ...] = (
    Suggestion(
        "高并发后端 · 3 年+",
        "找有 3 年以上经验、做过高并发系统的后端工程师，最好来自大厂",
    ),
    Suggestion(
        "推荐算法 · 3 年+",
        "推荐算法工程师，要求 3 年以上推荐系统与机器学习经验",
    ),
    Suggestion(
        "数据平台 · Spark/Flink",
        "数据工程师，熟悉 Spark 和 Flink，做过数据平台架构，3 年以上经验",
    ),
)

_EDGE_PUNCT = " \t\r\n!！?？。.,，;；:：~～…\"'“”‘’()（）[]【】"

_GREETING_RE = re.compile(
    r"^(?:你好|您好|嗨|哈喽|哈啰|在吗|早安|早上好|上午好|中午好|下午好|晚上好|晚安|"
    r"hello|hi|hey)(?:呀|啊|哦|哈|呢|\s+there)?$",
    re.IGNORECASE,
)

_HELP_RE = re.compile(
    r"^(?:"
    r"你能做什么|你可以做什么|你会做什么|你能干什么|你有什么功能|你有哪些功能|"
    r"支持什么|支持什么功能|支持哪些功能|功能介绍|使用方法|使用说明|"
    r"怎么用|怎么使用|如何使用|帮助|"
    r"(?:怎么|如何)(?:搜索|查找|寻找|找)候选人|"
    r"(?:怎么|如何)使用(?:这个|该)?(?:agent|助手|系统|工具)|"
    r"what can you do|how can you help|how to use(?: this)?(?: agent| tool)?|help"
    r")$",
    re.IGNORECASE,
)

_GENERAL_TASK_RE = re.compile(
    r"^(?:帮我|请|麻烦|我想|我要|我需要|能不能|可以)?\s*(?:怎么|如何)?\s*"
    r"(?:写|生成|创作|润色|改写|总结|翻译|解释|讲解|教我|学习|修复|调试|编码|"
    r"计算|算一下|解方程|证明|画|做|制作|设计|策划|规划|安排|预订|购买|下单)",
    re.IGNORECASE,
)

_GENERAL_TOPIC_RE = re.compile(
    r"(?:天气|weather|新闻|股票|机票|酒店|餐厅|菜谱|电影|音乐|笑话|故事|星座|彩票|汇率)",
    re.IGNORECASE,
)

_GENERAL_KNOWLEDGE_RE = re.compile(
    r"^(?:请问)?(?:"
    r"什么是\s*(?:python|java|javascript|typescript|go|golang|rust|sql|ai|llm|大模型|机器学习|深度学习|算法|高并发|微服务)|"
    r"(?:python|java|javascript|typescript|go|golang|rust|sql|ai|llm|大模型|机器学习|深度学习|算法|高并发|微服务)"
    r"\s*(?:是什么|是什么意思|怎么理解|的定义是什么|有什么用)"
    r")$",
    re.IGNORECASE,
)

_SEARCH_ACTION_RE = re.compile(
    r"(?:找|寻找|搜索|搜|推荐|筛选|招聘|招|物色|寻访|find|hire|recruit|source|recommend|screen)",
    re.IGNORECASE,
)

_ROLE_RE = re.compile(
    r"(?:工程师|开发者|架构师|算法|数据分析|数据工程|产品经理|项目经理|设计师|运营|"
    r"销售|市场|财务|法务|人力|研究员|科学家|测试|运维|客服|翻译|顾问|经理|主管|"
    r"专家|实习生|应届生|engineer|developer|architect|scientist|analyst|manager|"
    r"designer|recruiter|candidate|resume)",
    re.IGNORECASE,
)


def normalize_query(query: str) -> str:
    """Normalize width, case, whitespace and edge punctuation for matching."""
    text = unicodedata.normalize("NFKC", query or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip(_EDGE_PUNCT)


def _has_recruitment_signal(text: str) -> bool:
    """Protect recruiting queries that happen to contain a task-like phrase."""
    return bool(_SEARCH_ACTION_RE.search(text) and _ROLE_RE.search(text))


def _out_of_scope_rule(text: str) -> str | None:
    if _GENERAL_KNOWLEDGE_RE.fullmatch(text):
        return "general_knowledge"
    if _GENERAL_TASK_RE.match(text):
        return "general_task"
    if _GENERAL_TOPIC_RE.search(text):
        return "general_topic"
    return None


def _reply(intent: Intent, message: str, *, rule: str) -> RouteDecision:
    return RouteDecision(
        intent=intent,
        message=message,
        suggestions=SUGGESTIONS,
        rule=rule,
    )


class IntentRouter:
    """Zero-LLM request router used by the streaming HTTP entry point."""

    def route(self, query: str) -> RouteDecision:
        text = normalize_query(query)
        if not text:
            return _reply(
                Intent.OUT_OF_SCOPE,
                "请描述一个具体的招聘需求，例如岗位、技能和工作年限。",
                rule="empty",
            )
        if _GREETING_RE.fullmatch(text):
            return _reply(
                Intent.GREETING,
                "你好！我是 Search Agent，可以根据岗位、技能、年限和项目背景帮你发现候选人。\n"
                "告诉我目标岗位和关键要求，我会展示检索路径、匹配依据与运行指标。",
                rule="greeting",
            )
        if _HELP_RE.fullmatch(text):
            return _reply(
                Intent.HELP,
                "我可以把招聘需求拆成岗位画像和人才画像，规划多条检索路径，"
                "再根据结果反思并调整策略。\n"
                "你可以输入岗位名称、必备技能、工作年限、行业或项目背景，"
                "例如“找有 3 年以上经验、做过高并发系统的后端工程师”。",
                rule="help",
            )

        rule = _out_of_scope_rule(text)
        if rule and not _has_recruitment_signal(text):
            return _reply(
                Intent.OUT_OF_SCOPE,
                "我目前专注于招聘需求解析与候选人搜索，暂不处理写诗、翻译、天气或通用知识问答。\n"
                "你可以改为描述一个岗位和关键要求，我会启动八阶段搜索 Agent。",
                rule=rule,
            )

        # Ambiguous inputs deliberately stay on the search path. The Harness can
        # ask for clarification; the router must not reject an unusual job query
        # just because no familiar role keyword was found.
        return RouteDecision(intent=Intent.SEARCH, rule="search")


__all__ = [
    "Intent",
    "IntentRouter",
    "RouteDecision",
    "SUGGESTIONS",
    "Suggestion",
    "normalize_query",
]
