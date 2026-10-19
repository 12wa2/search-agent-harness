"""judge: ToolCandidate + abilities -> CandidateJudgment.

Parsing is tolerant: each LLM judgment is matched against candidates by doc_id
(accepting doc_id/docId/id/candidateProfileId aliases) and built inside a
try/except, so a malformed judgment never aborts the path. Unmatched doc_ids
are logged at debug level for diagnosis.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from search_agent.contract.tool import ToolCandidate
from search_agent.core.merge import merge_into
from search_agent.llm.prompts import (
    build_judge_payload,
    judge_prompt,
)
from search_agent.models.artifacts import (
    CandidateJudgment,
)
from search_agent.stages.base import Deps

logger = logging.getLogger(__name__)


def _resolve_doc_id(j: dict[str, Any]) -> str | None:
    for key in ("doc_id", "docId", "id", "candidateProfileId", "candidate_id"):
        val = j.get(key)
        if val:
            return str(val)
    return None


_ASCII_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#./_-]*")
_CHINESE_RUN = re.compile(r"[一-鿿]+")


def _candidate_text(item: Any) -> str:
    """Lowercase text blob from real candidate content (ground truth)."""
    parts: list[str] = []
    for s in getattr(item, "skills", None) or []:
        if isinstance(s, dict):
            parts.append(str(s.get("name", "")))
    for we in getattr(item, "workExperiences", None) or []:
        if isinstance(we, dict):
            parts.append(str(we.get("company_name") or we.get("companyName") or ""))
            parts.append(str(we.get("job_title") or we.get("jobTitle") or we.get("position") or ""))
    parts.append(str(getattr(item, "summary", "") or ""))
    for p in getattr(item, "expectedPositions", None) or []:
        parts.append(str(p))
    for c in getattr(item, "certificates", None) or []:
        if isinstance(c, dict):
            parts.append(str(c.get("name", "")))
    return " ".join(parts).lower()


def _chinese_keywords(name: str) -> set[str]:
    """Extract Chinese keyword fragments for grounding.

    For each Chinese run:
    - len 2-3: the whole run (e.g. a 3-char tech term)
    - len >= 4: the whole run + 3-char sliding windows
      so a 6-char phrase yields its 3-char sub-fragments.
    """
    kws: set[str] = set()
    for run in _CHINESE_RUN.findall(name or ""):
        if len(run) < 2:
            continue
        kws.add(run)
        if len(run) >= 4:
            # 3-char sliding windows only: 2-char fragments
            # are too generic and cause false positives.
            for i in range(len(run) - 2):
                kws.add(run[i : i + 3])
    return kws


def _grounding_keywords(ability_name: str, hints: list[str]) -> set[str]:
    """Keywords evidencing an ability: evidence_hints + ASCII tokens from its name."""
    kws: set[str] = set()
    for h in hints or []:
        if h:
            kws.add(str(h).lower())
    for tok in _ASCII_TOKEN.findall(ability_name or ""):
        if len(tok) >= 2:
            kws.add(tok.lower())
    kws |= _chinese_keywords(ability_name or "")
    return kws


def _ground_judgment(judgment, candidate, ability_hints):
    """Demote claimed matched abilities not evidenced in candidate content.

    Conservative: an ability with no grounding keywords (no hints, no ASCII token
    in its name) cannot be verified and is kept to avoid false negatives.
    """
    text = _candidate_text(candidate.item)
    grounded = []
    demoted = []
    for ab in judgment.matched_abilities:
        kws = _grounding_keywords(ab, ability_hints.get(ab, []))
        if not kws:
            grounded.append(ab)
            continue
        if any(k in text for k in kws):
            grounded.append(ab)
        else:
            demoted.append(ab)
    if demoted:
        return judgment.model_copy(
            update={
                "matched_abilities": grounded,
                "missing_abilities": list(judgment.missing_abilities) + demoted,
            }
        )
    return judgment


def _judge_path(
    deps: Deps,
    *,
    query: str,
    abilities: list[str],
    path_id: str,
    cands: list[ToolCandidate],
    ability_hints: dict[str, list[str]],
) -> list[CandidateJudgment]:
    if not cands:
        return []
    system, schema = judge_prompt()
    user = json.dumps(
        build_judge_payload(query, abilities, cands),
        ensure_ascii=False,
    )
    data = deps.llm.complete_json(
        system=system, user=user, schema_name="agent_judge", schema=schema
    )
    deps.budget_state.record_llm()

    # robust lookup: primary doc_id + candidateProfileId fallback
    by_doc: dict[str, ToolCandidate] = {}
    for c in cands:
        by_doc[c.doc_id] = c
        cp = getattr(c.item, "candidateProfileId", None)
        if cp:
            by_doc.setdefault(str(cp), c)

    out: list[CandidateJudgment] = []
    raw = data.get("judgments", []) or []
    for j in raw:
        if not isinstance(j, dict):
            continue
        doc_id = _resolve_doc_id(j)
        c = by_doc.get(doc_id) if doc_id else None
        if not c:
            logger.debug("judge path=%s: unmatched doc_id=%r", path_id, doc_id)
            continue
        try:
            matched_raw = [str(value) for value in (j.get("matched_abilities") or [])]
            if matched_raw:
                matched_set = set(matched_raw)
                matched_abilities = [name for name in abilities if name in matched_set]
            else:
                missing_raw = {str(value) for value in (j.get("missing_abilities") or [])}
                matched_abilities = (
                    [name for name in abilities if name not in missing_raw] if missing_raw else []
                )
            missing_abilities = [name for name in abilities if name not in matched_abilities]
            judgment = CandidateJudgment(
                doc_id=c.doc_id,
                score=c.score,
                matched_abilities=matched_abilities,
                missing_abilities=missing_abilities,
                hard_matches=c.hard_matches,
                summary=str(j.get("summary", "") or ""),
                source_path_ids=[path_id],
            )
            judgment = _ground_judgment(judgment, c, ability_hints)
            out.append(judgment)
        except Exception:  # noqa: BLE001 - skip malformed judgment
            logger.debug("judge path=%s: skip invalid judgment: %s", path_id, j)
            continue

    if len(out) < len(cands):
        logger.debug(
            "judge path=%s: %d judgments matched / %d candidates",
            path_id,
            len(out),
            len(cands),
        )
    return out


def run_judge(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    job = state["job_profile"]
    abilities = [a.name for a in job.must_abilities + job.nice_abilities]
    ability_hints = {
        a.name: list(a.evidence_hints or []) for a in job.must_abilities + job.nice_abilities
    }
    acc = dict(state.get("accumulated", {}))
    before = len(acc)

    updated_results = []
    for pr in state.get("path_results", []):
        if pr.judgments:
            updated_results.append(pr)
            continue
        judgments = _judge_path(
            deps,
            query=job.raw_query,
            abilities=abilities,
            path_id=pr.path_id,
            cands=pr.tool_result.items,
            ability_hints=ability_hints,
        )
        updated_results.append(pr.model_copy(update={"judgments": judgments}))
        merge_into(acc, judgments)

    return {
        "path_results": updated_results,
        "accumulated": acc,
        "new_qualified_last_round": len(acc) - before,
        "phase": "evaluate",
    }


__all__ = ["run_judge"]
