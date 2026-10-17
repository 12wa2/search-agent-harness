"""Recall/rerank funnel sizing for Hybrid V2."""

from __future__ import annotations

from dataclasses import dataclass

from search_agent.hybrid_v2.config import HybridV2Config


@dataclass(frozen=True)
class FunnelLimits:
    cheap_pool_size: int
    rerank_scan_cap: int
    rerank_target_n: int
    rerank_shard_size: int

    @classmethod
    def from_config(cls, config: HybridV2Config) -> "FunnelLimits":
        return cls(
            cheap_pool_size=max(1, int(config.cheap_pool_size)),
            rerank_scan_cap=max(1, int(config.rerank_scan_cap)),
            rerank_target_n=max(1, int(config.rerank_target_n)),
            rerank_shard_size=max(1, int(config.rerank_shard_size)),
        )

    def recall_pool_size(self, top_k: int, override: int | None = None) -> int:
        if override is not None and int(override) > 0:
            return max(int(top_k), int(override))
        return max(int(top_k), self.cheap_pool_size)

    def rerank_scan_n(self) -> int:
        return min(self.rerank_scan_cap, self.rerank_target_n)

    def llm_shard_count(self, candidate_count: int) -> int:
        count = max(0, int(candidate_count))
        if count == 0:
            return 0
        return (count + self.rerank_shard_size - 1) // self.rerank_shard_size

    def snapshot(self) -> dict[str, int]:
        return {
            "cheapPoolSize": self.cheap_pool_size,
            "rerankScanCap": self.rerank_scan_cap,
            "rerankTargetN": self.rerank_target_n,
            "rerankScanN": self.rerank_scan_n(),
            "rerankShardSize": self.rerank_shard_size,
        }


__all__ = ["FunnelLimits"]
