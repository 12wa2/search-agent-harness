"""Strategy memory: (query family, strategy) -> observed yield.

The loop decides *what* to search this run; this store remembers *which kind of
move paid off* for a family of queries, so the next plan/reflect starts from
evidence instead of a cold prompt. Keyed by a hash of the job's must-ability
set -- no query text, no candidate data -- and fully optional, mirroring
``conversation_memory``: Redis when configured, in-process otherwise.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 30 * 24 * 3600  # 30 days: strategy memory should outlive conversations


def query_family(job_profile: Any) -> str:
    """Stable family id for a job: sha1 over the sorted must-ability names.

    Deliberately coarse (abilities, not the raw query text): near-duplicate JDs
    from one team share priors, and no query text ever leaves the process.
    """
    abilities = getattr(job_profile, "must_abilities", []) or []
    names = sorted(str(getattr(a, "name", a) or "") for a in abilities)
    payload = "|".join(name for name in names if name)
    if not payload:
        payload = str(getattr(job_profile, "title", "") or "unknown")
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


@dataclass
class StrategyStat:
    """Observed outcome of one strategy for one family."""

    runs: int = 0
    qualified: int = 0
    tokens: int = 0

    @property
    def avg_qualified(self) -> float:
        return self.qualified / self.runs if self.runs else 0.0


class ExperienceStore:
    """Per-family strategy outcomes: Redis when configured, memory otherwise."""

    PREFIX = "search_agent:experience:"

    def __init__(self, ttl_seconds: int = _DEFAULT_TTL, *, connect_redis: bool = True) -> None:
        self._ttl = ttl_seconds
        self._redis = None
        self._mem: dict[str, dict[str, StrategyStat]] = {}
        if connect_redis:
            try:
                import redis

                url = os.environ.get("SEARCH_AGENT_REDIS_URL") or os.environ.get("REDIS_URL")
                if url:
                    self._redis = redis.Redis.from_url(url, protocol=2)
            except Exception as exc:  # pragma: no cover - optional dependency
                logger.info("Redis unavailable, strategy memory stays in-process: %s", exc)

    def record(self, family: str, strategy: str, *, qualified: int, tokens: int = 0) -> None:
        """Add one observation: this strategy ran and yielded N qualified people."""
        bucket = self._mem.setdefault(family, {})
        stat = bucket.setdefault(strategy, StrategyStat())
        stat.runs += 1
        stat.qualified += max(0, int(qualified))
        stat.tokens += max(0, int(tokens))
        if self._redis is not None:
            key = f"{self.PREFIX}{family}"
            try:
                pipe = self._redis.pipeline()
                pipe.hincrby(key, f"{strategy}:runs", 1)
                pipe.hincrby(key, f"{strategy}:qualified", max(0, int(qualified)))
                pipe.hincrby(key, f"{strategy}:tokens", max(0, int(tokens)))
                pipe.expire(key, self._ttl)
                pipe.execute()
            except Exception as exc:  # pragma: no cover - memory copy already updated
                logger.warning("experience record to Redis failed: %s", exc)

    def priors(self, family: str) -> dict[str, StrategyStat]:
        """Known strategies for the family, best average yield first."""
        stats = self._from_redis(family) if self._redis is not None else self._memory(family)
        return dict(sorted(stats.items(), key=lambda kv: (-kv[1].avg_qualified, -kv[1].runs)))

    def hints(self, family: str, limit: int = 3) -> list[dict[str, Any]]:
        """Compact, prompt-ready priors (empty list when nothing is known)."""
        return [
            {"strategy": name, "runs": stat.runs, "avg_qualified": round(stat.avg_qualified, 2)}
            for name, stat in list(self.priors(family).items())[:limit]
        ]

    def snapshot(self) -> dict[str, dict[str, dict[str, int]]]:
        """Copy of everything known in this process (debug / tests)."""
        return {
            family: {
                name: {"runs": s.runs, "qualified": s.qualified, "tokens": s.tokens}
                for name, s in bucket.items()
            }
            for family, bucket in self._mem.items()
        }

    # -- internals -- --------------------------------------------------------

    def _memory(self, family: str) -> dict[str, StrategyStat]:
        return self._mem.get(family, {})

    def _from_redis(self, family: str) -> dict[str, StrategyStat]:
        try:
            raw = self._redis.hgetall(f"{self.PREFIX}{family}")
        except Exception as exc:  # pragma: no cover - degrade to in-process copy
            logger.warning("experience read from Redis failed: %s", exc)
            return self._memory(family)
        stats: dict[str, StrategyStat] = {}
        for key, value in raw.items():
            key_str = key.decode("utf-8") if isinstance(key, bytes) else str(key)
            strategy, _, field_name = key_str.partition(":")
            if field_name not in ("runs", "qualified", "tokens"):
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            stat = stats.setdefault(strategy, StrategyStat())
            setattr(stat, field_name, number)
        return stats


def attribute_qualified(state: dict[str, Any]) -> dict[str, int]:
    """Count truly-matched judgments per strategy that produced them.

    A judgment counts once, attributed to its first source path, so the numbers
    answer "which move found the people we wanted". Paths carry ``origin``; the
    converged final search judges under path id ``final``.
    """
    job = state.get("job_profile")
    must = {a.name for a in job.must_abilities} if job else set()
    origin = {p.id: (getattr(p, "origin", "") or "plan") for p in state.get("paths", [])}
    counts: dict[str, int] = {}
    for judgment in (state.get("accumulated") or {}).values():
        matched = set(judgment.matched_abilities or [])
        if not matched or (must and not (matched & must)):
            continue
        source_ids = list(judgment.source_path_ids or [])
        path_id = source_ids[0] if source_ids else None
        strategy = origin.get(path_id, "converged" if path_id == "final" else "plan")
        counts[strategy] = counts.get(strategy, 0) + 1
    return counts


__all__ = ["ExperienceStore", "StrategyStat", "attribute_qualified", "query_family"]
