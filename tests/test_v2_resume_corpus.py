"""Tests for the V2-native rich resume benchmark corpus."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from search_agent.corpus import DEFAULT_QUERIES, EVIDENCE_HINTS
from search_agent.hybrid_v2.config import HybridV2Config
from search_agent.hybrid_v2.embedding import HashEmbedder
from search_agent.hybrid_v2.indexing import convert_candidates_to_documents
from search_agent.v2_resume_corpus import (
    generate_v2_resume_corpus,
    write_v2_resume_corpus,
)


def test_generation_is_deterministic_and_has_unique_people() -> None:
    first = generate_v2_resume_corpus(size=100, seed=2026)
    second = generate_v2_resume_corpus(size=100, seed=2026)

    assert first.records() == second.records()
    assert first.abilities == second.abilities
    assert first.size == 100
    assert len({row["docId"] for row in first.records()}) == 100
    assert len({row["name"] for row in first.records()}) == 100


def test_every_resume_fills_v2_text_channels() -> None:
    corpus = generate_v2_resume_corpus(size=100, seed=2026)
    for row in corpus.records():
        assert row["summary"]
        assert row["overview"]["self_evaluation"]
        assert row["skills"]
        assert row["workExperiences"]
        assert all(work["description"] for work in row["workExperiences"])
        assert row["projectExperiences"]
        assert all(project["description"] for project in row["projectExperiences"])
        assert row["educations"]
        assert row["expectedPositions"]
        assert row["expectedCities"]
        assert row["expectedSalary"]
        assert row["workState"]
        assert row["languages"]


def test_implicit_abilities_have_no_keyword_evidence() -> None:
    corpus = generate_v2_resume_corpus(size=100, seed=2026)
    for row in corpus.records():
        blob = json.dumps(row, ensure_ascii=False).lower()
        for ability in corpus.implicit[row["docId"]]:
            for hint in EVIDENCE_HINTS[ability]:
                assert hint not in blob, (row["docId"], ability, hint)


def test_query_oracles_have_enough_candidates_and_semantic_gaps() -> None:
    corpus = generate_v2_resume_corpus(size=100, seed=2026)
    assert all(len(corpus.relevant(spec)) >= 3 for spec in DEFAULT_QUERIES)
    assert any(
        set(spec.required_abilities) - corpus.visible[doc_id]
        for spec in DEFAULT_QUERIES
        for doc_id in corpus.relevant(spec)
    )


def test_rich_records_convert_to_complete_v2_documents() -> None:
    corpus = generate_v2_resume_corpus(size=8, seed=2026)
    config = replace(
        HybridV2Config.from_env(),
        es_url="http://example.invalid:9200",
        es_index="v2-resume-test",
        embedding_provider="hash",
        embedding_dims=16,
        vector_mode="flat",
    )
    documents = convert_candidates_to_documents(
        corpus.records(),
        company_id=1,
        config=config,
        embedder=HashEmbedder(16),
        as_of=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )

    assert len(documents) == 8
    for document in documents:
        assert document["work_experiences"]
        assert document["project_experiences"]
        assert document["work_experiences"][0]["description"]
        assert document["project_experiences"][0]["description"]
        for field in (
            "work_vector",
            "project_vector",
            "education_vector",
            "self_evaluation_vector",
            "skills_vector",
        ):
            assert len(document[field]) == 16


def test_write_v2_corpus_outputs_oracle_and_jsonl(tmp_path: Path) -> None:
    corpus = generate_v2_resume_corpus(size=12, seed=2026)
    candidates_path, oracle_path = write_v2_resume_corpus(corpus, tmp_path)

    rows = [
        json.loads(line)
        for line in candidates_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    assert len(rows) == 12
    assert oracle["schemaVersion"] == 2
    assert oracle["size"] == 12
    assert oracle["coverage"]["projectExperiences"] == 12
    assert set(oracle["labels"]) == {row["docId"] for row in rows}
