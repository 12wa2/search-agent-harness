"""Offline demo: a deterministic scripted LLM plus sample queries.

The CLI ``demo`` command and the smoke test use this to run the full
eight-stage loop with zero API keys. The scripted client is intentionally
rule-based: it demonstrates the contract between harness and LLM (four JSON
schemas), not model intelligence.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from search_agent.core.difficulty import difficulty_floor
from search_agent.direct_search import build_direct_search_plan
from search_agent.llm.client import LlmUsage, estimated_usage
from search_agent.models.api import SearchRequest, SearchResponse

DEMO_DATA = Path(__file__).resolve().parent / "data" / "demo_candidates.jsonl"

DEMO_QUERIES = [
    "找有 3 年以上经验、做过高并发系统的后端工程师，最好来自大厂",
    "招聘推荐算法工程师，要求有召回排序和机器学习经验，5 年以上",
    "数据工程师，熟悉 Spark 和 Flink，3 年以上经验",
]

ABILITY_LIBRARY: list[dict[str, Any]] = [
    {
        "keys": ["高并发", "大流量", "性能"],
        "name": "高并发系统设计",
        "hints": ["高并发", "QPS", "流量", "性能优化", "大促"],
    },
    {
        "keys": ["微服务", "spring cloud", "dubbo", "服务治理"],
        "name": "微服务架构",
        "hints": ["微服务", "Spring Cloud", "Dubbo", "服务治理", "Service Mesh"],
    },
    {
        "keys": ["分布式", "一致性", "消息队列", "kafka"],
        "name": "分布式系统",
        "hints": ["分布式", "一致性", "Kafka", "消息队列", "分布式事务"],
    },
    {
        "keys": ["推荐", "召回", "排序", "ctr"],
        "name": "推荐系统",
        "hints": ["推荐", "召回", "排序", "CTR", "特征工程"],
    },
    {
        "keys": ["数据", "spark", "flink", "数仓", "etl"],
        "name": "数据工程",
        "hints": ["Spark", "Flink", "数据仓库", "ETL", "数据平台"],
    },
    {
        "keys": ["机器学习", "深度学习", "模型", "算法"],
        "name": "机器学习",
        "hints": ["机器学习", "深度学习", "PyTorch", "TensorFlow", "模型训练"],
    },
    {
        "keys": ["架构", "技术选型"],
        "name": "系统架构设计",
        "hints": ["架构", "技术选型", "稳定性", "容量规划"],
    },
    {
        "keys": ["后端", "服务端", "java", "go", "python"],
        "name": "服务端开发",
        "hints": ["后端", "服务端", "Java", "Go", "Python"],
    },
]

_TECH_SKILLS = [
    "Java",
    "Go",
    "Python",
    "C++",
    "Kafka",
    "Redis",
    "MySQL",
    "Kubernetes",
    "Spark",
    "Flink",
    "PyTorch",
    "TensorFlow",
]
_CITIES = ["北京", "上海", "深圳", "杭州", "广州", "成都", "武汉", "南京"]
_BIG_TECH = ["字节跳动", "腾讯", "阿里", "美团", "百度", "京东", "快手", "网易", "滴滴"]
_FOLLOW_UP_MARKERS = ("最好", "优先", "不要", "排除", "还要", "希望", "偏向")


def _json_payload(user: str) -> dict[str, Any]:
    try:
        return json.loads(user)
    except json.JSONDecodeError:
        return {}


def _loose_years_condition() -> dict[str, Any]:
    return {
        "field": "work_years",
        "operator": "gte",
        "values": [0],
        "required": True,
        "weight": 1.0,
        "softText": None,
    }


def _ability_keywords(name: str) -> list[str]:
    for entry in ABILITY_LIBRARY:
        if entry["name"] == name:
            return [str(hint).lower() for hint in entry["hints"]]
    lowered = name.lower()
    words = [w for w in re.findall(r"[a-z0-9+#.]+", lowered) if len(w) >= 2]
    return list(dict.fromkeys([lowered, *words]))


def _title_for(query: str) -> str:
    if "推荐" in query or "算法" in query:
        return "算法工程师"
    if "数据" in query or "Spark" in query or "Flink" in query:
        return "数据工程师"
    if "架构" in query:
        return "系统架构师"
    return "后端工程师"


def _is_follow_up_query(query: str) -> bool:
    """Offline stand-in for the real model's conversation-history merge."""
    return any(marker in query for marker in _FOLLOW_UP_MARKERS)


class ScriptedLlmClient:
    """Deterministic rule-based stand-in for the four LLM roles.

    Implements the same ``LlmClient`` protocol as ``OpenAILlmClient`` and
    records ``(schema_name, user)`` calls like ``FakeLlmClient``.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.usage = LlmUsage()
        self.last_usage: LlmUsage | None = None

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append((schema_name, user))
        if schema_name == "hybrid_search_v2_ir":
            result = self._hybrid_v2_ir(user)
            self.last_usage = estimated_usage(system, user, result)
            self.usage.add(self.last_usage)
            return result
        payload = _json_payload(user)
        handler = {
            "agent_understand": self._understand,
            "agent_plan": self._plan,
            "agent_judge": self._judge,
            "agent_reflect": self._reflect,
        }.get(schema_name)
        result = handler(payload) if handler else {}
        self.last_usage = estimated_usage(system, user, result)
        self.usage.add(self.last_usage)
        return result

    def _hybrid_v2_ir(self, query: str) -> dict[str, Any]:
        """Deterministic stand-in for the real V2 NL-to-IR call."""
        plan = build_direct_search_plan(query)
        if plan is not None:
            return plan.intent.model_dump()
        text = str(query or "").strip() or "候选人"
        return {
            "where": {
                "type": "leaf",
                "condition": {
                    "field": "skill",
                    "operator": "any",
                    "values": [text],
                    "required": False,
                    "weight": 1.0,
                },
            },
            "semantic": [
                {"text": text, "fields": ["work_vector"], "weight": 1.0}
            ],
            "meta": {"needClarification": False, "clarifyQuestions": [], "notes": []},
        }

    # -- the four roles ------------------------------------------------------

    def _understand(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_query = str(payload.get("query") or "")
        query = raw_query
        history = payload.get("conversation_history") or []
        if history and _is_follow_up_query(raw_query):
            prior_queries = [str(item.get("query") or "") for item in history[-2:]]
            query = " ".join(part for part in [*prior_queries, raw_query] if part)
        lowered = query.lower()
        abilities: list[dict[str, Any]] = []
        soft_signals: list[dict[str, Any]] = []
        for entry in ABILITY_LIBRARY:
            if any(str(key).lower() in lowered for key in entry["keys"]):
                abilities.append(
                    {
                        "name": entry["name"],
                        "level": "must",
                        "evidence_hints": list(entry["hints"]),
                    }
                )
                soft_signals.append(
                    {
                        "text": f"{entry['name']}相关经验",
                        "fields": ["work_vector", "project_vector"],
                        "weight": 1.5,
                    }
                )
        if not abilities:
            fragment = query[:12] or "岗位"
            abilities = [{"name": "岗位相关经验", "level": "must", "evidence_hints": [fragment]}]
            soft_signals = [{"text": fragment, "fields": ["work_vector"], "weight": 1.0}]

        hard_constraints: list[dict[str, Any]] = []
        years = re.search(r"(\d+)\s*年", query)
        if years:
            hard_constraints.append(
                {
                    "field": "work_years",
                    "operator": "gte",
                    "values": [int(years.group(1))],
                    "required": True,
                    "weight": 1.0,
                    "softText": None,
                }
            )
        skills = [skill for skill in _TECH_SKILLS if skill.lower() in lowered][:3]
        if skills:
            hard_constraints.append(
                {
                    "field": "skill",
                    "operator": "any",
                    "values": skills,
                    "required": True,
                    "weight": 1.0,
                    "softText": None,
                }
            )
        city = next((c for c in _CITIES if c in query), None)
        if city:
            hard_constraints.append(
                {
                    "field": "city",
                    "operator": "any",
                    "values": [city],
                    "required": True,
                    "weight": 1.0,
                    "softText": None,
                }
            )

        sources: list[dict[str, Any]] = []
        mentioned = [name for name in _BIG_TECH if name in query][:2]
        for name in mentioned:
            sources.append(
                {
                    "id": f"s{len(sources) + 1}",
                    "kind": "company",
                    "value": name,
                    "rationale": "查询中明确提到的目标公司",
                    "transfer_from": [],
                }
            )
        if not sources and "大厂" in query:
            sources.append(
                {
                    "id": "s1",
                    "kind": "transferable",
                    "value": "一线互联网大厂",
                    "rationale": "查询偏好归纳出的来源维度",
                    "transfer_from": [],
                }
            )
        if not sources:
            sources.append(
                {
                    "id": "s1",
                    "kind": "industry",
                    "value": "互联网",
                    "rationale": "岗位默认来源行业",
                    "transfer_from": [],
                }
            )

        return {
            "raw_query": raw_query,
            "title": _title_for(query),
            "must_abilities": abilities,
            "nice_abilities": [],
            "hard_constraints": hard_constraints,
            "subjective_difficulty": difficulty_floor(query),
            "difficulty_reason": "演示脚本按查询的显式程度判定",
            "needs_clarification": False,
            "clarify_questions": [],
            "talent_profile": {
                "source_hypotheses": sources,
                "must_keywords": [skill for skill in skills],
                "soft_signals": soft_signals,
                "risk_filters": [],
            },
        }

    def _plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        job = payload.get("job") or {}
        talent = payload.get("talent") or {}
        hard = [c for c in (job.get("hard_constraints") or []) if isinstance(c, dict)]
        softs = [s for s in (talent.get("soft_signals") or []) if isinstance(s, dict)]
        sources = [s for s in (talent.get("source_hypotheses") or []) if isinstance(s, dict)]

        def leaf(condition: dict[str, Any]) -> dict[str, Any]:
            return {"type": "leaf", "condition": condition}

        if len(hard) == 1:
            where_precise: dict[str, Any] = leaf(hard[0])
        elif hard:
            where_precise = {"type": "and", "children": [leaf(c) for c in hard]}
        else:
            where_precise = leaf(_loose_years_condition())

        precise = {
            "rationale": "硬条件精准匹配",
            "source_refs": [],
            "expected_yield": "high",
            "intent": {"where": where_precise, "semantic": softs[:2], "meta": {}},
        }

        broad_semantic = list(softs)
        for src in sources:
            if src.get("kind") == "industry":
                broad_semantic.append(
                    {"text": str(src.get("value") or ""), "fields": ["work_vector"], "weight": 1.2}
                )
        broad = {
            "rationale": "放宽硬条件的语义拓宽路径",
            "source_refs": [str(s.get("id")) for s in sources][:2],
            "expected_yield": "exploratory",
            "intent": {
                "where": leaf(_loose_years_condition()),
                "semantic": broad_semantic,
                "meta": {},
            },
        }
        cap = int(payload.get("max_paths") or 4)
        return {"paths": [precise, broad][: max(1, cap)]}

    def _judge(self, payload: dict[str, Any]) -> dict[str, Any]:
        abilities = [str(a) for a in payload.get("abilities") or []]
        judgments: list[dict[str, Any]] = []
        for candidate in payload.get("candidates") or []:
            profile = candidate.get("profile") or candidate.get("snapshot") or {}
            blob = " ".join(
                str(x)
                for x in [
                    profile.get("workYears"),
                    profile.get("summary"),
                    profile.get("skills"),
                    profile.get("recentRoles"),
                    profile.get("education"),
                ]
            ).lower()
            matched = [a for a in abilities if any(k in blob for k in _ability_keywords(a))]
            missing = [a for a in abilities if a not in matched]
            judgments.append(
                {
                    "doc_id": candidate.get("docId"),
                    "matched_abilities": matched,
                    "missing_abilities": missing,
                    "summary": f"命中 {len(matched)}/{len(abilities)} 项能力",
                }
            )
        return {"judgments": judgments}

    def _reflect(self, payload: dict[str, Any]) -> dict[str, Any]:
        top = payload.get("top_judgments") or []
        matched_counter = Counter(
            ability for j in top for ability in (j.get("matched_abilities") or [])
        )
        missing_counter = Counter(
            ability for j in top for ability in (j.get("missing_abilities") or [])
        )
        common = [ability for ability, _ in matched_counter.most_common(2)]
        gaps = [ability for ability, _ in missing_counter.most_common(2)]
        traits = [f"普遍命中：{ability}" for ability in common] or ["画像趋于稳定"]
        inferred = [
            {"text": ability, "fields": ["work_vector", "project_vector"], "weight": 1.2}
            for ability in common[:2]
        ]
        return {
            "common_traits": traits,
            "gaps": gaps,
            "inferred_soft_signals": inferred,
            "new_source_hints": [],
            "path_actions": [],
        }


def run_demo(
    query: str,
    *,
    top_k: int = 10,
    data_path: str | Path | None = None,
    event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    details: bool = True,
) -> SearchResponse:
    """Run one query end-to-end with the scripted LLM and the in-memory tool."""
    from search_agent.service import SearchAgentService
    from search_agent.tools.memory import InMemorySearchTool

    tool = InMemorySearchTool(data_path or DEMO_DATA)
    service = SearchAgentService(
        tool=tool,
        llm_client=ScriptedLlmClient(),
        event_sink=event_sink,
    )
    return service.search(SearchRequest(query=query, topK=top_k, details=details))


__all__ = [
    "ABILITY_LIBRARY",
    "DEMO_DATA",
    "DEMO_QUERIES",
    "ScriptedLlmClient",
    "run_demo",
]
