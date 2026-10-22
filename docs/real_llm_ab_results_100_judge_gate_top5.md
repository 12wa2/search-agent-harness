# Real LLM A/B Experiment

> Public synthetic corpus. Label provenance is recorded in benchmark metadata; this is an observed engineering run, not a company-data reproduction.

- Model: `search-agent-primary`
- Index: `search-agent-hybrid-v2-bench100`
- Corpus: `100` candidates (synthetic_oracle)
- Top-k: `5`
- Queries: `10`
- Wall clock: `308.36s`

| Query | Baseline pool recall | Agent pool recall | Baseline recall@k | Agent recall@k | New relevant | New candidates |
|---|---:|---:|---:|---:|---:|---:|
| `backend_concurrency` | 0.857 | 0.857 | 0.286 | 0.714 | 3 | 3 |
| `recommendation_ranking` | 1.000 | 1.000 | 0.333 | 0.167 | 1 | 4 |
| `data_realtime` | 1.000 | 1.000 | 0.312 | 0.312 | 2 | 2 |
| `stability_capacity` | 0.750 | 0.500 | 0.250 | 0.250 | 0 | 2 |
| `payment_distributed_tx` | 0.438 | 0.750 | 0.250 | 0.250 | 0 | 1 |
| `microservice_backend` | 0.750 | 0.750 | 0.250 | 0.188 | 2 | 4 |
| `ml_platform` | 1.000 | 1.000 | 0.333 | 0.333 | 2 | 2 |
| `data_architect` | 0.875 | 1.000 | 0.500 | 0.625 | 1 | 1 |
| `distributed_infra` | 0.857 | 1.000 | 0.286 | 0.357 | 1 | 1 |
| `architecture_transferable` | 1.000 | 1.000 | 0.800 | 0.600 | 1 | 3 |

| Aggregate | Baseline | Agent |
|---|---:|---:|
| Mean recall@5 | 0.360 | 0.380 |
| Mean pool recall | 0.853 | 0.886 |
| Mean precision | 0.720 | 0.760 |
| LLM calls | 20 | 63 |
| Tokens | 115912 | 361194 |
| p50 latency | 24051.8 ms | 80735.5 ms |
| p95 latency | 32508.5 ms | 103120.7 ms |

Paired mean recall delta = **+0.020** (bootstrap 95% CI -0.079 to +0.126); pool-recall delta = **+0.033** (CI -0.050 to +0.116).

Cost multipliers: LLM calls ×3.1, tokens ×3.1, p50 latency ×3.4.

Agent found **13** relevant candidates absent from the baseline across 10 queries; novel-candidate precision = **0.565**.

## Per-query Results

### `backend_concurrency`

Query: 找有3年以上后端经验的工程师，必须做过高并发系统；熟悉Java和微服务者优先，大厂背景优先

- Relevant labels: `v015, v029, v043, v057, v071, v085, v099`
- Baseline delivered: `v085, v071, v020, v035, v086`
- Agent delivered: `v085, v071, v043, v029, v099`
- Agent-only relevant: `v029, v043, v099`
- Agent degraded: `none`

### `recommendation_ranking`

Query: 招推荐算法工程师，必须有5年以上机器学习和推荐系统经验；有召回、排序、CTR或线上A/B实验经验者优先

- Relevant labels: `v017, v031, v045, v059, v073, v087`
- Baseline delivered: `v059, v087, v037, v051, v079`
- Agent delivered: `v079, v009, v010, v065, v017`
- Agent-only relevant: `v017`
- Agent degraded: `none`

### `data_realtime`

Query: 找有3年以上数据工程经验的工程师；做过实时数仓、熟悉Spark和Flink者优先

- Relevant labels: `v004, v012, v018, v022, v026, v032, v040, v046, v054, v060, v068, v074, v075, v082, v088, v096`
- Baseline delivered: `v088, v018, v068, v046, v096`
- Agent delivered: `v068, v074, v018, v088, v004`
- Agent-only relevant: `v004, v074`
- Agent degraded: `none`

### `stability_capacity`

Query: 找有3年以上经验的系统架构工程师，必须同时具备高并发系统设计和系统架构设计经验；做过稳定性治理或容量规划者优先

- Relevant labels: `v005, v019, v027, v033, v047, v061, v075, v089`
- Baseline delivered: `v061, v047, v041, v097, v006`
- Agent delivered: `v061, v047, v097, v083, v032`
- Agent-only relevant: `(none)`
- Agent degraded: `none`

### `payment_distributed_tx`

Query: 找有3年以上后端经验的工程师，必须同时具备服务端开发和分布式系统经验；有支付、交易或分布式事务经验者优先

- Relevant labels: `v002, v014, v016, v028, v030, v042, v044, v056, v058, v062, v070, v072, v084, v086, v098, v100`
- Baseline delivered: `v086, v100, v098, v044, v035`
- Agent delivered: `v086, v100, v098, v044, v067`
- Agent-only relevant: `(none)`
- Agent degraded: `none`

### `microservice_backend`

Query: 招5年以上后端工程师，必须同时具备服务端开发和微服务架构经验；熟悉服务治理、高可用或Spring Cloud者优先

- Relevant labels: `v002, v015, v016, v020, v030, v034, v044, v048, v058, v062, v066, v072, v076, v086, v090, v100`
- Baseline delivered: `v020, v044, v066, v056, v086`
- Agent delivered: `v020, v097, v072, v027, v030`
- Agent-only relevant: `v030, v072`
- Agent degraded: `none`

### `ml_platform`

Query: 找有3年以上机器学习经验的工程师；有模型训练、PyTorch、特征工程或在线推理经验者优先

- Relevant labels: `v003, v009, v010, v017, v023, v031, v037, v045, v051, v059, v065, v073, v079, v087, v093`
- Baseline delivered: `v087, v059, v051, v037, v065`
- Agent delivered: `v087, v059, v079, v009, v051`
- Agent-only relevant: `v009, v079`
- Agent degraded: `none`

### `data_architect`

Query: 找有5年以上经验的数据平台架构师，必须同时具备数据工程和系统架构设计经验；有实时数仓、容量规划或数据平台经验者优先

- Relevant labels: `v004, v018, v032, v046, v060, v074, v075, v088`
- Baseline delivered: `v004, v074, v018, v046, v047`
- Agent delivered: `v074, v004, v018, v046, v032`
- Agent-only relevant: `v032`
- Agent degraded: `none`

### `distributed_infra`

Query: 找有5年以上经验的分布式基础设施工程师，必须同时具备分布式系统和高并发系统设计经验；熟悉容灾、Kafka或链路追踪者优先

- Relevant labels: `v005, v011, v019, v025, v033, v039, v047, v053, v061, v067, v075, v081, v089, v095`
- Baseline delivered: `v061, v019, v089, v011, v057`
- Agent delivered: `v061, v011, v019, v089, v047`
- Agent-only relevant: `v047`
- Agent degraded: `none`

### `architecture_transferable`

Query: 找有8年以上经验的系统架构师，必须同时具备系统架构设计和微服务架构经验；跨团队协作、技术方案评审或可迁移背景优先

- Relevant labels: `v041, v055, v069, v083, v097`
- Baseline delivered: `v097, v083, v069, v055, v002`
- Agent delivered: `v097, v041, v072, v083, v030`
- Agent-only relevant: `v041`
- Agent degraded: `none`

## Interpretation Boundary

- The corpus and labels are public and synthetic; absolute values must not be compared with company metrics.
- The run uses one model configuration and one observed sample per query; bootstrap CIs are descriptive, not significance tests.
- The baseline is the same LLM-understanding plus one-shot Hybrid V2 path; the controlled variable is reflection/revise.
