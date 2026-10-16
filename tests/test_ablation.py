"""Ablation grid tests: switches flip exactly one mechanism, and the gate bites.

Tests:
1. Grid definitions: exactly-one-switch rows, lookup, reference/baseline rows
2. Stage-level switches: dedup off accepts a tried signature, converged search off
   skips the final search, single_pass row equals run_single_pass
3. Aggregation + baseline payload shape
4. compare_baseline: identical passes; ratio drop / cost rise / missing row fail
5. Determinism: the same corpus + seed yields identical metrics (CI gate premise)
6. CLI smoke: `eval --ablate` runs the whole grid offline
"""

from __future__ import annotations

import json

from search_agent.core.ablation import (
    ABLATION_ROWS,
    BASELINE_ROW,
    REFERENCE_ROW,
    AblationSwitches,
    row_by_name,
)
from search_agent.core.budget import BudgetState
from search_agent.corpus import generate_corpus
from search_agent.demo import DEMO_DATA, DEMO_QUERIES, ScriptedLlmClient
from search_agent.eval import (
    AblationScore,
    _score_row,
    baseline_payload,
    compare_baseline,
    run_ablation_row,
    run_single_pass,
)
from search_agent.models.artifacts import Budget, PathAction, SearchPath
from search_agent.models.ir import CandidateSearchIR, StructuredConditionIR, WhereNode
from search_agent.stages.base import Deps
from search_agent.stages.finalize import run_finalize
from search_agent.stages.plan import _dedup_disabled
from search_agent.stages.revise import _apply_action
from search_agent.tools.memory import InMemorySearchTool

# -- helpers ---------------------------------------------------------------


def _ir(values=("Python",)) -> CandidateSearchIR:
    return CandidateSearchIR(
        where=WhereNode(
            type="leaf",
            condition=StructuredConditionIR(field="skill", operator="any", values=list(values)),
        )
    )


def _deps(*, switches: AblationSwitches | None = None) -> Deps:
    bs = BudgetState(budget=Budget())
    return Deps(
        llm=ScriptedLlmClient(),
        tool=InMemorySearchTool(DEMO_DATA),
        budget_state=bs,
        ablation=switches,
    )


# -- 1. grid definitions ---------------------------------------------------


class TestGrid:
    def test_rows_are_unique_and_looked_up(self):
        names = [row.name for row in ABLATION_ROWS]
        assert len(names) == len(set(names))
        for name in names:
            assert row_by_name(name) is not None
        assert row_by_name("nope") is None

    def test_reference_and_baseline_rows_exist(self):
        assert row_by_name(REFERENCE_ROW) is not None
        assert row_by_name(BASELINE_ROW) is not None

    def test_every_row_flips_at_most_one_switch(self):
        for row in ABLATION_ROWS:
            flips = sum(
                (
                    row.switches.no_signature_dedup,
                    row.switches.no_converged_search,
                    row.max_paths_per_iteration is not None,
                    row.max_iterations is not None,
                    row.skip_reflection,
                    row.single_pass,
                )
            )
            assert flips <= 1, row.name

    def test_full_loop_row_is_pristine(self):
        row = row_by_name(REFERENCE_ROW)
        assert row.switches == AblationSwitches()
        assert not row.single_pass
        assert row.max_paths_per_iteration is None
        assert row.skip_reflection is False


# -- 2. stage-level switches ----------------------------------------------


class TestSwitches:
    def test_dedup_default_is_on(self):
        assert _dedup_disabled(_deps()) is False

    def test_dedup_switch_only_under_ablation(self):
        deps = _deps(switches=AblationSwitches(no_signature_dedup=True))
        assert _dedup_disabled(deps) is True

    def test_revise_rejects_seen_signature_unless_dedup_off(self):
        intent = _ir()
        from search_agent.core.signature import intent_signature

        sig = intent_signature(intent)
        action = PathAction(kind="add_path", rationale="r", intent=intent)

        def _apply(dedup: bool):
            paths: list[SearchPath] = []
            new_paths: list[SearchPath] = []
            _apply_action(action, paths, {sig}, set(), new_paths, dedup=dedup)
            return new_paths

        assert _apply(dedup=True) == []
        assert len(_apply(dedup=False)) == 1

    def test_converged_search_switch_skips_final_search(self):
        state = {
            "need_clarify": False,
            "top_k": 5,
            "accumulated": {},
            "path_results": [],
            "paths": [],
            "query": "q",
            "company_id": 1,
            "hr_id": None,
            "permission": None,
            "job_profile": None,
            "reflections": [object()],  # non-empty -> would trigger the converged search
        }
        from search_agent.models.artifacts import JobProfile

        state["job_profile"] = JobProfile(raw_query="q", title="t")
        deps = _deps(switches=AblationSwitches(no_converged_search=True))
        out = run_finalize(state, deps)
        assert out["final_search_total"] == 0

    def test_single_pass_row_reuses_the_single_pass_runner(self):
        row = row_by_name("single_pass")
        tool = InMemorySearchTool(DEMO_DATA)
        llm = ScriptedLlmClient()
        result = run_ablation_row(row, ScriptedLlmClient(), tool, DEMO_QUERIES[0], top_k=5)
        expected = run_single_pass(llm, tool, DEMO_QUERIES[0], top_k=5)
        assert result.name == "single_pass"
        assert result.doc_ids == expected.doc_ids

    def test_every_row_runs_end_to_end(self):
        tool = InMemorySearchTool(DEMO_DATA)
        for row in ABLATION_ROWS:
            result = run_ablation_row(row, ScriptedLlmClient(), tool, DEMO_QUERIES[0], top_k=5)
            assert result.name == row.name
            assert result.llm_calls >= 1


# -- 3. aggregation --------------------------------------------------------


class TestAggregation:
    def test_score_row_shapes(self):
        corpus = generate_corpus(size=20, seed=7)
        tool = InMemorySearchTool(corpus.records())
        row = row_by_name(REFERENCE_ROW)
        results = [
            run_ablation_row(row, ScriptedLlmClient(), tool, spec.text, top_k=5)
            for spec in corpus.queries
        ]
        score = _score_row(row, results, corpus, [r.surfaced_ids for r in results])
        assert 0.0 <= score.recall <= 1.0
        assert 0.0 <= score.precision <= 1.0
        assert score.llm_calls == sum(r.llm_calls for r in results)
        # comparing a row against itself: nothing is unique
        assert score.unique == 0
        assert score.unique_relevant == 0

    def test_baseline_payload_is_serializable(self):
        score = AblationScore(
            name="full_loop",
            label="完整循环",
            recall=0.5,
            precision=0.4,
            keyword_recall=0.3,
            semantic_recall=0.2,
            unique=4,
            unique_relevant=1,
            llm_calls=7,
            tokens=100,
            tokens_estimated=True,
            elapsed_ms=12.5,
            degraded=0,
        )
        corpus = generate_corpus(size=10, seed=3)
        payload = baseline_payload([score], corpus=corpus, top_k=5, target_qualified=100)
        assert payload["size"] == corpus.size
        assert payload["seed"] == 3
        assert json.loads(json.dumps(payload))["rows"]["full_loop"]["recall"] == 0.5


# -- 4. the CI gate -------------------------------------------------------


def _payload(*, recall=0.5, llm_calls=7, degraded=0) -> dict:
    return {
        "version": 1,
        "size": 120,
        "seed": 7,
        "top_k": 10,
        "target_qualified": 120,
        "rows": {
            "full_loop": {
                "recall": recall,
                "precision": 0.4,
                "keyword_recall": 0.3,
                "semantic_recall": 0.2,
                "unique": 4,
                "unique_relevant": 1,
                "llm_calls": llm_calls,
                "tokens": 1000,
                "elapsed_ms": 12.5,
                "degraded": degraded,
            }
        },
    }


def _write(tmp_path, payload) -> str:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


class TestGate:
    def test_identical_payload_passes(self, tmp_path):
        ok, messages = compare_baseline(_payload(), _write(tmp_path, _payload()))
        assert ok is True
        assert messages == []

    def test_small_jitter_passes(self, tmp_path):
        current = _payload(recall=0.49)
        ok, _ = compare_baseline(current, _write(tmp_path, _payload()))
        assert ok is True

    def test_recall_drop_fails(self, tmp_path):
        current = _payload(recall=0.40)
        ok, messages = compare_baseline(current, _write(tmp_path, _payload()))
        assert ok is False
        assert any("recall" in m for m in messages)

    def test_cost_blowup_fails(self, tmp_path):
        current = _payload(llm_calls=20)
        ok, messages = compare_baseline(current, _write(tmp_path, _payload()))
        assert ok is False
        assert any("llm_calls" in m for m in messages)

    def test_degradation_fails(self, tmp_path):
        current = _payload(degraded=1)
        ok, messages = compare_baseline(current, _write(tmp_path, _payload()))
        assert ok is False
        assert any("degraded" in m for m in messages)

    def test_missing_row_fails(self, tmp_path):
        current = _payload()
        current["rows"]["extra_row"] = dict(current["rows"]["full_loop"])
        ok, messages = compare_baseline(current, _write(tmp_path, _payload()))
        assert ok is False
        assert any("extra_row" in m for m in messages)

    def test_corpus_mismatch_fails(self, tmp_path):
        current = _payload()
        current["seed"] = 99
        ok, messages = compare_baseline(current, _write(tmp_path, _payload()))
        assert ok is False
        assert any("语料不一致" in m for m in messages)


# -- 5. determinism (the gate only works if runs repeat) ------------------


def test_ablation_is_deterministic_across_runs():
    # The CI gate only works if repeated runs agree; timing is excluded (never gated).
    corpus = generate_corpus(size=20, seed=7)
    first = _run_grid(corpus)
    second = _run_grid(corpus)
    assert _metrics(first) == _metrics(second)


def _metrics(scores: dict[str, AblationScore]) -> dict[str, dict]:
    stable = {}
    for name, score in scores.items():
        snapshot = score.to_dict()
        snapshot.pop("elapsed_ms")
        stable[name] = snapshot
    return stable


def _run_grid(corpus) -> dict[str, AblationScore]:
    tool = InMemorySearchTool(corpus.records())
    results = {
        row.name: [
            run_ablation_row(row, ScriptedLlmClient(), tool, spec.text, top_k=5)
            for spec in corpus.queries
        ]
        for row in ABLATION_ROWS
    }
    baseline_surfaced = [r.surfaced_ids for r in results[BASELINE_ROW]]
    return {
        row.name: _score_row(row, results[row.name], corpus, baseline_surfaced)
        for row in ABLATION_ROWS
    }


# -- 6. CLI smoke ---------------------------------------------------------


def test_cli_eval_ablate_runs_offline(capsys):
    from search_agent.cli import main

    exit_code = main(["eval", "--ablate", "--generated", "20", "--seed", "7", "--top-k", "5"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "消融" in out
    for row in ABLATION_ROWS:
        assert row.name in out


def test_cli_eval_ablate_gate_round_trip(tmp_path, capsys):
    from search_agent.cli import main

    baseline = tmp_path / "baseline.json"
    args = ["eval", "--ablate", "--generated", "20", "--seed", "7", "--top-k", "5"]
    assert main([*args, "--save-baseline", str(baseline)]) == 0
    assert baseline.exists()
    capsys.readouterr()
    assert main([*args, "--baseline", str(baseline)]) == 0
    assert "门禁" in capsys.readouterr().out
