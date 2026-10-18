"""Conversation memory tests: ConversationStore CRUD + multi-round behavior.

Tests:
1. ConversationStore: create, load, append_turn, expiry, Redis-unavailable fallback
2. ConversationTurn model: serialization roundtrip
3. Multi-round: first turn (no history) -> second turn (1 history) -> third turn (2)
4. Backward compat: no conversationId = empty history, behavior unchanged
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from search_agent.conversation_memory import (
    ConversationMemory,
    ConversationStore,
    ConversationTurn,
    new_conversation_id,
)

# ── helpers ──────────────────────────────────────────────────────────────


def _turn(index=0, query="Python 3 years"):
    return ConversationTurn(
        turn_index=index,
        query=query,
        job_profile_summary={
            "must_abilities": ["Python"],
            "hard_constraints": [{"field": "skill", "operator": "any", "values": ["Python"]}],
            "difficulty": "mid",
        },
        result_count=50,
        top_candidate_names=["Alice", "Bob", "Charlie"],
        timestamp=int(time.time()),
    )


# ── 1. ConversationTurn model ────────────────────────────────────────────


class TestConversationTurn:
    def test_serialization_roundtrip(self):
        turn = _turn(0, "find Python dev")
        data = turn.model_dump()
        restored = ConversationTurn.model_validate(data)
        assert restored.turn_index == 0
        assert restored.query == "find Python dev"
        assert restored.result_count == 50
        assert restored.top_candidate_names == ["Alice", "Bob", "Charlie"]

    def test_defaults(self):
        turn = ConversationTurn(turn_index=0, query="test")
        assert turn.job_profile_summary == {}
        assert turn.result_count == 0
        assert turn.top_candidate_names == []
        assert turn.timestamp == 0


# ── 2. ConversationStore with in-memory fallback ─────────────────────────


class TestConversationStoreMemory:
    """Use connect_redis=False to test in-memory fallback."""

    def test_load_nonexistent_returns_none(self):
        store = ConversationStore(connect_redis=False)
        assert store.load("nonexistent-id") is None

    def test_append_and_load_single_turn(self):
        store = ConversationStore(connect_redis=False)
        conv_id = new_conversation_id()
        store.append_turn(conv_id, company_id=39978, hr_id=1, turn=_turn(0))
        memory = store.load(conv_id)
        assert memory is not None
        assert len(memory.turns) == 1
        assert memory.turns[0].query == "Python 3 years"
        assert memory.company_id == 39978

    def test_append_multiple_turns(self):
        store = ConversationStore(connect_redis=False)
        conv_id = new_conversation_id()
        store.append_turn(conv_id, 39978, 1, _turn(0, "Python dev"))
        store.append_turn(conv_id, 39978, 1, _turn(1, "prefer big tech"))
        store.append_turn(conv_id, 39978, 1, _turn(2, "salary under 30k"))
        memory = store.load(conv_id)
        assert memory is not None
        assert len(memory.turns) == 3
        assert memory.turns[0].query == "Python dev"
        assert memory.turns[1].query == "prefer big tech"
        assert memory.turns[2].query == "salary under 30k"

    def test_append_keeps_only_latest_turns(self):
        store = ConversationStore(ttl_seconds=60, connect_redis=False, max_turns=2)
        conv_id = new_conversation_id()
        store.append_turn(conv_id, 39978, 1, _turn(0, "q1"))
        store.append_turn(conv_id, 39978, 1, _turn(1, "q2"))
        store.append_turn(conv_id, 39978, 1, _turn(2, "q3"))

        memory = store.load(conv_id)
        assert memory is not None
        assert [turn.query for turn in memory.turns] == ["q2", "q3"]

    def test_expired_entry_returns_none(self):
        store = ConversationStore(ttl_seconds=0, connect_redis=False)
        conv_id = new_conversation_id()
        store.append_turn(conv_id, 39978, 1, _turn(0))
        # TTL=0 means it expires immediately on next read
        time.sleep(0.1)
        assert store.load(conv_id) is None


# ── 3. ConversationStore with mocked Redis ───────────────────────────────


class TestConversationStoreRedis:
    """Test Redis path with a mock client."""

    def test_redis_set_called_on_append(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # no existing memory
        mock_redis.set.return_value = True

        store = ConversationStore(connect_redis=False)
        store._redis = mock_redis  # inject mock

        conv_id = "test-conv-123"
        store.append_turn(conv_id, 39978, 1, _turn(0))

        mock_redis.set.assert_called_once()
        args = mock_redis.set.call_args
        assert args[0][0] == "search_agent:conversation:test-conv-123"
        # value is JSON string
        import json

        payload = json.loads(args[0][1])
        assert payload["conversation_id"] == "test-conv-123"
        assert len(payload["turns"]) == 1

    def test_redis_get_returns_memory(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # no existing memory on first append
        conv_id = "test-conv-456"
        store = ConversationStore(connect_redis=False)
        store._redis = mock_redis

        # First append (writes to mock redis)
        store.append_turn(conv_id, 39978, 1, _turn(0, "first query"))

        # Now mock redis returns the stored value
        stored_value = mock_redis.set.call_args[0][1]
        mock_redis.get.return_value = stored_value

        memory = store.load(conv_id)
        assert memory is not None
        assert memory.conversation_id == conv_id
        assert memory.turns[0].query == "first query"

    def test_redis_get_returns_none_when_missing(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        store = ConversationStore(connect_redis=False)
        store._redis = mock_redis

        assert store.load("missing") is None

    def test_redis_bytes_response_handled(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # no existing memory on first append
        store = ConversationStore(connect_redis=False)
        store._redis = mock_redis

        store.append_turn("bytes-test", 39978, 1, _turn(0, "bytes query"))
        stored_value = mock_redis.set.call_args[0][1]
        mock_redis.get.return_value = stored_value.encode("utf-8")

        memory = store.load("bytes-test")
        assert memory is not None
        assert memory.turns[0].query == "bytes query"


# ── 4. new_conversation_id ───────────────────────────────────────────────


class TestNewConversationId:
    def test_generates_unique_ids(self):
        id1 = new_conversation_id()
        id2 = new_conversation_id()
        assert id1 != id2
        assert len(id1) == 36  # UUID format


# ── 5. ConversationMemory model ──────────────────────────────────────────


class TestConversationMemory:
    def test_full_serialization_roundtrip(self):
        memory = ConversationMemory(
            conversation_id="conv-abc",
            company_id=39978,
            hr_id=1,
            turns=[_turn(0, "q1"), _turn(1, "q2")],
            created_at=1000,
            expires_at=2000,
        )
        data = memory.model_dump()
        restored = ConversationMemory.model_validate(data)
        assert restored.conversation_id == "conv-abc"
        assert len(restored.turns) == 2
        assert restored.turns[1].query == "q2"
