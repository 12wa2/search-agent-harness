from __future__ import annotations

import json
from pathlib import Path

from search_agent.corpus import QuerySpec, generate_corpus, write_corpus

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "examples" / "bench100"
BENCHMARK_PATH = ROOT / "examples" / "real_ab_queries_100.json"
SEED = 2026
SIZE = 100


BENCH_SPECS: tuple[tuple[str, QuerySpec], ...] = (
    (
        "backend_concurrency",
        QuerySpec(
            "找有3年以上后端经验的工程师，必须做过高并发系统；熟悉Java和微服务者优先，大厂背景优先",
            ("服务端开发", "高并发系统设计"),
            3,
            "后端工程师",
        ),
    ),
    (
        "recommendation_ranking",
        QuerySpec(
            "招推荐算法工程师，必须有5年以上机器学习和推荐系统经验；有召回、排序、CTR或线上A/B实验经验者优先",
            ("推荐系统", "机器学习"),
            5,
            "推荐算法工程师",
        ),
    ),
    (
        "data_realtime",
        QuerySpec(
            "找有3年以上数据工程经验的工程师；做过实时数仓、熟悉Spark和Flink者优先",
            ("数据工程",),
            3,
            "数据工程师",
        ),
    ),
    (
        "stability_capacity",
        QuerySpec(
            "找有3年以上经验的系统架构工程师，必须同时具备高并发系统设计和系统架构设计经验；做过稳定性治理或容量规划者优先",
            ("高并发系统设计", "系统架构设计"),
            3,
            "系统架构师",
        ),
    ),
    (
        "payment_distributed_tx",
        QuerySpec(
            "找有3年以上后端经验的工程师，必须同时具备服务端开发和分布式系统经验；有支付、交易或分布式事务经验者优先",
            ("服务端开发", "分布式系统"),
            3,
            "后端工程师",
        ),
    ),
    (
        "microservice_backend",
        QuerySpec(
            "招5年以上后端工程师，必须同时具备服务端开发和微服务架构经验；"
            "熟悉服务治理、高可用或Spring Cloud者优先",
            ("服务端开发", "微服务架构"),
            5,
            "后端工程师",
        ),
    ),
    (
        "ml_platform",
        QuerySpec(
            "找有3年以上机器学习经验的工程师；有模型训练、PyTorch、特征工程或在线推理经验者优先",
            ("机器学习",),
            3,
            "机器学习工程师",
        ),
    ),
    (
        "data_architect",
        QuerySpec(
            "找有5年以上经验的数据平台架构师，必须同时具备数据工程和系统架构设计经验；有实时数仓、容量规划或数据平台经验者优先",
            ("数据工程", "系统架构设计"),
            5,
            "数据平台架构师",
        ),
    ),
    (
        "distributed_infra",
        QuerySpec(
            "找有5年以上经验的分布式基础设施工程师，必须同时具备分布式系统和高并发系统设计经验；熟悉容灾、Kafka或链路追踪者优先",
            ("分布式系统", "高并发系统设计"),
            5,
            "基础设施工程师",
        ),
    ),
    (
        "architecture_transferable",
        QuerySpec(
            "找有8年以上经验的系统架构师，必须同时具备系统架构设计和微服务架构经验；跨团队协作、技术方案评审或可迁移背景优先",
            ("系统架构设计", "微服务架构"),
            8,
            "系统架构师",
        ),
    ),
)


QUERY_META: dict[str, dict[str, list[str]]] = {
    "backend_concurrency": {
        "must": ["3年以上", "后端或架构经验", "高并发系统经验"],
        "nice": ["Java", "微服务", "大厂背景"],
    },
    "recommendation_ranking": {
        "must": ["5年以上", "推荐系统", "机器学习"],
        "nice": ["召回", "排序", "CTR", "A/B实验"],
    },
    "data_realtime": {
        "must": ["3年以上", "数据工程"],
        "nice": ["实时数仓", "Spark", "Flink", "数据治理"],
    },
    "stability_capacity": {
        "must": ["高并发", "系统架构", "稳定性或容量规划"],
        "nice": ["微服务", "全链路压测", "容灾"],
    },
    "payment_distributed_tx": {
        "must": ["3年以上", "服务端开发", "分布式系统"],
        "nice": ["支付", "交易", "分布式事务"],
    },
    "microservice_backend": {
        "must": ["5年以上", "服务端开发", "微服务架构"],
        "nice": ["服务治理", "高可用", "Spring Cloud"],
    },
    "ml_platform": {
        "must": ["3年以上", "机器学习"],
        "nice": ["模型训练", "PyTorch", "特征工程", "在线推理"],
    },
    "data_architect": {
        "must": ["5年以上", "数据工程", "系统架构"],
        "nice": ["实时数仓", "容量规划", "数据平台"],
    },
    "distributed_infra": {
        "must": ["5年以上", "分布式系统", "高并发"],
        "nice": ["容灾", "Kafka", "链路追踪"],
    },
    "architecture_transferable": {
        "must": ["8年以上", "系统架构", "微服务架构"],
        "nice": ["技术方案评审", "服务治理", "跨团队协作"],
    },
}


def main() -> int:
    specs = tuple(spec for _query_id, spec in BENCH_SPECS)
    corpus = generate_corpus(size=SIZE, seed=SEED, queries=specs)
    candidates_path, oracle_path = write_corpus(corpus, OUT_DIR)

    rows = []
    for query_id, spec in BENCH_SPECS:
        relevant = list(corpus.relevant(spec))
        if len(relevant) < 3:
            raise ValueError(f"{query_id} has only {len(relevant)} oracle-relevant candidates")
        meta = QUERY_META[query_id]
        rows.append(
            {
                "id": query_id,
                "query": spec.text,
                "must": meta["must"],
                "nice": meta["nice"],
                "requiredAbilities": list(spec.required_abilities),
                "minYears": spec.min_years,
                "roleTitle": spec.role_title,
                "relevant": relevant,
            }
        )

    payload = {
        "description": (
            "Synthetic-oracle complex-query benchmark over a seeded 100-candidate corpus. "
            "relevant is derived from generator ground truth (ability set + minimum years), "
            "not from human review."
        ),
        "label_kind": "synthetic_oracle",
        "seed": SEED,
        "size": corpus.size,
        "candidates": "examples/bench100/candidates.jsonl",
        "oracle": "examples/bench100/oracle.json",
        "queries": rows,
    }
    BENCHMARK_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("candidates:", candidates_path, corpus.size)
    print("oracle:", oracle_path)
    print("benchmark:", BENCHMARK_PATH, len(rows))
    for row in rows:
        print(row["id"], "relevant=", len(row["relevant"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
