"""Local O*NET/SOC title alias resolver.

Only the bundled JSON catalog (or an explicitly configured replacement file) is
used. There is no graph-database or network fallback, which keeps the public
engine deterministic and self-contained.
"""

from __future__ import annotations

import os
from functools import lru_cache

from .onet_position_alias_data import (
    OnetPositionAliasMatch,
    build_indexes,
    load_json_rows,
)


def _catalog_path() -> str:
    return os.environ.get("SEARCH_AGENT_HYBRID_V2_ONET_ALIASES", "").strip()


@lru_cache(maxsize=8)
def _load_alias_indexes(
    path: str,
) -> tuple[
    dict[str, OnetPositionAliasMatch],
    dict[str, OnetPositionAliasMatch],
    dict[str, OnetPositionAliasMatch],
]:
    return build_indexes(load_json_rows(path or None))


def _resolve_from_json(term: str, *, strict: bool) -> OnetPositionAliasMatch | None:
    key = str(term or "").strip().casefold()
    if not key:
        return None
    index, strict_index, canonical_index = _load_alias_indexes(_catalog_path())
    if strict:
        return strict_index.get(key)
    return canonical_index.get(key) or index.get(key)


def resolve_onet_position_alias(
    term: str,
    *,
    strict: bool = False,
) -> OnetPositionAliasMatch | None:
    """Resolve an exact SOC code/title, or a canonical/alias title when not strict."""

    return _resolve_from_json(term, strict=strict)


def expand_onet_position_aliases(term: str) -> list[str] | None:
    """Return curated recruiting titles for a known O*NET/SOC occupation."""

    match = resolve_onet_position_alias(term)
    return list(match.terms) if match is not None else None


def clear_onet_position_alias_json_cache() -> None:
    """Clear caches after tests or a runtime catalog change."""

    _load_alias_indexes.cache_clear()


__all__ = [
    "OnetPositionAliasMatch",
    "clear_onet_position_alias_json_cache",
    "expand_onet_position_aliases",
    "resolve_onet_position_alias",
]
