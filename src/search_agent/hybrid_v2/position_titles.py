"""Lightweight position-title expansion without a graph database."""

from __future__ import annotations


def expand_position_titles(title: str) -> list[str]:
    text = str(title or "").strip()
    return [text] if text else []


__all__ = ["expand_position_titles"]
