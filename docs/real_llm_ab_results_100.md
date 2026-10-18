# Real LLM A/B Experiment

> Public synthetic corpus. Label provenance is recorded in benchmark metadata; this is an observed engineering run, not a company-data reproduction.

- Model: `search-agent-primary` -> upstream `qwen3.8-flash`
- Index: `search-agent-hybrid-v2-bench100`
- Corpus: `100` candidates (synthetic_oracle)
- Top-k: `5`
- Queries: `10`
- Query workers: `4`
- Wall clock: `586.5s`

| Query | Baseline pool recall | Agent pool recall | Baseline recall@k | Agent recall@k | New relevant | New candidates |
|---|---:|---:|---:|---:|---:|---:|
| `backend_concurrency` | 0.250 | 0.875 | 0.000 | 0.500 | 4 | 5 |
| `recommendation_ranking` | 0.857 | 1.000 | 0.286 | 0.571 | 2 | 3 |
| `data_realtime` | 0.929 | 0.857 | 0.357 | 0.357 | 1 | 1 |
| `stability_capacity` | 1.000 | 1.000 | 0.429 | 0.571 | 2 | 2 |
| `payment_distributed_tx` | 0.333 | 0.867 | 0.267 | 0.267 | 1 | 2 |
| `microservice_backend` | 0.533 | 0.933 | 0.333 | 0.133 | 0 | 3 |
| `ml_platform` | 0.778 | 1.000 | 0.278 | 0.278 | 3 | 3 |
| `data_architect` | 1.000 | 1.000 | 0.833 | 0.833 | 1 | 1 |
| `distributed_infra` | 0.667 | 0.833 | 0.333 | 0.417 | 3 | 3 |
| `architecture_transferable` | 1.000 | 1.000 | 0.800 | 0.800 | 0 | 1 |

| Aggregate | Baseline | Agent |
|---|---:|---:|
| Mean recall@5 | 0.392 | 0.473 |
| Mean pool recall | 0.735 | 0.937 |
| Mean precision | 0.740 | 0.840 |
| LLM calls | 20 | 59 |
| Tokens | 148428 | 446704 |
| p50 latency | 50694.3 ms | 139887.9 ms |
| p95 latency | 57039.6 ms | 185296.1 ms |

Paired mean recall delta = **+0.081** (bootstrap 95% CI -0.031 to +0.201); pool-recall delta = **+0.202** (CI +0.065 to +0.351).

Cost multipliers: LLM calls ×3.0, tokens ×3.0, p50 latency ×2.8.

Agent found **17** relevant candidates absent from the baseline across 10 queries; novel-candidate precision = **0.708**.

## Per-query Results

### `backend_concurrency`

Query: 找有3年以上后端经验的工程师，必须做过高并发系统；熟悉Java和微服务者优先，大厂背景优先

- Relevant labels: `g001, g015, g029, g043, g057, g071, g085, g099`
- Baseline delivered: `g091, g090, g034, g062, g076`
- Agent delivered: `g055, g015, g099, g029, g085`
- Agent-only relevant: `g015, g029, g085, g099`
- Agent degraded: `budget_exceeded`

### `recommendation_ranking`

Query: 招推荐算法工程师，必须有5年以上机器学习和推荐系统经验；有召回、排序、CTR或线上A/B实验经验者优先

- Relevant labels: `g003, g017, g031, g045, g059, g073, g087`
- Baseline delivered: `g045, g003, g050, g038, g092`
- Agent delivered: `g045, g003, g017, g031, g023`
- Agent-only relevant: `g017, g031`
- Agent degraded: `none`

### `data_realtime`

Query: 找有3年以上数据工程经验的工程师；做过实时数仓、熟悉Spark和Flink者优先

- Relevant labels: `g004, g012, g018, g026, g032, g040, g046, g054, g060, g068, g074, g082, g088, g096`
- Baseline delivered: `g096, g060, g032, g074, g082`
- Agent delivered: `g060, g032, g074, g040, g096`
- Agent-only relevant: `g040`
- Agent degraded: `none`

### `stability_capacity`

Query: 找有3年以上经验的系统架构工程师，必须同时具备高并发系统设计和系统架构设计经验；做过稳定性治理或容量规划者优先

- Relevant labels: `g005, g019, g033, g047, g061, g075, g089`
- Baseline delivered: `g089, g019, g055, g047, g041`
- Agent delivered: `g089, g019, g061, g005, g055`
- Agent-only relevant: `g005, g061`
- Agent degraded: `none`

### `payment_distributed_tx`

Query: 找有3年以上后端经验的工程师，必须同时具备服务端开发和分布式系统经验；有支付、交易或分布式事务经验者优先

- Relevant labels: `g002, g014, g016, g028, g030, g042, g044, g056, g058, g070, g072, g084, g086, g098, g100`
- Baseline delivered: `g016, g028, g100, g084, g099`
- Agent delivered: `g011, g044, g028, g016, g100`
- Agent-only relevant: `g044`
- Agent degraded: `budget_exceeded`

### `microservice_backend`

Query: 招5年以上后端工程师，必须同时具备服务端开发和微服务架构经验；熟悉服务治理、高可用或Spring Cloud者优先

- Relevant labels: `g002, g006, g016, g020, g030, g034, g044, g048, g052, g058, g062, g072, g086, g090, g091`
- Baseline delivered: `g090, g091, g016, g034, g048`
- Agent delivered: `g055, g091, g013, g097, g090`
- Agent-only relevant: `(none)`
- Agent degraded: `none`

### `ml_platform`

Query: 找有3年以上机器学习经验的工程师；有模型训练、PyTorch、特征工程或在线推理经验者优先

- Relevant labels: `g003, g009, g011, g012, g014, g017, g023, g031, g037, g045, g051, g059, g065, g071, g073, g079, g087, g093`
- Baseline delivered: `g087, g079, g009, g023, g065`
- Agent delivered: `g079, g065, g051, g017, g031`
- Agent-only relevant: `g017, g031, g051`
- Agent degraded: `none`

### `data_architect`

Query: 找有5年以上经验的数据平台架构师，必须同时具备数据工程和系统架构设计经验；有实时数仓、容量规划或数据平台经验者优先

- Relevant labels: `g004, g018, g032, g060, g074, g088`
- Baseline delivered: `g032, g074, g004, g088, g018`
- Agent delivered: `g032, g074, g004, g088, g060`
- Agent-only relevant: `g060`
- Agent degraded: `none`

### `distributed_infra`

Query: 找有5年以上经验的分布式基础设施工程师，必须同时具备分布式系统和高并发系统设计经验；熟悉容灾、Kafka或链路追踪者优先

- Relevant labels: `g005, g011, g019, g025, g033, g039, g047, g053, g061, g067, g075, g089`
- Baseline delivered: `g061, g089, g005, g056, g047`
- Agent delivered: `g061, g053, g067, g005, g011`
- Agent-only relevant: `g011, g053, g067`
- Agent degraded: `none`

### `architecture_transferable`

Query: 找有8年以上经验的系统架构师，必须同时具备系统架构设计和微服务架构经验；跨团队协作、技术方案评审或可迁移背景优先

- Relevant labels: `g041, g055, g069, g083, g097`
- Baseline delivered: `g055, g097, g041, g069, g020`
- Agent delivered: `g055, g097, g041, g069, g005`
- Agent-only relevant: `(none)`
- Agent degraded: `none`

## Interpretation Boundary

## What This Run Shows

- Exploration improved clearly: mean pool recall increased from `0.735` to `0.937`, and the paired bootstrap interval excludes zero.
- Delivered top-5 recall increased from `0.392` to `0.473`, but its interval crosses zero; this 10-query sample does not establish a reliable average top-5 gain.
- Ranking quality did not simply trade off against recall: mean precision@5 increased from `0.740` to `0.840`, and `17` Agent-only candidates were relevant (`0.708` novel-candidate precision).
- The improvement costs roughly `3x` LLM calls, tokens, and p50 latency. Two Agent runs hit `budget_exceeded`.
- Not every query improved. `microservice_backend` regressed from recall@5 `0.333` to `0.133`; `data_realtime` widened its pool but did not improve delivered recall. The next iteration should focus on judge/rerank precision, converged-search budget allocation, and failure analysis for these query families.

- The corpus and labels are public and synthetic; absolute values must not be compared with company metrics.
- The run uses one model configuration and one observed sample per query; bootstrap CIs are descriptive, not significance tests.
- The baseline is the same LLM-understanding plus one-shot Hybrid V2 path; the controlled variable is reflection/revise.
