"""Tests for judge-stage grounding with Chinese ability names."""

from __future__ import annotations

from types import SimpleNamespace

from search_agent.contract.tool import ToolCandidate
from search_agent.core.budget import BudgetState
from search_agent.llm.client import FakeLlmClient
from search_agent.llm.prompts import build_judge_payload, judge_prompt
from search_agent.models.api import CandidateItem
from search_agent.models.artifacts import (
    Budget,
    CandidateJudgment,
)
from search_agent.stages.base import Deps
from search_agent.stages.judge import (
    _chinese_keywords,
    _ground_judgment,
    _grounding_keywords,
    _judge_path,
)

# -- _chinese_keywords --------------------------------------------------


def test_chinese_keywords_short_run():
    # 3-char run: whole run only (no sliding windows for len < 4)
    kws = _chinese_keywords("高可用")
    assert "高可用" in kws
    assert len(kws) == 1


def test_chinese_keywords_long_run():
    # 6-char run: whole + 3-char sliding windows only
    name = "微服务架构设计"
    kws = _chinese_keywords(name)
    assert name in kws
    assert "微服务" in kws  # 3-char window
    assert "架构设" in kws  # 3-char window
    # 2-char windows are NOT generated (too generic)
    assert "设计" not in kws


def test_chinese_keywords_ascii_only():
    assert _chinese_keywords("K8s+Docker") == set()


def test_chinese_keywords_mixed():
    name = "K8s+微服务"
    kws = _chinese_keywords(name)
    assert "微服务" in kws
    assert len(kws) == 1


# -- _grounding_keywords ------------------------------------------------


def test_grounding_keywords_merges_chinese():
    name = "微服务架构设计"
    kws = _grounding_keywords(name, [])
    assert "微服务" in kws


def test_grounding_keywords_keeps_ascii():
    kws = _grounding_keywords("Python", [])
    assert "python" in kws


def test_grounding_keywords_keeps_hints():
    kws = _grounding_keywords("Python", ["flask", "django"])
    assert "flask" in kws
    assert "django" in kws


# -- _ground_judgment ---------------------------------------------------


def _make_item(**kw):
    return SimpleNamespace(
        skills=kw.get("skills", []),
        workExperiences=kw.get("workExperiences", []),
        summary=kw.get("summary", ""),
        expectedPositions=kw.get("expectedPositions", []),
        certificates=kw.get("certificates", []),
    )


def _make_candidate(item):
    return SimpleNamespace(doc_id="d1", score=0.9, item=item, hard_matches=[])


def test_ground_keeps_chinese_ability_with_evidence():
    """Chinese ability grounded when candidate text contains a sub-fragment."""
    ab = "微服务架构设计"
    item = _make_item(
        skills=[{"name": "微服务"}, {"name": "Spring Cloud"}],
        summary="",
    )
    j = CandidateJudgment(
        doc_id="d1",
        score=0.9,
        matched_abilities=[ab],
        missing_abilities=[],
        hard_matches=[],
        summary="",
        source_path_ids=["p1"],
    )
    result = _ground_judgment(j, _make_candidate(item), {})
    assert ab in result.matched_abilities
    assert ab not in result.missing_abilities


def test_ground_demotes_chinese_ability_without_evidence():
    """Chinese ability demoted when candidate text has no matching fragment."""
    ab = "微服务架构设计"
    item = _make_item(
        skills=[{"name": "Photoshop"}],
        summary="前端设计师",
    )
    j = CandidateJudgment(
        doc_id="d1",
        score=0.9,
        matched_abilities=[ab],
        missing_abilities=[],
        hard_matches=[],
        summary="",
        source_path_ids=["p1"],
    )
    result = _ground_judgment(j, _make_candidate(item), {})
    assert ab not in result.matched_abilities
    assert ab in result.missing_abilities


def test_ground_keeps_ascii_ability_with_evidence():
    j = CandidateJudgment(
        doc_id="d1",
        score=0.9,
        matched_abilities=["Python"],
        missing_abilities=[],
        hard_matches=[],
        summary="",
        source_path_ids=["p1"],
    )
    item = _make_item(skills=[{"name": "Python"}])
    result = _ground_judgment(j, _make_candidate(item), {})
    assert "Python" in result.matched_abilities


def test_build_judge_payload_sends_only_compact_resume_evidence():
    item = CandidateItem(
        docId="d1",
        name="Synthetic Candidate",
        workYears=8,
        summary="Built a high-throughput platform. " * 40,
        skills=[
            {"name": f"Skill{i}", "level": "expert", "years": i, "description": "noise"}
            for i in range(20)
        ],
        workExperiences=[
            {
                "company_name": f"Company{i}",
                "job_title": f"Engineer{i}",
                "startDate": "2020-01-01",
                "endDate": f"20{14 + i}-01-01",
                "description": "long resume description that judge does not need",
            }
            for i in range(4)
        ],
        educations=[
            {
                "school": "Example University",
                "major": "Computer Science",
                "degree": "Bachelor",
                "description": "noise",
            }
        ],
    )
    candidate = ToolCandidate(doc_id="d1", score=0.9, item=item, hard_matches=[])

    payload = build_judge_payload("Find Python engineers", ["Python"], [candidate])
    profile = payload["candidates"][0]["profile"]

    assert "snapshot" not in payload["candidates"][0]
    assert profile["workYears"] == 8
    assert len(profile["skills"]) == 12
    assert all(isinstance(name, str) for name in profile["skills"])
    assert len(profile["recentRoles"]) == 2
    assert [role["company"] for role in profile["recentRoles"]] == ["Company3", "Company2"]
    assert set(profile["recentRoles"][0]) == {"company", "title", "period"}
    assert "description" not in profile["recentRoles"][0]
    assert profile["education"] == {"degree": "Bachelor", "major": "Computer Science"}
    assert len(profile["summary"]) <= 160


def test_judge_schema_requests_only_compact_verdict_labels():
    _, schema = judge_prompt()
    item = schema["properties"]["judgments"]["items"]

    assert item["required"] == ["doc_id", "matched_abilities"]
    assert "summary" not in item["properties"]
    assert "missing_abilities" not in item["properties"]
    assert item["additionalProperties"] is False


def test_judge_derives_missing_abilities_from_matched_labels():
    item = CandidateItem(docId="d1", skills=[{"name": "Python"}])
    candidate = ToolCandidate(doc_id="d1", score=0.9, item=item, hard_matches=[])
    llm = FakeLlmClient(
        responses={
            "agent_judge": {
                "judgments": [{"doc_id": "d1", "matched_abilities": ["Python"]}]
            }
        }
    )
    deps = Deps(
        llm=llm,
        tool=SimpleNamespace(),
        budget_state=BudgetState(budget=Budget()),
    )

    judgments = _judge_path(
        deps,
        query="Python engineer",
        abilities=["Python", "Docker"],
        path_id="p1",
        cands=[candidate],
        ability_hints={"Python": ["Python"], "Docker": ["Docker"]},
    )

    assert judgments[0].matched_abilities == ["Python"]
    assert judgments[0].missing_abilities == ["Docker"]
