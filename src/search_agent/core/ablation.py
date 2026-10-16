"""Ablation switches: flip exactly one structural decision at a time.

The loop makes several structural bets (multi-path planning, signature
dedup, the converged final search, reflection itself). Reporting one aggregate
"loop vs baseline" delta cannot say which bet paid off, so ``search-agent eval
--ablate`` runs the same corpus with exactly one switch flipped per row and
prints the deltas against the full loop.

Every switch defaults to *off*: serving code never sets them, and the pipeline
behaviour without an ``AblationConfig`` is byte-for-byte the production path.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AblationSwitches:
    """Structural deviations from the full loop (all default off)."""

    #: Keep re-trying a revised path whose intent signature was already tried.
    no_signature_dedup: bool = False
    #: Skip the converged final search; finalize falls back to merge + rerank.
    no_converged_search: bool = False


@dataclass(frozen=True)
class AblationRow:
    """One configuration in the ablation grid."""

    name: str
    label: str
    switches: AblationSwitches = AblationSwitches()
    #: Budget override: max search paths per iteration.
    max_paths_per_iteration: int | None = None
    #: Budget override: max reflection iterations.
    max_iterations: int | None = None
    #: Route evaluate straight to finalize (no reflect / revise).
    skip_reflection: bool = False
    #: One-shot pipeline: understand -> single hard-constraint path -> judge.
    single_pass: bool = False


#: Grid order is "full loop first, then remove one thing at a time".
ABLATION_ROWS: tuple[AblationRow, ...] = (
    AblationRow("full_loop", "完整八阶段循环"),
    AblationRow(
        "no_converged_search",
        "去掉收敛再搜",
        AblationSwitches(no_converged_search=True),
    ),
    AblationRow(
        "no_signature_dedup",
        "去掉签名防重",
        AblationSwitches(no_signature_dedup=True),
    ),
    AblationRow("single_iteration", "只跑一轮（无 reflect / revise）", skip_reflection=True),
    AblationRow("single_path_loop", "每轮只规划 1 条路径（无多路径）", max_paths_per_iteration=1),
    AblationRow("single_pass", "单趟基线（understand → 1 path → judge）", single_pass=True),
)

#: Row the others are compared against in the report.
REFERENCE_ROW = "full_loop"

#: Row whose surfaced ids seed the "only this row found it" column.
BASELINE_ROW = "single_pass"


def row_by_name(name: str) -> AblationRow | None:
    """Look up one grid row by its short name (None when unknown)."""
    for row in ABLATION_ROWS:
        if row.name == name:
            return row
    return None


__all__ = [
    "ABLATION_ROWS",
    "BASELINE_ROW",
    "REFERENCE_ROW",
    "AblationRow",
    "AblationSwitches",
    "row_by_name",
]
