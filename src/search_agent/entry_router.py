"""LLM review for weak direct-search entry decisions.

The deterministic direct-search gate keeps the common path cheap. Some queries
only produce weak structured evidence, however, such as a city or years filter
without a role or skill. Those cases are reviewed by one small LLM call before
the request chooses between ``direct_search`` and the Agent Harness.

Rules remain authoritative outside the grey zone:

* no direct plan -> Agent, without an extra LLM call;
* a strong direct plan -> ``direct_search``, without an extra LLM call;
* a weak direct plan -> one LLM review may keep or veto the direct route;
* an unavailable/invalid review keeps the deterministic direct route;
* an explicit low-confidence review falls back to Agent.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from search_agent.direct_search import DirectSearchPlan
from search_agent.llm.client import LlmClient

logger = logging.getLogger(__name__)

EntryRoute = Literal["direct_search", "agent"]
EntryRouteSource = Literal["rule", "llm", "fallback"]

ENTRY_ROUTE_SCHEMA_NAME = "agent_entry_route"
_MIN_CONFIDENCE = 0.65

_ENTRY_ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ["direct_search", "agent"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reason": {"type": "string"},
    },
    "required": ["route", "confidence", "reason"],
    "additionalProperties": False,
}

_ENTRY_ROUTE_SYSTEM = (
    "你是招聘搜索系统的入口路由复核器。只输出严格 JSON，不要输出额外文本。\n"
    "规则层只检测到弱直连信号，请复核当前 Query 是否可以直接执行一次结构化检索，"
    "还是必须进入 Agent Harness。\n"
    "direct_search：岗位、技能、地点或年限等条件已经足够清晰，不需要澄清、偏好权衡、"
    "抽象能力判断、能力迁移、多路径探索或跨来源比较。\n"
    "agent：需要理解隐含需求、职业迁移、开放偏好、多路径探索，或当前条件不足以安全检索。\n"
    "安全优先：不确定时选择 agent。confidence 必须在 0 到 1 之间。"
)


@dataclass(frozen=True)
class EntryRouteDecision:
    """The final pre-Harness route decision and how it was reached."""

    route: EntryRoute
    source: EntryRouteSource
    confidence: float
    reason: str = ""
    llm_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "source": self.source,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "llmUsed": self.llm_used,
        }


def is_strong_direct_plan(plan: DirectSearchPlan) -> bool:
    """Return True when the rule plan has enough evidence to skip LLM review."""
    if plan.skills:
        return True
    return bool(plan.role and (plan.years or plan.cities))


def choose_entry_route(
    query: str,
    plan: DirectSearchPlan | None,
    llm_client: LlmClient | None = None,
    *,
    has_history: bool = False,
) -> EntryRouteDecision:
    """Choose direct vs Agent, consulting the LLM only in the weak-plan zone."""
    if plan is None:
        return EntryRouteDecision(
            route="agent",
            source="rule",
            confidence=1.0,
            reason="rules_require_agent",
        )
    if is_strong_direct_plan(plan):
        return EntryRouteDecision(
            route="direct_search",
            source="rule",
            confidence=0.95,
            reason="strong_direct_signals",
        )
    if llm_client is None:
        return EntryRouteDecision(
            route="direct_search",
            source="rule",
            confidence=0.55,
            reason="weak_direct_signals_no_reviewer",
        )

    reviewed = _review_weak_plan(
        query,
        plan,
        llm_client,
        has_history=has_history,
    )
    if reviewed is None:
        return EntryRouteDecision(
            route="direct_search",
            source="fallback",
            confidence=0.55,
            reason="entry_route_review_unavailable",
            llm_used=True,
        )

    route, confidence, reason = reviewed
    if confidence < _MIN_CONFIDENCE:
        return EntryRouteDecision(
            route="agent",
            source="fallback",
            confidence=confidence,
            reason=f"entry_route_low_confidence: {reason}",
            llm_used=True,
        )
    return EntryRouteDecision(
        route=route,
        source="llm",
        confidence=confidence,
        reason=reason,
        llm_used=True,
    )


def _review_weak_plan(
    query: str,
    plan: DirectSearchPlan,
    llm_client: LlmClient,
    *,
    has_history: bool,
) -> tuple[EntryRoute, float, str] | None:
    payload = {
        "query": query,
        "hasConversationHistory": has_history,
        "rulePlan": {
            "title": plan.title,
            "role": plan.role,
            "skills": list(plan.skills),
            "cities": list(plan.cities),
            "years": list(plan.years) if plan.years else None,
            "hardConstraints": [
                constraint.model_dump() for constraint in plan.job_profile.hard_constraints
            ],
        },
        "reviewReason": "rules_found_only_weak_direct_signals",
    }
    try:
        data = llm_client.complete_json(
            system=_ENTRY_ROUTE_SYSTEM,
            user=json.dumps(payload, ensure_ascii=False),
            schema_name=ENTRY_ROUTE_SCHEMA_NAME,
            schema=_ENTRY_ROUTE_SCHEMA,
        )
    except Exception:  # noqa: BLE001 - routing review must not break the request
        logger.debug("entry route review failed", exc_info=True)
        return None

    route = str(data.get("route") or "").strip()
    if route not in {"direct_search", "agent"}:
        return None
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        return None
    confidence = min(1.0, max(0.0, confidence))
    reason = str(data.get("reason") or "").strip()[:240]
    return route, confidence, reason  # type: ignore[return-value]


__all__ = [
    "ENTRY_ROUTE_SCHEMA_NAME",
    "EntryRouteDecision",
    "choose_entry_route",
    "is_strong_direct_plan",
]
