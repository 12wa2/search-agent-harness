# Evaluation

> 本页只报告真实 LLM + Hybrid V2 的 top-5 A/B 复核。候选人与查询均为合成数据，不使用企业真实简历；结果只用于分析工程机制与成本，不代表生产环境表现。

## 1. 实验设置

本页只描述真实 LLM A/B：100 个合成候选人、10 条 Query、top-5。仓库另有一套用于 CI 门禁的 Oracle / 消融基准（120 个候选人、Seed 7、top-10），两套数据用途不同，不能混用指标。

- 语料：100 个 V2 原生字段合成候选人
- 查询：10 条复杂 Query
- 检索：Elasticsearch 索引 `search-agent-hybrid-v2-bench100` + Hybrid V2
- 客户端模型别名：`search-agent-primary`
- 网关记录：实验产物只记录客户端别名 `search-agent-primary`；未持久化逐次上游模型与 fallback 状态，不能用仓库示例网关配置反推实验所用模型
- 排序策略：`final_selection_policy=current`
- 交付深度：top-5
- 基线：`understand → 单路径 → judge → finalize`
- 实验组：Agent 反思链路

两条链路共享语料、模型、检索工具和自动生成标签；唯一结构变量是反思循环。每个 Query 只观察一次，bootstrap 区间是描述性的，不是显著性检验。

## 2. 结果

| 指标 | 单趟基线 | Agent 反思循环 | 变化 |
|---|---:|---:|---:|
| 平均 recall@5 | 0.334 | 0.425 | +0.092（bootstrap 95% CI -0.001 ~ +0.194） |
| 平均池召回 | 0.788 | 0.875 | +0.087（CI +0.004 ~ +0.170） |
| 平均 precision@5 | 0.660 | 0.820 | +0.160（描述性） |
| Agent 独有交付候选人 | — | 21 人 | — |
| 其中相关 | — | 14 人（1.4 / query） | — |
| 新增候选相关率 | — | 0.667 | — |
| LLM 调用 | 20 | 65 | ×3.25 |
| tokens | 113,495 | 412,812 | ×3.64 |
| p50 延迟 | 25.5 s | 86.7 s | ×3.40 |
| p95 延迟 | 32.2 s | 116.0 s | ×3.60 |
| 降级次数 | 0 | 0 | — |

### 结果边界

- 池召回提升的配对 bootstrap 区间不包含 0。
- top-5 召回虽然从 0.334 提升到 0.425，但区间 `-0.001 ~ +0.194` 跨过 0；这 10 条合成查询不足以证明稳定的平均交付提升。
- 精确率没有因扩大召回而下降；21 个 Agent 独有交付候选人中 14 个相关（0.667）。Precision@5 的提升没有配对 CI，只作描述性报告。
- LLM 调用约为基线的 3.25 倍、tokens 约为 3.64 倍，p95 延迟从 32.2 s 升到 116.0 s；本次 10 条查询均未触发 `budget_exceeded`。
- 并非每条查询都提升：`stability_capacity` 的 recall@5 从 0.500 降到 0.375，`microservice_backend` 从 0.250 降到 0.188。`payment_distributed_tx` 的最终 recall@5 从 0.062 升到 0.250，但池召回从 0.562 降到 0.438，说明更小的候选池也可能因排序改善而交付更好。
- 下一轮应优先修 judge / rerank 精度、收敛再搜的预算分配，以及落后查询族的失败分析；对应假设和验证方法见[技术规划](ROADMAP.md)。

逐查询结果、bootstrap 区间与原始 JSON：

- [top-5 完整报告](real_llm_ab_results_100.md)
- [top-5 原始 JSON](real_llm_ab_results_100.json)
- [当前 V2 原生字段基准与复现说明](../examples/bench_v2_100/README.md)

历史产物（保留用于追溯，不作为当前结论）：

- [current rerun](real_llm_ab_results_100_current_rerun_top5.md)
- [final v2](real_llm_ab_results_100_final_v2_top5.md)
- [judge gate](real_llm_ab_results_100_judge_gate_top5.md)
- [judge gate v2](real_llm_ab_results_100_judge_gate_v2_top5.md)

当前 canonical 报告是 `real_llm_ab_results_100.md`；带 `_current_rerun`、`_final_v2`、
`_judge_gate`、`_judge_gate_v2` 后缀的文件是不同排序策略下的历史复跑结果。

## 3. 解释边界

- 语料和标签公开、合成；标签由生成器自动生成，不是人工标注；绝对数值不能与公司数据或生产 A/B 直接比较。
- 真实模型实验只覆盖一个模型配置，每个查询一次观测；这里的 bootstrap 区间是描述性的，不是显著性检验。
- 原始 JSON 记录的是客户端别名，没有逐次记录实际解析到的上游模型或 fallback 状态；本页只能确认网关配置，不能反推每次调用都使用了主映射。
- A/B 报告记录的是服务入口结果，未逐条记录实际执行阶段；不能据此断言每条 Query 都执行了全部八阶段。
- 本页不报告无法在仓库内复现的非公开指标。
