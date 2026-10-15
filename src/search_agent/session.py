"""AgentSession: store + paginate the agent's merged result pool.

Unlike a classic paging session that re-queries the index, the agent has no
single query plan -- it produces the entire merged pool in one run. Pagination =
slice the stored pool; no re-run, no re-query. Pool exhausted -> total
truncation, not auto re-search.

Storage: reuses RedisClient (hot) with an in-memory fallback, mirroring
``session_service.py`` patterns. TTL follows existing session convention.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field

from search_agent.models.api import CandidateItem

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 3600  # 1 hour, aligned with existing session TTL
_MEM_CACHE: dict[str, tuple[int, str]] = {}  # key -> (expires_at, json)


class AgentSessionData(BaseModel):
    """Serializable snapshot of one agent run for pagination."""

    session_id: str
    query: str
    company_id: int
    hr_id: int | None = None
    job_profile_hash: str = ""
    created_at: int = 0
    expires_at: int = 0
    merged_pool: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    agent_trace: dict[str, Any] = Field(default_factory=dict)
    agent_metrics: dict[str, Any] = Field(default_factory=dict)
    degraded_reason: str | None = None
    clarification: dict[str, Any] | None = None
    need_clarify: bool = False


class AgentSessionPage(BaseModel):
    """One page of paginated agent results."""

    query_token: str
    offset: int
    limit: int
    total: int
    current_page: int
    page_size: int
    total_pages: int
    has_more: bool
    next_offset: int
    items: list[CandidateItem]
    degraded_reason: str | None = None


class AgentSessionStore:
    """Redis-backed (with in-memory fallback) agent session store."""

    PREFIX = "search_agent:session:"

    def __init__(self, ttl_seconds: int = _DEFAULT_TTL, *, connect_redis: bool = True) -> None:
        self._ttl = ttl_seconds
        self._redis = None
        if connect_redis:
            try:
                import redis

                url = os.environ.get("SEARCH_AGENT_REDIS_URL") or os.environ.get("REDIS_URL")
                if url:
                    self._redis = redis.Redis.from_url(url)
            except Exception as exc:  # pragma: no cover
                logger.info("Redis unavailable, agent session falls back to memory: %s", exc)

    # ── create ──────────────────────────────────────────────────────────

    def create(self, state: dict[str, Any]) -> str:
        """Serialize agent state into a session, return query_token."""
        token = str(uuid.uuid4())
        job_profile = state.get("job_profile")
        job_hash = ""
        if job_profile:
            raw = json.dumps(job_profile.model_dump(), ensure_ascii=False, sort_keys=True)
            job_hash = hashlib.sha1(raw.encode()).hexdigest()[:12]

        final_items = state.get("final_items", [])
        merged_pool = [tc.model_dump() for tc in final_items]

        data = AgentSessionData(
            session_id=token,
            query=state.get("query", ""),
            company_id=state.get("company_id", 0),
            hr_id=state.get("hr_id"),
            job_profile_hash=job_hash,
            created_at=int(time.time()),
            expires_at=int(time.time()) + self._ttl,
            merged_pool=merged_pool,
            total=len(merged_pool),
            agent_trace=state.get("agent_trace", {}),
            agent_metrics=state.get("agent_metrics", {}),
            degraded_reason=state.get("degraded_reason"),
            clarification=None,
            need_clarify=bool(state.get("need_clarify")),
        )
        self._set(token, data.model_dump())
        logger.info(
            "[agent-session] created token=%s pool_size=%d",
            token,
            data.total,
        )
        return token

    # ── read / paginate ─────────────────────────────────────────────────

    def get_page(
        self,
        query_token: str,
        offset: int = 0,
        limit: int = 20,
    ) -> AgentSessionPage:
        """Slice the stored merged_pool; no re-run, no re-query."""
        raw = self._get(query_token)
        if raw is None:
            raise KeyError(f"agent session not found or expired: {query_token}")

        pool = raw.get("merged_pool", [])
        total = raw.get("total", len(pool))
        page = pool[offset : offset + limit]
        items = [CandidateItem.model_validate(tc["item"]) for tc in page]

        page_size = len(items)
        total_pages = max(1, -(-total // limit)) if limit > 0 else 1
        current_page = offset // limit + 1 if limit > 0 else 1
        has_more = offset + page_size < total
        next_offset = offset + page_size if has_more else offset

        return AgentSessionPage(
            query_token=query_token,
            offset=offset,
            limit=limit,
            total=total,
            current_page=current_page,
            page_size=page_size,
            total_pages=total_pages,
            has_more=has_more,
            next_offset=next_offset,
            items=items,
            degraded_reason=raw.get("degraded_reason"),
        )

    def get_session(self, query_token: str) -> AgentSessionData | None:
        """Full session data (for debug / trace retrieval)."""
        raw = self._get(query_token)
        if raw is None:
            return None
        return AgentSessionData.model_validate(raw)

    # ── invalidate ──────────────────────────────────────────────────────

    def invalidate(self, query_token: str) -> bool:
        key = self._key(query_token)
        if self._redis is not None:
            self._redis.delete(key)
            return True
        _MEM_CACHE.pop(key, None)
        return True

    # ── internals ───────────────────────────────────────────────────────

    def _key(self, token: str) -> str:
        return f"{self.PREFIX}{token}"

    def _set(self, token: str, payload: dict[str, Any]) -> bool:
        value = json.dumps(payload, ensure_ascii=False)
        key = self._key(token)
        if self._redis is not None:
            return self._redis.set(key, value, ex=self._ttl)
        _MEM_CACHE[key] = (int(time.time()) + self._ttl, value)
        return True

    def _get(self, token: str) -> dict[str, Any] | None:
        key = self._key(token)
        if self._redis is not None:
            raw = self._redis.get(key)
            return json.loads(raw) if raw else None
        row = _MEM_CACHE.get(key)
        if not row:
            return None
        expires, value = row
        if int(time.time()) >= expires:
            _MEM_CACHE.pop(key, None)
            return None
        return json.loads(value)


__all__ = ["AgentSessionData", "AgentSessionPage", "AgentSessionStore"]
