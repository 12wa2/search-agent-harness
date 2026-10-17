"""SQLite (+FTS5) SearchTool: a zero-dependency persistent backend.

Same contract and identical results as ``tools/memory.py``, but the filtering
runs inside a real SQL engine:

* every ``WhereNode`` tree is compiled to a ``WHERE`` clause over a small
  normalized schema (``candidates`` + ``candidate_terms``); operators mirror
  ``tools/text.py`` exactly, and the clause is at worst an over-approximation
  that never drops a document the shared evaluator would keep;
* survivors are re-verified with ``where_matches`` and scored with the shared
  helpers, so both bundled backends return identical rankings on the same
  corpus (pinned by ``tests/test_sqlite_fts.py``);
* an FTS5 virtual table indexes the tokenized candidate text and backs the
  keyword channel (``search_text``, BM25-ranked).

Python standard library only. Pass a file path as ``db_path`` for a persistent
index; the default is a fresh in-memory database.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Sequence

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    SearchToolResult,
    ToolCandidate,
)
from search_agent.models.api import CandidateItem
from search_agent.models.ir import StructuredConditionIR, WhereNode
from search_agent.tools.memory import load_candidates
from search_agent.tools.text import (
    TERM_FIELDS,
    ability_hits,
    as_number,
    build_tool_candidate,
    candidate_text,
    collect_leaves,
    hard_facts,
    normalized_terms,
    quality_label,
    soft_score,
    tokens,
    where_matches,
)

__all__ = ["SqliteFtsSearchTool"]

__all__ = ["SqliteFtsSearchTool"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    doc_id TEXT PRIMARY KEY,
    blob TEXT NOT NULL,
    work_years REAL,
    age REAL
);
CREATE TABLE IF NOT EXISTS candidate_terms (
    doc_id TEXT NOT NULL,
    field TEXT NOT NULL,
    position INTEGER NOT NULL,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS candidate_terms_lookup_idx ON candidate_terms(field, value);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS candidate_fts USING fts5(
    doc_id UNINDEXED,
    tokens
);
"""
# IR fields that alias another term field; ``latest_*`` only keeps the newest entry.
_LATEST_FIELDS = {"latest_company": "company", "latest_work_job": "position"}
_ALIAS_FIELDS = {"live_city": "city"}
_NUMERIC_FIELDS = ("work_years", "age")


class SqliteFtsSearchTool:
    """SQLite/FTS5 implementation of the ``SearchTool`` protocol."""

    def __init__(
        self,
        candidates: str | Path | Sequence[dict[str, Any] | CandidateItem],
        *,
        db_path: str = ":memory:",
        fts: bool = True,
    ) -> None:
        self._items = load_candidates(candidates)
        self._by_id = {item.docId: item for item in self._items}
        self._fts = bool(fts)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        if self._fts:
            try:
                self._conn.executescript(_FTS_SCHEMA)
            except sqlite3.OperationalError as exc:  # pragma: no cover - exotic builds
                raise RuntimeError(
                    "this SQLite build has no FTS5 support; construct with fts=False"
                ) from exc
        self._ingest()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @property
    def size(self) -> int:
        return len(self._items)

    # -- ingest --------------------------------------------------------------

    @staticmethod
    def _term_rows(item: CandidateItem) -> list[tuple[str, int, str]]:
        """Normalized (field, position, value) rows; same fields as the shared evaluator."""
        rows: list[tuple[str, int, str]] = []
        for field, values in normalized_terms(item).items():
            rows.extend((field, position, value) for position, value in enumerate(values))
        return rows

    def _ingest(self) -> None:
        for item in self._items:
            blob = candidate_text(item)
            self._conn.execute("DELETE FROM candidate_terms WHERE doc_id = ?", (item.docId,))
            self._conn.execute(
                "INSERT INTO candidates(doc_id, blob, work_years, age) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(doc_id) DO UPDATE SET "
                "blob = excluded.blob, work_years = excluded.work_years, age = excluded.age",
                (
                    item.docId,
                    blob,
                    item.workYears if isinstance(item.workYears, (int, float)) else None,
                    item.age if isinstance(item.age, (int, float)) else None,
                ),
            )
            self._conn.executemany(
                "INSERT INTO candidate_terms(doc_id, field, position, value) VALUES (?, ?, ?, ?)",
                [(item.docId, *row) for row in self._term_rows(item)],
            )
            if self._fts:
                self._conn.execute("DELETE FROM candidate_fts WHERE doc_id = ?", (item.docId,))
                self._conn.execute(
                    "INSERT INTO candidate_fts(doc_id, tokens) VALUES (?, ?)",
                    (item.docId, " ".join(tokens(blob))),
                )
        self._conn.commit()

    # -- extra channels ------------------------------------------------------

    def search_text(self, query: str, *, top_k: int = 5) -> list[tuple[str, float]]:
        """Keyword channel: BM25 over the FTS5 index of tokenized candidate text."""
        if not self._fts:
            raise RuntimeError("FTS5 index disabled at construction (fts=False)")
        terms = sorted(set(tokens(query)))
        if not terms:
            return []
        match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        with self._lock:
            rows = self._conn.execute(
                "SELECT doc_id, bm25(candidate_fts) AS rank FROM candidate_fts "
                "WHERE candidate_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, max(0, int(top_k))),
            ).fetchall()
        return [(str(row["doc_id"]), round(-float(row["rank"]), 4)) for row in rows]

    # -- WhereNode -> SQL ----------------------------------------------------

    def compile_where(self, node: WhereNode | None) -> dict[str, Any]:
        """Expose the SQL the backend would run for an intent (debugging aid)."""
        sql, params, exact = self._compile(node)
        return {"sql": sql, "params": list(params), "exact": exact}

    def _compile(self, node: WhereNode | None) -> tuple[str, list[Any], bool]:
        """Compile a where tree to ``(sql, params, exact)``.

        ``exact`` is False when the SQL is a deliberate over-approximation
        (unknown field, non-compilable ``not``); the shared Python evaluator
        stays the source of truth either way.
        """
        if node is None:
            return "1", [], True
        if node.type in ("and", "or"):
            parts = [self._compile(child) for child in node.children]
            params = [value for _, values, _ in parts for value in values]
            exact = all(part[2] for part in parts)
            if node.type == "and":
                sql = " AND ".join(f"({part[0]})" for part in parts)
                return sql, params, exact
            need = int(node.minimumShouldMatch or 1)
            if need <= 1:
                sql = " OR ".join(f"({part[0]})" for part in parts)
                return sql, params, exact
            counted = " + ".join(f"(CASE WHEN ({part[0]}) THEN 1 ELSE 0 END)" for part in parts)
            return f"({counted}) >= {need}", params, exact
        if node.type == "not":
            sql, params, exact = self._compile(node.child)
            if not exact:
                return "1", [], False
            return f"NOT ({sql})", params, True
        if node.condition is None:
            return "1", [], False
        return self._compile_leaf(node.condition)

    def _compile_leaf(self, cond: StructuredConditionIR) -> tuple[str, list[Any], bool]:
        field = _ALIAS_FIELDS.get(cond.field, cond.field)
        if field in _NUMERIC_FIELDS:
            return self._compile_numeric_leaf(field, cond)
        term_field: str | None = None
        latest = False
        if field in _LATEST_FIELDS:
            term_field, latest = _LATEST_FIELDS[field], True
        elif field in TERM_FIELDS:
            term_field = field
        if term_field is None:
            # Unknown to the shared evaluator as well: stay permissive.
            return "1", [], False
        wanted = sorted({str(v).strip().lower() for v in cond.values if str(v).strip()})
        if not wanted:
            return "0", [], True
        scope = "AND t.field = ?" + (" AND t.position = 0" if latest else "")
        marks = ", ".join("?" for _ in wanted)
        exists = (
            f"EXISTS (SELECT 1 FROM candidate_terms t WHERE t.doc_id = c.doc_id "
            f"{scope} AND t.value IN ({marks}))"
        )
        params: list[Any] = [term_field, *wanted]
        if cond.operator in ("any", "eq"):
            return exists, params, True
        if cond.operator == "not":
            return f"NOT ({exists})", params, True
        if cond.operator == "all":
            counted = (
                f"(SELECT COUNT(DISTINCT t.value) FROM candidate_terms t "
                f"WHERE t.doc_id = c.doc_id {scope} AND t.value IN ({marks}))"
            )
            return f"{counted} = ?", [term_field, *wanted, len(wanted)], True
        # numeric operator on a term-backed field: the shared evaluator says False
        return "0", [], True

    @staticmethod
    def _compile_numeric_leaf(
        field: str, cond: StructuredConditionIR
    ) -> tuple[str, list[Any], bool]:
        if cond.operator not in ("gte", "lte", "range"):
            return "0", [], True
        numbers = [n for n in (as_number(value) for value in cond.values) if n is not None]
        if not numbers:
            return "0", [], True
        if cond.operator == "gte":
            return f"COALESCE(c.{field} >= ?, 0)", [numbers[0]], True
        if cond.operator == "lte":
            return f"COALESCE(c.{field} <= ?, 0)", [numbers[0]], True
        low, high = sorted(numbers[:2])
        return f"COALESCE(c.{field} BETWEEN ? AND ?, 0)", [low, high], True

    # -- SearchTool protocol -------------------------------------------------

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult:
        started = time.perf_counter()
        excluded = set(req.exclude_doc_ids or [])
        where_sql, where_params, _exact = self._compile(req.intent.where)
        sql = f"SELECT c.doc_id FROM candidates c WHERE ({where_sql})"
        params = list(where_params)
        if excluded:
            marks = ", ".join("?" for _ in excluded)
            sql += f" AND c.doc_id NOT IN ({marks})"
            params.extend(sorted(excluded))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        # The SQL clause is at worst an over-approximation: the shared Python
        # evaluator stays the single source of truth for constraint semantics.
        filtered = [
            self._by_id[str(row["doc_id"])]
            for row in rows
            if where_matches(req.intent.where, self._by_id[str(row["doc_id"])])
        ]
        filtered_ms = int((time.perf_counter() - started) * 1000)

        score_started = time.perf_counter()
        leaves = collect_leaves(req.intent.where)
        scored: list[tuple[float, list[dict[str, Any]], list[dict[str, Any]], CandidateItem]] = []
        for item in filtered:
            blob = candidate_text(item)
            hard = hard_facts(leaves, item)
            score, soft = soft_score(req.intent.semantic, blob)
            scored.append((score, hard, soft, item))
        score_ms = int((time.perf_counter() - score_started) * 1000)

        scored.sort(key=lambda row: (-row[0], row[3].docId))
        total = len(scored)
        page_size = int(req.recall_pool_size or req.top_k)
        page = scored[: max(0, page_size)]
        items = [build_tool_candidate(score, hard, soft, item) for score, hard, soft, item in page]
        quality = quality_label(len(items), total, page_size)

        es_query: dict[str, Any] | None = None
        if req.details:
            es_query = {
                "backend": "sqlite-fts5",
                "whereSql": where_sql,
                "params": list(where_params),
                "filteredCount": total,
                "excludedCount": len(excluded),
                "semanticConditions": [s.text for s in req.intent.semantic or []],
            }

        return SearchToolResult(
            items=items,
            total=total,
            total_relation="eq",
            quality=quality,
            es_query=es_query,
            timings_ms={
                "filter": filtered_ms,
                "score": score_ms,
                "total": int((time.perf_counter() - started) * 1000),
            },
        )

    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult:
        started = time.perf_counter()
        abilities = [str(a) for a in (req.job_context.get("abilities") or [])]
        job_hard: list[StructuredConditionIR] = []
        for raw in req.job_context.get("hard_constraints") or []:
            try:
                job_hard.append(StructuredConditionIR.model_validate(raw))
            except Exception:  # noqa: BLE001 - ignore malformed job facts
                continue

        scored: list[tuple[float, list[dict[str, Any]], ToolCandidate]] = []
        for candidate in req.candidates:
            blob = candidate_text(candidate.item)
            hits = ability_hits(abilities, blob)
            hard = hard_facts(job_hard, candidate.item)
            score = round(1.0 + 0.5 * hits + 0.2 * sum(1 for h in hard if h["matched"]), 4)
            scored.append((score, hard, candidate))

        scored.sort(key=lambda row: (-row[0], row[2].doc_id))
        top = scored[: max(0, int(req.top_k))]
        items = [
            build_tool_candidate(score, hard, [], candidate.item) for score, hard, candidate in top
        ]
        return SearchToolResult(
            items=items,
            total=len(scored),
            total_relation="eq",
            quality="rich" if items else "empty",
            timings_ms={"total": int((time.perf_counter() - started) * 1000)},
        )
