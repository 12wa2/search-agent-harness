"""In-memory SearchTool: a deterministic, dependency-free reference backend.

The agent only ever talks to the ``SearchTool`` protocol. This module is a
complete, honest implementation of that contract over a small JSONL corpus:

* ``search_with_intent`` walks the ``WhereNode`` tree as structured filters,
  scores semantic conditions by token coverage against the candidate text,
  and returns explainable hard-match facts.
* ``rerank_pool`` re-scores a cross-path candidate pool against job-level
  abilities and hard constraints.

Swap it for Elasticsearch + vectors + an LLM re-ranker and the harness does
not change -- that boundary is the point of this repository.

Note: ``total`` is the number of matching documents in this corpus (the whole
corpus is "indexed"), mirroring how the real backend reports ES totals.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Sequence

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    SearchToolResult,
    ToolCandidate,
)
from search_agent.models.api import CandidateItem
from search_agent.models.ir import (
    StructuredConditionIR,
    WhereNode,
)

logger = logging.getLogger(__name__)

__all__ = ["InMemorySearchTool", "load_candidates"]

_NUMERIC_OPS = {"gte", "lte", "range"}
_ASCII_RE = re.compile(r"[a-z0-9+#.]+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


# ── loading ────────────────────────────────────────────────────────────────


def load_candidates(
    source: str | Path | Sequence[dict[str, Any] | CandidateItem],
) -> list[CandidateItem]:
    """Load candidates from a JSONL path or an in-memory sequence."""
    if isinstance(source, (str, Path)):
        path = Path(source)
        rows: list[Any] = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        rows = list(source)
    items: list[CandidateItem] = []
    for row in rows:
        items.append(row if isinstance(row, CandidateItem) else CandidateItem.model_validate(row))
    return items


# ── candidate field extraction ─────────────────────────────────────────────


def _skill_names(item: CandidateItem) -> list[str]:
    return [str(s.get("name", "")) for s in item.skills or [] if isinstance(s, dict)]


def _first_present(entry: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = entry.get(key)
        if value:
            return str(value)
    return ""


def _companies(item: CandidateItem) -> list[str]:
    return [
        name
        for name in (
            _first_present(we, ("company_name", "companyName"))
            for we in item.workExperiences or []
            if isinstance(we, dict)
        )
        if name
    ]


def _titles(item: CandidateItem) -> list[str]:
    return [
        title
        for title in (
            _first_present(we, ("job_title", "jobTitle", "position", "positionName"))
            for we in item.workExperiences or []
            if isinstance(we, dict)
        )
        if title
    ]


def _education_values(item: CandidateItem, *keys: str) -> list[str]:
    return [
        value
        for value in (
            _first_present(edu, keys) for edu in item.educations or [] if isinstance(edu, dict)
        )
        if value
    ]


def _certificate_names(item: CandidateItem) -> list[str]:
    return [str(c.get("name", "")) for c in item.certificates or [] if isinstance(c, dict)]


def _as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _candidate_values(item: CandidateItem, field: str) -> list[str] | float | int | None:
    """Best-effort structured field extraction for demo filters."""
    if field == "name":
        return [item.name] if item.name else []
    if field == "skill":
        return _skill_names(item)
    if field == "company":
        return _companies(item)
    if field == "latest_company":
        return _companies(item)[:1]
    if field in ("position", "latest_work_job"):
        return _titles(item)
    if field == "intention_position":
        return list(item.expectedPositions or [])
    if field == "school":
        return _education_values(item, "school", "school_name", "schoolName")
    if field == "major":
        return _education_values(item, "major", "major_name", "majorName")
    if field == "degree":
        return _education_values(item, "degree", "degree_name", "degreeName")
    if field in ("city", "live_city"):
        return [item.currentResidence] if item.currentResidence else []
    if field == "intention_city":
        return list(item.expectedCities or [])
    if field == "certificate":
        return _certificate_names(item)
    if field == "language":
        return [
            str(lang.get("name", "")) for lang in item.languages or [] if isinstance(lang, dict)
        ]
    if field == "work_years":
        return item.workYears
    if field == "age":
        return item.age
    return None


# ── WHERE tree evaluation ──────────────────────────────────────────────────


def _leaf_matches(cond: StructuredConditionIR, item: CandidateItem) -> bool:
    values = _candidate_values(item, cond.field)
    if values is None:
        logger.debug("in-memory tool: unsupported field %r", cond.field)
        return False
    op = cond.operator
    if isinstance(values, (int, float)):
        if op not in _NUMERIC_OPS or not cond.values:
            return False
        numbers = [n for n in (_as_number(v) for v in cond.values) if n is not None]
        if not numbers:
            return False
        number = float(values)
        if op == "gte":
            return number >= numbers[0]
        if op == "lte":
            return number <= numbers[0]
        low, high = sorted(numbers[:2])
        return low <= number <= high  # range
    wanted = {str(v).strip().lower() for v in cond.values if str(v).strip()}
    if not wanted:
        return False
    candidates = {str(v).strip().lower() for v in values if str(v).strip()}
    if op in ("any", "eq"):
        return bool(candidates & wanted)
    if op == "all":
        return wanted <= candidates
    if op == "not":
        return not (candidates & wanted)
    return False


def _where_matches(node: WhereNode | None, item: CandidateItem) -> bool:
    if node is None:
        return True
    if node.type == "and":
        return all(_where_matches(child, item) for child in node.children)
    if node.type == "or":
        need = node.minimumShouldMatch or 1
        return sum(1 for child in node.children if _where_matches(child, item)) >= need
    if node.type == "not":
        return not _where_matches(node.child, item)
    if node.condition is not None:
        return _leaf_matches(node.condition, item)
    return True


def _collect_leaves(node: WhereNode | None) -> list[StructuredConditionIR]:
    if node is None:
        return []
    if node.type in ("and", "or"):
        out: list[StructuredConditionIR] = []
        for child in node.children:
            out.extend(_collect_leaves(child))
        return out
    if node.type == "not":
        return _collect_leaves(node.child)
    return [node.condition] if node.condition is not None else []


# ── soft scoring ───────────────────────────────────────────────────────────


def _tokens(text: str) -> list[str]:
    """ASCII words + CJK runs and bigrams, good enough for demo scoring."""
    lowered = (text or "").lower()
    tokens = [t for t in _ASCII_RE.findall(lowered) if len(t) >= 2]
    for run in _CJK_RUN_RE.findall(lowered):
        if len(run) >= 2:
            tokens.append(run)
        if len(run) >= 3:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def _candidate_text(item: CandidateItem) -> str:
    parts: list[str] = [item.name or "", item.summary or ""]
    parts.extend(_skill_names(item))
    parts.extend(_companies(item))
    parts.extend(_titles(item))
    parts.extend(item.expectedPositions or [])
    parts.extend(_certificate_names(item))
    parts.extend(_education_values(item, "school", "school_name", "schoolName"))
    parts.extend(_education_values(item, "major", "major_name", "majorName"))
    return " ".join(parts).lower()


def _coverage(text: str, blob: str) -> float:
    tokens = set(_tokens(text))
    if not tokens:
        return 0.0
    return sum(1 for token in tokens if token in blob) / len(tokens)


def _confidence(score: float) -> float:
    return round(min(1.0, score / 2.5), 3)


# ── the tool ───────────────────────────────────────────────────────────────


class InMemorySearchTool:
    """Deterministic reference ``SearchTool`` over an in-memory corpus."""

    def __init__(
        self,
        candidates: str | Path | Sequence[dict[str, Any] | CandidateItem],
    ) -> None:
        self._items = load_candidates(candidates)
        self._by_id = {item.docId: item for item in self._items}

    @property
    def size(self) -> int:
        return len(self._items)

    # -- SearchTool protocol ------------------------------------------------

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult:
        started = time.perf_counter()
        excluded = set(req.exclude_doc_ids or [])

        filtered: list[CandidateItem] = []
        for item in self._items:
            if item.docId in excluded:
                continue
            if _where_matches(req.intent.where, item):
                filtered.append(item)
        filtered_ms = int((time.perf_counter() - started) * 1000)

        score_started = time.perf_counter()
        leaves = _collect_leaves(req.intent.where)
        scored: list[tuple[float, list[dict[str, Any]], list[dict[str, Any]], CandidateItem]] = []
        for item in filtered:
            hard = [
                {
                    "field": cond.field,
                    "operator": cond.operator,
                    "values": list(cond.values),
                    "required": cond.required,
                    "matched": _leaf_matches(cond, item),
                }
                for cond in leaves
            ]
            blob = _candidate_text(item)
            score = 1.0
            soft: list[dict[str, Any]] = []
            for cond in req.intent.semantic or []:
                cov = _coverage(cond.text, blob)
                if cov > 0:
                    score += min(float(cond.weight), 2.0) * cov * 0.6
                soft.append({"text": cond.text, "weight": cond.weight, "coverage": round(cov, 3)})
            scored.append((round(score, 4), hard, soft, item))
        score_ms = int((time.perf_counter() - score_started) * 1000)

        scored.sort(key=lambda row: (-row[0], row[3].docId))
        total = len(scored)
        page = scored[: max(0, int(req.top_k))]

        items = [
            self._to_tool_candidate(score, hard, soft, item) for score, hard, soft, item in page
        ]
        if total <= 0:
            quality = "empty"
        elif len(items) >= max(3, int(req.top_k) // 4):
            quality = "rich"
        else:
            quality = "thin"

        es_query: dict[str, Any] | None = None
        if req.details:
            es_query = {
                "backend": "in-memory",
                "filteredCount": total,
                "excludedCount": len(excluded),
                "semanticConditions": [s.text for s in req.intent.semantic or []],
            }

        return SearchToolResult(
            items=items,
            total=total,
            total_relation="eq",
            quality=quality,
            es_query=es_query,
            timings_ms={
                "filter": filtered_ms,
                "score": score_ms,
                "total": int((time.perf_counter() - started) * 1000),
            },
        )

    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult:
        started = time.perf_counter()
        abilities = [str(a) for a in (req.job_context.get("abilities") or [])]
        job_hard: list[StructuredConditionIR] = []
        for raw in req.job_context.get("hard_constraints") or []:
            try:
                job_hard.append(StructuredConditionIR.model_validate(raw))
            except Exception:  # noqa: BLE001 - ignore malformed job facts
                continue

        scored: list[tuple[float, list[dict[str, Any]], ToolCandidate]] = []
        for candidate in req.candidates:
            blob = _candidate_text(candidate.item)
            ability_hits = sum(
                1 for ability in abilities if any(t in blob for t in _tokens(ability))
            )
            hard = [
                {
                    "field": cond.field,
                    "operator": cond.operator,
                    "values": list(cond.values),
                    "required": cond.required,
                    "matched": _leaf_matches(cond, candidate.item),
                }
                for cond in job_hard
            ]
            score = round(1.0 + 0.5 * ability_hits + 0.2 * sum(1 for h in hard if h["matched"]), 4)
            scored.append((score, hard, candidate))

        scored.sort(key=lambda row: (-row[0], row[2].doc_id))
        top = scored[: max(0, int(req.top_k))]
        items = [
            self._to_tool_candidate(score, hard, [], candidate.item)
            for score, hard, candidate in top
        ]
        return SearchToolResult(
            items=items,
            total=len(scored),
            total_relation="eq",
            quality="rich" if items else "empty",
            timings_ms={"total": int((time.perf_counter() - started) * 1000)},
        )

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _to_tool_candidate(
        score: float,
        hard: list[dict[str, Any]],
        soft: list[dict[str, Any]],
        item: CandidateItem,
    ) -> ToolCandidate:
        hard_hits = sum(1 for h in hard if h["matched"])
        soft_hits = sum(1 for s in soft if s["coverage"] > 0)
        if hard or soft:
            summary = f"命中 {hard_hits}/{len(hard)} 硬条件、{soft_hits}/{len(soft)} 语义信号"
        else:
            summary = "命中检索条件"
        explanation: dict[str, Any] = {
            "summary": summary,
            "hardMatches": hard,
            "softSignals": soft,
            "confidence": _confidence(score),
        }
        return ToolCandidate(
            doc_id=item.docId,
            score=score,
            item=item.model_copy(update={"score": score, "matchExplanation": explanation}),
            hard_matches=hard,
            match_explanation=explanation,
        )
