from search_agent.core.merge import (
    merge_into,
    merge_judgment,
    path_quality,
    rich_min_for,
)
from search_agent.models.artifacts import CandidateJudgment


def _j(doc_id, score, matched=None, missing=None, sources=None, summary=""):
    return CandidateJudgment(
        doc_id=doc_id,
        score=score,
        matched_abilities=matched or [],
        missing_abilities=missing or [],
        source_path_ids=sources or [],
        summary=summary,
    )


def test_merge_takes_max_score_and_summary():
    m = merge_judgment(_j("d1", 0.5, summary="old"), _j("d1", 0.8, summary="new"))
    assert m.score == 0.8
    assert m.summary == "new"


def test_merge_unions_matched_removes_from_missing():
    a = _j("d1", 0.5, matched=["A"], missing=["B", "C"])
    b = _j("d1", 0.8, matched=["B"], missing=["C", "D"])
    m = merge_judgment(a, b)
    assert set(m.matched_abilities) == {"A", "B"}
    assert set(m.missing_abilities) == {"C", "D"}


def test_merge_unions_sources():
    m = merge_judgment(_j("d1", 0.5, sources=["p1"]), _j("d1", 0.8, sources=["p2", "p1"]))
    assert m.source_path_ids == ["p1", "p2"]


def test_merge_into_dedup_by_doc_id():
    acc: dict[str, CandidateJudgment] = {}
    merge_into(acc, [_j("d1", 0.5), _j("d2", 0.3), _j("d1", 0.7, matched=["A"])])
    assert len(acc) == 2
    assert acc["d1"].score == 0.7
    assert acc["d1"].matched_abilities == ["A"]


def test_path_quality_buckets():
    assert path_quality(0, 0, 5) == "empty"
    assert path_quality(10, 2, 5) == "thin"
    assert path_quality(10, 5, 5) == "rich"


def test_rich_min_for():
    assert rich_min_for(20, 4) == 5
    assert rich_min_for(3, 4) == 3
