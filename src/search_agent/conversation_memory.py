"""Conversation memory for multi-round hiring searches.

L1 keeps the latest three turns in the prompt payload. L2 persists the full
turn list in Redis (or an in-process fallback) so the history sidebar can
restore a conversation. L3 tracks candidate IDs seen in the conversation and
the subset the recruiter explicitly excluded. Only explicit exclusions are
passed to the SearchTool; seen IDs are kept for history/audit and current-run
de-duplication.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

logger = logging.getLogger(__name__)

_DEFAULT_TTL = int(os.environ.get("SEARCH_AGENT_CONVERSATION_TTL_SEC", str(30 * 24 * 3600)))
_DEFAULT_MAX_TURNS = int(os.environ.get("SEARCH_AGENT_CONVERSATION_MAX_TURNS", "50"))
_MAX_CANDIDATE_IDS = 500
_MEM_CACHE: dict[str, tuple[int, str]] = {}  # key -> (expires_at, json)
_MEM_INDEX: dict[str, dict[str, int]] = {}  # scope -> {conversation_id: updated_at}


class _ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class ConversationTurn(_ApiModel):
    """One round of conversation: what was asked + what was found."""

    turn_index: int
    query: str
    assistant_summary: str = ""
    job_profile_summary: dict[str, Any] = Field(default_factory=dict)
    result_count: int = 0
    top_candidate_names: list[str] = Field(default_factory=list)
    top_candidate_ids: list[str] = Field(default_factory=list)
    result_items: list[dict[str, Any]] = Field(default_factory=list)
    route: str | None = None
    status: str = "completed"
    query_token: str | None = None
    clarification: dict[str, Any] | None = None
    top_k: int = 0
    timestamp: int = 0


class ConversationMemory(_ApiModel):
    """Full conversation history for one conversationId."""

    conversation_id: str
    company_id: int
    hr_id: int | None = None
    title: str = ""
    turns: list[ConversationTurn] = Field(default_factory=list)
    total_turns: int = 0
    seen_candidate_ids: list[str] = Field(default_factory=list)
    excluded_candidate_ids: list[str] = Field(default_factory=list)
    created_at: int = 0
    updated_at: int = 0
    expires_at: int = 0


class ConversationSummary(_ApiModel):
    """Compact conversation row for the history sidebar."""

    conversation_id: str
    title: str
    turn_count: int
    result_count: int = 0
    last_query: str = ""
    status: str = "completed"
    seen_candidate_count: int = 0
    excluded_candidate_count: int = 0
    created_at: int = 0
    updated_at: int = 0


class ConversationStore:
    """Redis-backed conversation history with an in-process fallback."""

    PREFIX = "search_agent:conversation:"
    INDEX_PREFIX = "search_agent:conversation-index:"

    def __init__(
        self,
        ttl_seconds: int | None = None,
        *,
        connect_redis: bool = True,
        max_turns: int | None = None,
    ) -> None:
        resolved_max_turns = _DEFAULT_MAX_TURNS if max_turns is None else max_turns
        if resolved_max_turns < 1:
            raise ValueError("max_turns must be positive")
        self._ttl = _DEFAULT_TTL if ttl_seconds is None else ttl_seconds
        self._max_turns = resolved_max_turns
        self._redis = None
        if connect_redis:
            try:
                import redis

                url = os.environ.get("SEARCH_AGENT_REDIS_URL") or os.environ.get("REDIS_URL")
                if url:
                    self._redis = redis.Redis.from_url(url, protocol=2)
            except Exception as exc:  # pragma: no cover
                logger.info("Redis unavailable, conversation memory falls back to memory: %s", exc)

    @property
    def backend_name(self) -> str:
        return "redis" if self._redis is not None else "memory"

    def create(
        self,
        company_id: int,
        hr_id: int | None,
        *,
        title: str = "",
        conversation_id: str | None = None,
    ) -> ConversationMemory:
        """Create a conversation and add it to the tenant-scoped index."""
        if conversation_id:
            existing = self.load(conversation_id)
            if existing is not None:
                if not self._scope_matches(existing, company_id, hr_id):
                    raise ValueError("conversation scope mismatch")
                return existing
        now = int(time.time())
        memory = ConversationMemory(
            conversation_id=conversation_id or new_conversation_id(),
            company_id=company_id,
            hr_id=hr_id,
            title=title.strip()[:80],
            created_at=now,
            updated_at=now,
            expires_at=now + self._ttl,
        )
        self._set(memory.conversation_id, memory.model_dump())
        self._touch_index(memory)
        return memory

    def load(self, conversation_id: str) -> ConversationMemory | None:
        """Load conversation history by conversation_id."""
        raw = self._get(conversation_id)
        if raw is None:
            return None
        try:
            memory = ConversationMemory.model_validate(raw)
            if memory.total_turns < len(memory.turns):
                memory.total_turns = len(memory.turns)
            if not memory.updated_at:
                timestamps = [turn.timestamp for turn in memory.turns if turn.timestamp]
                memory.updated_at = max(timestamps, default=memory.created_at)
            if not memory.title:
                first_search = next(
                    (turn for turn in memory.turns if turn.status != "reply"),
                    None,
                )
                if first_search is not None:
                    memory.title = _conversation_title(first_search.query)
            return memory
        except Exception as exc:
            logger.warning("conversation memory parse failed: %s", exc)
            return None

    def list(
        self,
        company_id: int,
        hr_id: int | None,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ConversationSummary]:
        """List recent conversations for one exact tenant scope."""
        summaries: list[ConversationSummary] = []
        for conversation_id in self._index_ids(company_id, hr_id, limit=limit, offset=offset):
            memory = self.load(conversation_id)
            if memory is None:
                self._remove_index(company_id, hr_id, conversation_id)
                continue
            if not self._scope_matches(memory, company_id, hr_id):
                continue
            summaries.append(self._summary(memory))
        return summaries

    def delete(self, conversation_id: str, company_id: int, hr_id: int | None) -> bool:
        """Delete one conversation only when the tenant scope matches."""
        memory = self.load(conversation_id)
        if memory is None or not self._scope_matches(memory, company_id, hr_id):
            return False
        if self._redis is not None:
            self._redis.delete(self._key(conversation_id))
        else:
            _MEM_CACHE.pop(self._key(conversation_id), None)
        self._remove_index(company_id, hr_id, conversation_id)
        return True

    def append_turn(
        self,
        conversation_id: str,
        company_id: int,
        hr_id: int | None,
        turn: ConversationTurn,
    ) -> ConversationMemory:
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
                updated_at=now,
                expires_at=now + self._ttl,
            )
        elif not self._scope_matches(memory, company_id, hr_id):
            raise ValueError("conversation scope mismatch")

        if not memory.title and turn.status != "reply":
            memory.title = _conversation_title(turn.query)
        memory.turns.append(turn)
        memory.turns = memory.turns[-self._max_turns :]
        memory.total_turns = max(memory.total_turns, turn.turn_index + 1)
        memory.seen_candidate_ids = _merge_ids(
            memory.seen_candidate_ids,
            _turn_candidate_ids(turn),
        )
        memory.updated_at = now
        memory.expires_at = now + self._ttl

        self._set(conversation_id, memory.model_dump())
        self._touch_index(memory)
        logger.info(
            "[conversation-memory] appended turn=%d to conv=%s total_turns=%d",
            turn.turn_index,
            conversation_id,
            memory.total_turns,
        )
        return memory

    def set_candidate_excluded(
        self,
        conversation_id: str,
        company_id: int,
        hr_id: int | None,
        doc_id: str,
        *,
        excluded: bool,
    ) -> ConversationMemory | None:
        """Persist an explicit candidate exclusion for this conversation."""
        memory = self.load(conversation_id)
        if memory is None or not self._scope_matches(memory, company_id, hr_id):
            return None
        doc_id = str(doc_id).strip()
        if not doc_id:
            return memory
        candidates = [item for item in memory.excluded_candidate_ids if item != doc_id]
        if excluded:
            candidates.append(doc_id)
        memory.excluded_candidate_ids = candidates[-_MAX_CANDIDATE_IDS:]
        now = int(time.time())
        memory.updated_at = now
        memory.expires_at = now + self._ttl
        self._set(conversation_id, memory.model_dump())
        self._touch_index(memory)
        return memory

    # -- internals -- --------------------------------------------------------

    def _key(self, conversation_id: str) -> str:
        return f"{self.PREFIX}{conversation_id}"

    @staticmethod
    def _scope_key(company_id: int, hr_id: int | None) -> str:
        return f"{company_id}:{'none' if hr_id is None else hr_id}"

    @classmethod
    def _index_key(cls, company_id: int, hr_id: int | None) -> str:
        return f"{cls.INDEX_PREFIX}{cls._scope_key(company_id, hr_id)}"

    @staticmethod
    def _scope_matches(
        memory: ConversationMemory,
        company_id: int,
        hr_id: int | None,
    ) -> bool:
        return memory.company_id == company_id and memory.hr_id == hr_id

    @staticmethod
    def _summary(memory: ConversationMemory) -> ConversationSummary:
        last = memory.turns[-1] if memory.turns else None
        return ConversationSummary(
            conversation_id=memory.conversation_id,
            title=memory.title or (last.query if last else "未命名会话"),
            turn_count=memory.total_turns,
            result_count=last.result_count if last else 0,
            last_query=last.query if last else "",
            status=last.status if last else "empty",
            seen_candidate_count=len(memory.seen_candidate_ids),
            excluded_candidate_count=len(memory.excluded_candidate_ids),
            created_at=memory.created_at,
            updated_at=memory.updated_at,
        )

    def _touch_index(self, memory: ConversationMemory) -> None:
        scope_key = self._scope_key(memory.company_id, memory.hr_id)
        if self._redis is not None:
            key = self._index_key(memory.company_id, memory.hr_id)
            self._redis.zadd(key, {memory.conversation_id: int(memory.updated_at)})
            self._redis.expire(key, self._ttl)
            return
        _MEM_INDEX.setdefault(scope_key, {})[memory.conversation_id] = int(memory.updated_at)

    def _index_ids(
        self,
        company_id: int,
        hr_id: int | None,
        *,
        limit: int,
        offset: int,
    ) -> list[str]:
        if limit < 1:
            return []
        if self._redis is not None:
            raw_ids = self._redis.zrevrange(
                self._index_key(company_id, hr_id),
                offset,
                offset + limit - 1,
            )
            return [row.decode("utf-8") if isinstance(row, bytes) else str(row) for row in raw_ids]
        scope_key = self._scope_key(company_id, hr_id)
        rows = sorted(
            _MEM_INDEX.get(scope_key, {}).items(),
            key=lambda item: item[1],
            reverse=True,
        )
        return [conversation_id for conversation_id, _ in rows[offset : offset + limit]]

    def _remove_index(self, company_id: int, hr_id: int | None, conversation_id: str) -> None:
        if self._redis is not None:
            self._redis.zrem(self._index_key(company_id, hr_id), conversation_id)
            return
        _MEM_INDEX.get(self._scope_key(company_id, hr_id), {}).pop(conversation_id, None)

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


def _conversation_title(query: str, limit: int = 48) -> str:
    compact = " ".join(str(query or "").split())
    return compact[:limit].rstrip() + ("…" if len(compact) > limit else "")


def _turn_candidate_ids(turn: ConversationTurn) -> list[str]:
    ids = [str(doc_id) for doc_id in turn.top_candidate_ids if doc_id]
    for item in turn.result_items:
        doc_id = item.get("docId") or item.get("doc_id")
        if doc_id:
            ids.append(str(doc_id))
    return ids


def _merge_ids(existing: list[str], incoming: list[str]) -> list[str]:
    merged = list(dict.fromkeys([*existing, *incoming]))
    return merged[-_MAX_CANDIDATE_IDS:]


__all__ = [
    "ConversationMemory",
    "ConversationStore",
    "ConversationSummary",
    "ConversationTurn",
    "new_conversation_id",
]
