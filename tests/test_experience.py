"""Strategy memory tests: query families, priors, and outcome attribution.

Tests:
1. query_family: stable across ability order, splits different jobs, no text leak
2. ExperienceStore (in-process): record / priors / hints / snapshot
3. ExperienceStore (Redis): hincrby writes, bytes reads, failure degradation
4. attribute_qualified: first-source attribution, converged, must-ability filter
5. End to end: run twice -> priors recorded -> second run's plan sees them
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    SearchToolResult,
    ToolCandidate,
)
from search_agent.core.experience import (
    ExperienceStore,
    attribute_qualified,
    query_family,
)
from search_agent.llm.client import FakeLlmClient
from search_agent.models.api import CandidateItem, SearchRequest
from search_agent.models.artifacts import CandidateJudgment, JobAbility, JobProfile, SearchPath
from search_agent.models.ir import CandidateSearchIR, StructuredConditionIR, WhereNode
from search_agent.runner import LoopRunner
from search_agent.service import SearchAgentService

# -- helpers ---------------------------------------------------------------


def _job(*abilities: str, title: str = "engineer") -> JobProfile:
    return JobProfile(
        raw_query="q",
        title=title,
        must_abilities=[JobAbility(name=a) for a in abilities],
    )


def _ir() -> CandidateSearchIR:
    return CandidateSearchIR(
        where=WhereNode(
            type="leaf",
            condition=StructuredConditionIR(field="skill", operator="any", values=["Python"]),
        )
    )


def _path(path_id: str, origin: str = "plan") -> SearchPath:
    return SearchPath(id=path_id, intent=_ir(), origin=origin)


def _judgment(doc_id: str, matched: list[str], sources: list[str]) -> CandidateJudgment:
    return CandidateJudgment(
        doc_id=doc_id, matched_abilities=matched, source_path_ids=sources, summary="s"
    )


def _state(job, paths, judgments) -> dict:
    return {
        "job_profile": job,
        "paths": paths,
        "accumulated": {j.doc_id: j for j in judgments},
    }


# -- 1. query_family -------------------------------------------------------


class TestQueryFamily:
    def test_stable_across_ability_order(self):
        assert query_family(_job("Python", "Redis", "MySQL")) == query_family(
            _job("MySQL", "Python", "Redis")
        )

    def test_distinct_for_different_ability_sets(self):
        assert query_family(_job("Python")) != query_family(_job("Java"))

    def test_falls_back_to_title_without_abilities(self):
        fam = query_family(JobProfile(raw_query="q", title="data engineer"))
        assert fam == query_family(JobProfile(raw_query="q", title="data engineer"))
        assert fam != query_family(JobProfile(raw_query="q", title="backend engineer"))

    def test_family_is_opaque(self):
        fam = query_family(_job("Python"))
        assert len(fam) == 12
        assert "python" not in fam.lower()

    def test_tolerates_dicts_and_nones(self):
        assert query_family(None) == query_family({})


# -- 2. ExperienceStore (in-process) --------------------------------------


class TestExperienceStoreMemory:
    def test_unknown_family_is_empty(self):
        store = ExperienceStore(connect_redis=False)
        assert store.priors("nope") == {}
        assert store.hints("nope") == []

    def test_record_accumulates(self):
        store = ExperienceStore(connect_redis=False)
        store.record("f1", "loosen", qualified=2, tokens=100)
        store.record("f1", "loosen", qualified=4, tokens=50)
        stat = store.priors("f1")["loosen"]
        assert stat.runs == 2
        assert stat.qualified == 6
        assert stat.tokens == 150
        assert stat.avg_qualified == 3.0

    def test_priors_sorted_by_average_yield(self):
        store = ExperienceStore(connect_redis=False)
        store.record("f1", "tighten", qualified=1)
        store.record("f1", "tighten", qualified=0)
        store.record("f1", "add_path", qualified=3)
        assert list(store.priors("f1")) == ["add_path", "tighten"]

    def test_hints_shape_and_limit(self):
        store = ExperienceStore(connect_redis=False)
        for name in ("a", "b", "c", "d"):
            store.record("f1", name, qualified=1)
        hints = store.hints("f1", limit=2)
        assert len(hints) == 2
        assert set(hints[0]) == {"strategy", "runs", "avg_qualified"}
        assert hints[0]["runs"] == 1

    def test_negative_counts_are_clamped(self):
        store = ExperienceStore(connect_redis=False)
        store.record("f1", "plan", qualified=-5, tokens=-3)
        stat = store.priors("f1")["plan"]
        assert stat.qualified == 0
        assert stat.tokens == 0
        assert stat.runs == 1

    def test_snapshot_is_a_copy(self):
        store = ExperienceStore(connect_redis=False)
        store.record("f1", "plan", qualified=1)
        snap = store.snapshot()
        assert snap == {"f1": {"plan": {"runs": 1, "qualified": 1, "tokens": 0}}}
        snap["f1"]["plan"]["runs"] = 99
        assert store.priors("f1")["plan"].runs == 1


# -- 3. ExperienceStore (Redis) -------------------------------------------


class TestExperienceStoreRedis:
    def test_record_writes_field_counters_and_ttl(self):
        mock = MagicMock()
        store = ExperienceStore(ttl_seconds=123, connect_redis=False)
        store._redis = mock
        store.record("fam", "loosen", qualified=3, tokens=9)

        pipe = mock.pipeline.return_value
        written = {c.args[1]: c.args[2] for c in pipe.hincrby.call_args_list}
        assert written == {"loosen:runs": 1, "loosen:qualified": 3, "loosen:tokens": 9}
        for call in pipe.hincrby.call_args_list:
            assert call.args[0] == "search_agent:experience:fam"
        pipe.expire.assert_called_once_with("search_agent:experience:fam", 123)
        pipe.execute.assert_called_once()

    def test_priors_read_back_from_redis(self):
        mock = MagicMock()
        mock.hgetall.return_value = {
            b"loosen:runs": b"2",
            b"loosen:qualified": b"5",
            b"loosen:tokens": b"40",
            b"tighten:runs": b"1",
            b"tighten:qualified": b"1",
            b"garbage": b"x",
            b"loosen:junk": b"1",
        }
        store = ExperienceStore(connect_redis=False)
        store._redis = mock
        priors = store.priors("fam")
        assert priors["loosen"].runs == 2
        assert priors["loosen"].qualified == 5
        assert priors["tighten"].avg_qualified == 1.0
        assert "garbage" not in priors
        assert list(priors) == ["loosen", "tighten"]

    def test_redis_read_failure_degrades_to_memory(self):
        mock = MagicMock()
        mock.hgetall.side_effect = RuntimeError("redis down")
        store = ExperienceStore(connect_redis=False)
        store._redis = mock
        store.record("fam", "plan", qualified=1)
        assert store.priors("fam")["plan"].qualified == 1

    def test_redis_write_failure_keeps_memory_copy(self):
        mock = MagicMock()
        mock.pipeline.side_effect = RuntimeError("redis down")
        store = ExperienceStore(connect_redis=False)
        store._redis = mock
        store.record("fam", "plan", qualified=1)
        assert store._memory("fam")["plan"].qualified == 1


# -- 4. attribute_qualified ------------------------------------------------


class TestAttributeQualified:
    def test_attributes_to_first_source_path_origin(self):
        job = _job("Python")
        paths = [_path("p1", "loosen"), _path("p2", "add_path")]
        acc = [
            _judgment("d1", ["Python"], ["p1", "p2"]),
            _judgment("d2", ["Python"], ["p2"]),
        ]
        assert attribute_qualified(_state(job, paths, acc)) == {"loosen": 1, "add_path": 1}

    def test_converged_paths_counted_separately(self):
        job = _job("Python")
        acc = [_judgment("d1", ["Python"], ["final"])]
        assert attribute_qualified(_state(job, [_path("p1")], acc)) == {"converged": 1}

    def test_requires_a_must_ability_match(self):
        job = _job("Python")
        acc = [
            _judgment("d1", ["Java"], ["p1"]),
            _judgment("d2", [], ["p1"]),
            _judgment("d3", ["Python"], ["p1"]),
        ]
        assert attribute_qualified(_state(job, [_path("p1")], acc)) == {"plan": 1}

    def test_unknown_path_defaults_to_plan(self):
        job = _job("Python")
        acc = [_judgment("d1", ["Python"], ["ghost"])]
        assert attribute_qualified(_state(job, [_path("p1")], acc)) == {"plan": 1}

    def test_no_job_profile_counts_any_match(self):
        acc = [_judgment("d1", ["Python"], ["final"])]
        state = {"paths": [], "accumulated": {"d1": acc[0]}}
        assert attribute_qualified(state) == {"converged": 1}

    def test_empty_state_is_empty(self):
        assert attribute_qualified({}) == {}


# -- 5. End to end: priors feed the next plan ------------------------------


def _candidate(doc_id: str, score: float = 0.8) -> ToolCandidate:
    # summary carries real evidence so the judge grounding step keeps "Python".
    return ToolCandidate(
        doc_id=doc_id,
        score=score,
        item=CandidateItem(
            docId=doc_id, score=score, name=f"Candidate {doc_id}", summary="Python engineer"
        ),
        hard_matches=[{"field": "skill", "matched": True}],
    )


class _FakeTool:
    def __init__(self, items):
        self._items = items

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult:
        return SearchToolResult(
            items=self._items, total=len(self._items), quality="rich", timings_ms={"total": 100}
        )

    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult:
        return SearchToolResult(
            items=self._items[: req.top_k],
            total=len(self._items),
            quality="rich",
            timings_ms={"total": 200},
        )


def _llm() -> FakeLlmClient:
    return FakeLlmClient(
        responses={
            "agent_understand": {
                "title": "Python engineer",
                "must_abilities": [{"name": "Python", "level": "must"}],
                "needs_clarification": False,
                "talent_profile": {"must_keywords": ["Python"]},
            },
            "agent_plan": {
                "paths": [
                    {"rationale": "direct", "intent": _ir().model_dump(), "expected_yield": "high"}
                ]
            },
            "agent_judge": {
                "judgments": [
                    {
                        "doc_id": f"d{i}",
                        "matched_abilities": ["Python"],
                        "missing_abilities": [],
                        "summary": "ok",
                    }
                    for i in range(5)
                ]
            },
        }
    )


def _plan_payloads(llm: FakeLlmClient) -> list[dict]:
    return [json.loads(user) for name, user in llm.calls if name == "agent_plan"]


def test_first_run_records_priors_second_run_sees_them():
    store = ExperienceStore(connect_redis=False)
    llm = _llm()
    svc = SearchAgentService(
        tool=_FakeTool([_candidate(f"d{i}") for i in range(5)]),
        llm_client=llm,
        runner=LoopRunner(),
        experience=store,
    )
    req = SearchRequest(query="Python engineer", companyId=39978, topK=20, details=True)

    svc.search(req)
    (family,) = store.snapshot()
    assert store.snapshot()[family]["plan"]["qualified"] == 5
    assert "strategyPriors" not in _plan_payloads(llm)[0]

    svc.search(req)
    payload = _plan_payloads(llm)[1]
    assert payload["strategyPriors"] == [{"strategy": "plan", "runs": 1, "avg_qualified": 5.0}]
    assert store.snapshot()[family]["plan"]["runs"] == 2


def test_broken_experience_store_never_breaks_a_search():
    class _BoomStore(ExperienceStore):
        def record(self, *args, **kwargs):
            raise RuntimeError("store down")

    svc = SearchAgentService(
        tool=_FakeTool([_candidate(f"d{i}") for i in range(5)]),
        llm_client=_llm(),
        runner=LoopRunner(),
        experience=_BoomStore(connect_redis=False),
    )
    resp = svc.search(SearchRequest(query="Python engineer", companyId=39978, topK=20))
    assert resp.total == 5


def test_priors_are_exposed_through_the_service_property():
    store = ExperienceStore(connect_redis=False)
    store.record("fam", "plan", qualified=1)
    svc = SearchAgentService(tool=_FakeTool([]), llm_client=_llm(), experience=store)
    assert svc.experience.priors("fam")["plan"].runs == 1
