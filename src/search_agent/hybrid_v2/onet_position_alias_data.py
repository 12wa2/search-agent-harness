"""Shared O*NET occupation alias row parsing for local catalog imports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DATA_PATH = Path(__file__).resolve().parent / "data" / "onet_position_aliases.json"


@dataclass(frozen=True)
class OnetPositionAliasMatch:
    code: str
    title_en: str
    title_zh: str
    canonical: str
    aliases: tuple[str, ...]
    terms: tuple[str, ...]


def data_path(override: str | Path | None = None) -> Path:
    if override is None:
        return _DATA_PATH
    return Path(override).expanduser().resolve()


def load_json_rows(path: str | Path | None = None) -> list[dict[str, Any]]:
    target = data_path(path)
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)]


def dedup_keep_order(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        text = str(term).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def row_lookup_keys(row: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for field in ("code", "title_en", "title_zh", "canonical"):
        value = str(row.get(field) or "").strip()
        if value:
            keys.append(value)
    for alias in list(row.get("aliases") or []):
        value = str(alias or "").strip()
        if value:
            keys.append(value)
    code = str(row.get("code") or "").strip()
    if code.endswith(".00"):
        keys.append(code[:-3])
    return dedup_keep_order(keys)


def row_strict_lookup_keys(row: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for field in ("code", "title_en"):
        value = str(row.get(field) or "").strip()
        if value:
            keys.append(value)
    code = str(row.get("code") or "").strip()
    if code.endswith(".00"):
        keys.append(code[:-3])
    return dedup_keep_order(keys)


def build_match_from_row(row: dict[str, Any]) -> OnetPositionAliasMatch | None:
    canonical = str(row.get("canonical") or row.get("title_zh") or "").strip()
    aliases = [str(v).strip() for v in list(row.get("aliases") or []) if str(v).strip()]
    terms = dedup_keep_order([canonical] + aliases)
    if not terms:
        return None
    return OnetPositionAliasMatch(
        code=str(row.get("code") or "").strip(),
        title_en=str(row.get("title_en") or "").strip(),
        title_zh=str(row.get("title_zh") or "").strip(),
        canonical=canonical,
        aliases=tuple(aliases),
        terms=tuple(terms),
    )


def build_indexes(
    rows: list[dict[str, Any]],
) -> tuple[
    dict[str, OnetPositionAliasMatch],
    dict[str, OnetPositionAliasMatch],
    dict[str, OnetPositionAliasMatch],
]:
    index: dict[str, OnetPositionAliasMatch] = {}
    strict_index: dict[str, OnetPositionAliasMatch] = {}
    canonical_index: dict[str, OnetPositionAliasMatch] = {}
    for row in rows:
        match = build_match_from_row(row)
        if match is None:
            continue
        canonical_index[match.canonical.casefold()] = match
        for key in row_lookup_keys(row):
            index[key.casefold()] = match
        for key in row_strict_lookup_keys(row):
            strict_index[key.casefold()] = match
    return index, strict_index, canonical_index


def validate_rows(rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    codes_seen: set[str] = set()
    strict_owner: dict[str, str] = {}

    for row in rows:
        code = str(row.get("code") or "").strip()
        title_en = str(row.get("title_en") or "").strip()
        if not code:
            errors.append("Missing code on O*NET alias row")
            continue
        if not title_en:
            errors.append(f"Missing title_en for code {code}")
        if code in codes_seen:
            errors.append(f"Duplicate O*NET code: {code}")
        codes_seen.add(code)

        match = build_match_from_row(row)
        if match is None:
            errors.append(f"Empty canonical/aliases for code {code}")
            continue

        for key in row_strict_lookup_keys(row):
            owner = strict_owner.get(key.casefold())
            if owner and owner != code:
                errors.append(
                    f"Strict lookup key conflict: '{key}' used by both {owner} and {code}"
                )
            strict_owner[key.casefold()] = code

    return errors


__all__ = [
    "OnetPositionAliasMatch",
    "build_indexes",
    "build_match_from_row",
    "data_path",
    "dedup_keep_order",
    "load_json_rows",
    "row_lookup_keys",
    "row_strict_lookup_keys",
    "validate_rows",
]
