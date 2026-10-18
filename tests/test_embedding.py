"""Vector channel: deterministic local embeddings (offline, no model, no server)."""

from __future__ import annotations

import math

import pytest

from search_agent.tools.embedding import EMBEDDING_DIMS, cosine, embed_text


def test_embedding_is_deterministic_and_normalised() -> None:
    first = embed_text("高并发系统设计")
    assert first == embed_text("高并发系统设计")
    assert len(first) == EMBEDDING_DIMS
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0, rel_tol=1e-9)


def test_embedding_is_a_bag_of_tokens() -> None:
    assert embed_text("Kafka Redis 微服务") == embed_text("微服务 Redis Kafka")


def test_empty_text_has_no_direction() -> None:
    vector = embed_text("")
    assert set(vector) == {0.0}
    assert cosine(vector, vector) == 0.0


def test_non_empty_text_without_tokens_still_has_a_direction() -> None:
    vector = embed_text("3年+")
    assert any(value != 0.0 for value in vector)
    assert math.isclose(math.sqrt(sum(value * value for value in vector)), 1.0, rel_tol=1e-9)


def test_related_text_beats_unrelated_text() -> None:
    query = embed_text("高并发系统设计 服务端开发")
    related = embed_text("主导过日均十亿级高并发交易系统的架构设计与稳定性治理")
    unrelated = embed_text("市场推广 品牌公关 活动策划")
    assert cosine(query, related) > cosine(query, unrelated)


def test_cosine_rejects_dimension_mismatch() -> None:
    with pytest.raises(ValueError):
        cosine([1.0], [1.0, 0.0])
