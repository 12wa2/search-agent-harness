"""Conversation memory: cross-turn context for multi-round search.

Stores per-turn condition summaries in Redis so the understand stage can
see what was searched before. Fully optional: no conversationId = no memory
loaded, behavior identical to before.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 7200  # 2 hours
_MEM_CACHE: dict[str, tuple[int, str]] = {}  # key -> (expires_at, json)


class ConversationTurn(BaseModel):
    """One round of conversation: what was asked + what was found."""

    turn_index: int
    query: str
    job_profile_summary: dict[str, Any] = Field(default_factory=dict)
    result_count: int = 0
    top_candidate_names: list[str] = Field(default_factory=list)
    timestamp: int = 0


class ConversationMemory(BaseModel):
    """Full conversation history for one conversationId."""

    conversation_id: str
    company_id: int
    hr_id: int | None = None
    turns: list[ConversationTurn] = Field(default_factory=list)
    created_at: int = 0
    expires_at: int = 0


class ConversationStore:
    """Redis-backed (with in-memory fallback) conversation memory store."""

    PREFIX = "search_agent:conversation:"

    def __init__(
        self,
        ttl_seconds: int = _DEFAULT_TTL,
        *,
        connect_redis: bool = True,
        max_turns: int = 10,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        self._ttl = ttl_seconds
        self._max_turns = max_turns
        self._redis = None
        if connect_redis:
            try:
                import redis

                url = os.environ.get("SEARCH_AGENT_REDIS_URL") or os.environ.get("REDIS_URL")
                if url:
                    self._redis = redis.Redis.from_url(url)
            except Exception as exc:  # pragma: no cover
                logger.info("Redis unavailable, conversation memory falls back to memory: %s", exc)

    def load(self, conversation_id: str) -> ConversationMemory | None:
        """Load conversation history by conversation_id."""
        raw = self._get(conversation_id)
        if raw is None:
            return None
        try:
            return ConversationMemory.model_validate(raw)
        except Exception as exc:
            logger.warning("conversation memory parse failed: %s", exc)
            return None

    def append_turn(
        self,
        conversation_id: str,
        company_id: int,
        hr_id: int | None,
        turn: ConversationTurn,
    ) -> None:
        """Append a turn to the conversation, create if not exists."""
        memory = self.load(conversation_id)
        now = int(time.time())

        if memory is None:
            memory = ConversationMemory(
                conversation_id=conversation_id,
                company_id=company_id,
                hr_id=hr_id,
                turns=[],
                created_at=now,
                expires_at=now + self._ttl,
            )

        memory.turns.append(turn)
        memory.turns = memory.turns[-self._max_turns :]
        memory.expires_at = now + self._ttl

        self._set(conversation_id, memory.model_dump())
        logger.info(
            "[conversation-memory] appended turn=%d to conv=%s total_turns=%d",
            turn.turn_index,
            conversation_id,
            len(memory.turns),
        )

    # -- internals -- --------------------------------------------------------

    def _key(self, conversation_id: str) -> str:
        return f"{self.PREFIX}{conversation_id}"

    def _set(self, conversation_id: str, payload: dict[str, Any]) -> bool:
        value = json.dumps(payload, ensure_ascii=False)
        key = self._key(conversation_id)
        if self._redis is not None:
            return self._redis.set(key, value, ex=self._ttl)
        _MEM_CACHE[key] = (int(time.time()) + self._ttl, value)
        return True

    def _get(self, conversation_id: str) -> dict[str, Any] | None:
        key = self._key(conversation_id)
        if self._redis is not None:
            raw = self._redis.get(key)
            if raw is None:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        row = _MEM_CACHE.get(key)
        if not row:
            return None
        expires, value = row
        if int(time.time()) >= expires:
            _MEM_CACHE.pop(key, None)
            return None
        return json.loads(value)


def new_conversation_id() -> str:
    """Generate a new conversation ID."""
    return str(uuid.uuid4())


__all__ = [
    "ConversationMemory",
    "ConversationStore",
    "ConversationTurn",
    "new_conversation_id",
]
