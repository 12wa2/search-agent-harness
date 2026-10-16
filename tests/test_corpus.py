"""Tests for the seeded synthetic corpus and its oracle labels."""

import json

from search_agent.corpus import (
    ABILITY_SKILLS,
    DEFAULT_QUERIES,
    EVIDENCE_HINTS,
    generate_corpus,
    load_corpus,
    write_corpus,
)
from search_agent.demo import ScriptedLlmClient
from search_agent.tools.memory import load_candidates

SIZE = 36


def test_same_seed_gives_identical_corpus():
    first = generate_corpus(size=SIZE, seed=7)
    second = generate_corpus(size=SIZE, seed=7)
    assert first.records() == second.records()
    assert first.abilities == second.abilities
    assert first.visible == second.visible


def test_different_seed_changes_corpus():
    assert (
        generate_corpus(size=SIZE, seed=7).records() != generate_corpus(size=SIZE, seed=8).records()
    )


def test_records_load_into_candidates():
    corpus = generate_corpus(size=SIZE, seed=7)
    items = load_candidates(corpus.records())
    assert len(items) == SIZE
    assert all(item.docId and item.name and item.summary for item in items)


def test_implicit_abilities_leave_no_keyword_evidence():
    corpus = generate_corpus(size=120, seed=7)
    for record in corpus.candidates:
        blob = json.dumps(record, ensure_ascii=False).lower()
        for ability in corpus.implicit[record["docId"]]:
            for hint in EVIDENCE_HINTS[ability]:
                assert hint not in blob, f"{record['docId']} leaks {hint!r} ({ability})"


def test_oracle_sets_are_meaningful():
    corpus = generate_corpus(size=120, seed=7)
    for spec in corpus.queries:
        assert len(corpus.relevant(spec)) >= 3, spec.text
    with_semantic_gap = [spec for spec in corpus.queries if corpus.semantic_only(spec)]
    assert len(with_semantic_gap) >= 4


def test_required_abilities_match_scripted_understanding():
    llm = ScriptedLlmClient()
    for spec in DEFAULT_QUERIES:
        data = llm.complete_json(
            system="",
            user=json.dumps({"query": spec.text}, ensure_ascii=False),
            schema_name="agent_understand",
            schema={},
        )
        names = {entry["name"] for entry in data["must_abilities"]}
        assert names == set(spec.required_abilities), spec.text


def test_write_and_load_round_trip(tmp_path):
    corpus = generate_corpus(size=SIZE, seed=7)
    write_corpus(corpus, tmp_path)
    restored = load_corpus(tmp_path)
    assert restored.records() == corpus.records()
    assert restored.abilities == corpus.abilities
    assert restored.queries == corpus.queries
    assert restored.seed == corpus.seed


def test_explicit_skill_tags_never_leak_other_abilities():
    """Every skill tag carries its own evidence keyword and no other ability's."""
    for ability, skills in ABILITY_SKILLS.items():
        own = EVIDENCE_HINTS[ability]
        others = {
            hint for other, hints in EVIDENCE_HINTS.items() if other != ability for hint in hints
        }
        blob = " ".join(skills).lower()
        assert any(hint in blob for hint in own), ability
        assert not any(hint in blob for hint in others), ability
