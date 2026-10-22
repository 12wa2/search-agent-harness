# Real LLM A/B Experiment

> Public synthetic corpus. Label provenance is recorded in benchmark metadata; this is an observed engineering run, not a company-data reproduction.

- Model: `search-agent-primary`
- Index: `search-agent-hybrid-v2-bench100`
- Corpus: `100` candidates (synthetic_oracle)
- Top-k: `5`
- Queries: `10`
- Wall clock: `314.24s`

| Query | Baseline pool recall | Agent pool recall | Baseline recall@k | Agent recall@k | New relevant | New candidates |
|---|---:|---:|---:|---:|---:|---:|
| `backend_concurrency` | 0.857 | 1.000 | 0.286 | 0.000 | 0 | 4 |
| `recommendation_ranking` | 0.667 | 1.000 | 0.333 | 0.500 | 1 | 3 |
| `data_realtime` | 1.000 | 1.000 | 0.312 | 0.312 | 0 | 0 |
| `stability_capacity` | 0.750 | 1.000 | 0.500 | 0.000 | 0 | 5 |
| `payment_distributed_tx` | 0.688 | 0.812 | 0.312 | 0.188 | 1 | 3 |
| `microservice_backend` | 0.812 | 0.875 | 0.250 | 0.250 | 3 | 4 |
| `ml_platform` | 1.000 | 1.000 | 0.333 | 0.200 | 2 | 4 |
| `data_architect` | 0.875 | 1.000 | 0.500 | 0.625 | 1 | 1 |
| `distributed_infra` | 0.857 | 0.857 | 0.286 | 0.286 | 1 | 2 |
| `architecture_transferable` | 1.000 | 1.000 | 1.000 | 0.600 | 0 | 2 |

| Aggregate | Baseline | Agent |
|---|---:|---:|
| Mean recall@5 | 0.411 | 0.296 |
| Mean pool recall | 0.851 | 0.954 |
| Mean precision | 0.800 | 0.600 |
| LLM calls | 20 | 65 |
| Tokens | 118857 | 416990 |
| p50 latency | 22040.5 ms | 82359.8 ms |
| p95 latency | 31378.4 ms | 98712.8 ms |

Paired mean recall delta = **-0.115** (bootstrap 95% CI -0.244 to +0.003); pool-recall delta = **+0.104** (CI +0.040 to +0.179).

Cost multipliers: LLM calls ×3.2, tokens ×3.5, p50 latency ×3.7.

Agent found **9** relevant candidates absent from the baseline across 10 queries; novel-candidate precision = **0.321**.

## Per-query Results

### `backend_concurrency`

Query: 找有3年以上后端经验的工程师，必须做过高并发系统；熟悉Java和微服务者优先，大厂背景优先

- Relevant labels: `v015, v029, v043, v057, v071, v085, v099`
- Baseline delivered: `v085, v071, v020, v035, v086`
- Agent delivered: `v020, v044, v046, v090, v097`
- Agent-only relevant: `(none)`
- Agent degraded: `none`

### `recommendation_ranking`

Query: 招推荐算法工程师，必须有5年以上机器学习和推荐系统经验；有召回、排序、CTR或线上A/B实验经验者优先

- Relevant labels: `v017, v031, v045, v059, v073, v087`
- Baseline delivered: `v059, v087, v037, v051, v079`
- Agent delivered: `v087, v059, v036, v060, v045`
- Agent-only relevant: `v045`
- Agent degraded: `none`

### `data_realtime`

Query: 找有3年以上数据工程经验的工程师；做过实时数仓、熟悉Spark和Flink者优先

- Relevant labels: `v004, v012, v018, v022, v026, v032, v040, v046, v054, v060, v068, v074, v075, v082, v088, v096`
- Baseline delivered: `v068, v088, v018, v074, v004`
- Agent delivered: `v068, v088, v018, v074, v004`
- Agent-only relevant: `(none)`
- Agent degraded: `none`

### `stability_capacity`

Query: 找有3年以上经验的系统架构工程师，必须同时具备高并发系统设计和系统架构设计经验；做过稳定性治理或容量规划者优先

- Relevant labels: `v005, v019, v027, v033, v047, v061, v075, v089`
- Baseline delivered: `v061, v047, v019, v011, v089`
- Agent delivered: `v097, v046, v020, v002, v034`
- Agent-only relevant: `(none)`
- Agent degraded: `none`

### `payment_distributed_tx`

Query: 找有3年以上后端经验的工程师，必须同时具备服务端开发和分布式系统经验；有支付、交易或分布式事务经验者优先

- Relevant labels: `v002, v014, v016, v028, v030, v042, v044, v056, v058, v062, v070, v072, v084, v086, v098, v100`
- Baseline delivered: `v100, v098, v086, v044, v030`
- Agent delivered: `v020, v035, v100, v086, v028`
- Agent-only relevant: `v028`
- Agent degraded: `none`

### `microservice_backend`

Query: 招5年以上后端工程师，必须同时具备服务端开发和微服务架构经验；熟悉服务治理、高可用或Spring Cloud者优先

- Relevant labels: `v002, v015, v016, v020, v030, v034, v044, v048, v058, v062, v066, v072, v076, v086, v090, v100`
- Baseline delivered: `v020, v044, v086, v066, v056`
- Agent delivered: `v097, v030, v090, v072, v020`
- Agent-only relevant: `v030, v072, v090`
- Agent degraded: `none`

### `ml_platform`

Query: 找有3年以上机器学习经验的工程师；有模型训练、PyTorch、特征工程或在线推理经验者优先

- Relevant labels: `v003, v009, v010, v017, v023, v031, v037, v045, v051, v059, v065, v073, v079, v087, v093`
- Baseline delivered: `v087, v059, v051, v037, v023`
- Agent delivered: `v059, v003, v045, v021, v022`
- Agent-only relevant: `v003, v045`
- Agent degraded: `none`

### `data_architect`

Query: 找有5年以上经验的数据平台架构师，必须同时具备数据工程和系统架构设计经验；有实时数仓、容量规划或数据平台经验者优先

- Relevant labels: `v004, v018, v032, v046, v060, v074, v075, v088`
- Baseline delivered: `v004, v074, v032, v046, v047`
- Agent delivered: `v004, v074, v018, v046, v032`
- Agent-only relevant: `v018`
- Agent degraded: `none`

### `distributed_infra`

Query: 找有5年以上经验的分布式基础设施工程师，必须同时具备分布式系统和高并发系统设计经验；熟悉容灾、Kafka或链路追踪者优先

- Relevant labels: `v005, v011, v019, v025, v033, v039, v047, v053, v061, v067, v075, v081, v089, v095`
- Baseline delivered: `v061, v019, v089, v011, v057`
- Agent delivered: `v011, v019, v061, v086, v025`
- Agent-only relevant: `v025`
- Agent degraded: `none`

### `architecture_transferable`

Query: 找有8年以上经验的系统架构师，必须同时具备系统架构设计和微服务架构经验；跨团队协作、技术方案评审或可迁移背景优先

- Relevant labels: `v041, v055, v069, v083, v097`
- Baseline delivered: `v097, v041, v083, v069, v055`
- Agent delivered: `v083, v097, v041, v005, v088`
- Agent-only relevant: `(none)`
- Agent degraded: `none`

## Interpretation Boundary

- The corpus and labels are public and synthetic; absolute values must not be compared with company metrics.
- The run uses one model configuration and one observed sample per query; bootstrap CIs are descriptive, not significance tests.
- The baseline is the same LLM-understanding plus one-shot Hybrid V2 path; the controlled variable is reflection/revise.
