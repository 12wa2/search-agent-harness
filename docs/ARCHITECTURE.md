# 架构设计

> 本文说明 search-agent-harness 的运行机制。所有内容与代码一一对应
> （括号中给出对应文件与函数，可直接对照阅读）。

## 1. 设计原则

1. **LLM 只做判断，事实来自工具**。LLM 负责理解意图、规划路径、做能力级判断；
   候选人事实（hardMatches、简历快照）一律来自 `SearchTool` 的返回，prompt 明确禁止编造。
2. **控制面不抛异常**。任何阶段的任何失败都走降级矩阵（`core/degrade.py`），
   循环永远能前进或收敛；`degradedReason` 显式透传到响应与 trace。
3. **预算是第一公民**。迭代 / 路径 / LLM 调用 / 墙钟预算贯穿整个循环（`core/budget.py`），
   而不是用「超时兜底」处理失控。
4. **不引入 Agent 框架**。8 个阶段是纯函数，`LoopRunner` 就是一个显式循环；
   框架接入点（`LangGraphRunner`）通过 `runner.py` 的协议预留，阶段代码不需要改动。
5. **唯一协议边界**：`contract/tool.py` 的 `SearchTool`。换检索后端 = 换一个实现类。

## 2. 八个阶段

| # | 阶段 | 输入 | 输出 | 依赖 |
|---|------|------|------|------|
| 1 | understand | query（+ 多轮对话历史） | JobProfile + TalentProfile + 难度评估 | LLM |
| 2 | plan | 两个画像 | `list[SearchPath]`（多条不同召回意图） | LLM |
| 3 | execute | pending 路径 | PathResult（并行调用后端，质量分级） | SearchTool |
| 4 | judge | 路径结果 + 岗位能力 | CandidateJudgment（能力级判断） | LLM |
| 5 | evaluate | 累计池 + 路径状态 | 继续 / 收敛（规则，无 LLM） | — |
| 6 | reflect | 路径结果 + top 判断 | Reflection（共性 / 缺口 / 来源假设） | LLM |
| 7 | revise | 反思结果 | 路径动作：收紧 / 放宽 / 新增 / 丢弃 | — |
| 8 | finalize | 全部状态 | 最终排序结果（收敛再搜或合并重排） | LLM + SearchTool |

阶段约定（`stages/base.py` 的 `Deps` 注入 LLM / SearchTool / BudgetState / event_sink）：

- 每个阶段签名统一为 `run_xxx(state, deps) -> dict`，只返回增量更新；
- LLM 相关阶段的解析层都是**宽容解析**：单条坏数据跳过并记日志，不拖垮整个阶段
  （见 understand / plan / judge / reflect 的解析代码）；
- execute 内部的并行只用线程池，状态变更全部留在主线程（`harness.py` 顶部注释）。

## 3. 状态机与流转

主循环在 `harness.py::Harness.run`，`phase` 字段驱动，上限 60 步兜底：

```text
understand ──(need_clarify)──> 返回澄清问题
    │
    ├──(难度路由)──> single_pass：跳过 plan/reflect/revise，直接执行单路径
    │
    v
  plan ──> execute ──> judge ──> evaluate
                                   │
                     ┌─ 未达标且预算允许 ─> reflect ─> revise ─> execute（下一轮）
                     │
                     └─ 达标 / 预算耗尽 ─> finalize ─> done
```

关键分支：

- **难度路由**（`core/difficulty.py`）：understand 产出 `subjective_difficulty` 后立即生效——
  `high → single_pass`（`max_iterations=0`、单路径；evaluate 后跳过反思直接 finalize，
  `finalize_reason=difficulty_single_pass`）、`low → reduced`（迭代 1、每轮 2 路径）、
  `mid → standard`。目的是不让「客观就难」的查询白烧预算，也不让简单查询多轮空转。
- **预算守卫**：非 understand / finalize 阶段发现时间或 LLM 调用超预算，直接跳 finalize
  （`budget_exceeded`）。
- **全路径失败检测**：execute 后若本轮没有任何新 PathResult，判定所有路径失败，
  走 `fallback_baseline`（基线单趟检索）。

## 4. 预算与终止

`Budget`（`models/artifacts.py`）五个字段，默认值来自 `config.budget_from_env`：

| 字段 | 默认 | 含义 |
|---|---|---|
| `max_iterations` | 3 | 反思循环最大迭代数 |
| `max_paths_per_iteration` | 4 | 每轮最大路径数 |
| `target_qualified` | `max(top_k*2, 20)` | 合格候选人目标 |
| `time_budget_sec` | 12.0 | 墙钟预算（秒） |
| `max_llm_calls` | 40 | LLM 调用上限 |

终止判定集中在 `core/budget.py::should_finalize`，返回 `(finalize, reason)`，
共 5 种原因：`target_reached` / `max_iterations` / `time_budget` / `llm_budget` /
`no_progress`（没有 pending 路径且本轮无新增合格）。`BudgetState` 同时记录
`llm_calls` / `iteration` / `new_qualified_last_round` 三个计数器。

## 5. 降级矩阵

`core/degrade.py` 把 `(阶段, 失败类型)` 映射为 `(动作, degradedReason)`：

| 阶段 | 失败 | 动作 | degradedReason |
|---|---|---|---|
| understand | LLM 失败 | `fallback_baseline`：放弃 Agent，走基线单趟检索 | `understand_failed` |
| understand | 需要澄清 | `finalize_now`：直接返回澄清问题 | `understand_failed` |
| plan | LLM 失败 | `single_path`：用岗位硬条件直接构造单条路径 | `plan_failed_single_path` |
| execute | 全部路径失败 | `fallback_baseline` | `all_paths_failed` |
| judge | LLM 失败 | `skip_judge`：跳过能力判断，用工具事实继续 | `judge_failed` |
| rerank_pool | LLM 失败 | `skip_global_rerank`：按 per-path 分数排序收敛 | `global_rerank_failed` |
| 任意 | 预算耗尽 | `finalize_now` | `budget_exceeded` |

动作语义：`fallback_baseline` = 调用可选注入的基线函数 `fallback_fn`（没注入则返回空结果 +
降级原因）；`single_path` = 用 understand 的硬条件拼一条路径继续；`skip_*` = 用已有事实
继续；`finalize_now` = 立即收敛。Harness 应用失败处理时还会触发一次
`event_sink("degrade:<阶段>", state)`，方便观测。

## 6. Intent Signature（防重复检索）

`core/signature.py::intent_signature`：把搜索意图规范化后做 sha1——

- `where` 布尔树：leaf 只保留 `field / operator / values`（values 去空格、
  casefold、排序）；`and/or` 子节点按规范化 JSON 排序（顺序无关）；
  **有意丢弃** `weight / required / softText`——它们不改变召回域，只影响排序；
- `semantic` 条件按 `(text, fields)` 规范化后排序；
- `meta` 保留 `expandScope / peerHint / tenureIntent`。

同一签名的路径视为「同一次召回」，绝不重复执行：plan 产出的路径与 revise 新增的动作
都会对照 `tried_signatures`（状态里的 set，见 `models/state.py`）。

## 7. 合并与路径质量

跨路径 / 跨轮次的同一候选人（doc_id）在 `core/merge.py::merge_judgment` 中折叠：

- `score`：取 max；
- `matched_abilities`：并集去重；`missing_abilities`：并集后剔除已命中的；
- `hard_matches`：保留更多的一方；`summary`：取分数高的一方；
- `source_path_ids`：来源路径并集（最终结果里"来源"列的出处）。

路径质量分级 `path_quality`：`empty`（召回 0）/ `thin`（合格数 < rich_min）/ `rich`；
`rich_min = max(3, ceil(target_qualified / max_paths_per_iteration))`。
分级结果驱动 evaluate / reflect 的后续策略。

## 8. finalize：两条收敛路径

`stages/finalize.py::run_finalize`：

1. **收敛再搜（画像被反思循环充实过时）**：`where` 只用岗位硬条件（结构性过滤，
   如年限），`semantic` 用全部收敛后的软信号 + 来源假设（按权重取 top 12）——
   让向量召回由最丰富的画像驱动。这是一发决定性检索，随后对新出现、还没判断过的
   候选人做一次批量 judge（grounding 校验，约 1 次 LLM 调用）。
2. **回退路径（未充实 / 单趟 / 降级）**：跨路径合并池 → 全局 `rerank_pool`；
   若 rerank 失败则按 per-path 分数排序（`skip_global_rerank`）。

收尾：不足 `top_k` 时用已判断池补齐；若所有结果都已判断，按「命中 must 能力数、再按分」
做精度排序；最后 `_validate_output` 做输出校验（硬条件违规、负分、空结果异常等，记录 `validationIssues`）。

## 9. SearchTool 契约

`contract/tool.py`（runtime_checkable Protocol），后端只需实现两个方法：

- `search_with_intent(SearchToolRequest) -> SearchToolResult`：执行一条意图。
  请求携带 `intent`（布尔树 + 语义条件）、`query_text`、`company_id / hr_id /
  permission`、`top_k`、`exclude_doc_ids`（跨轮去重）等；返回 `items`
  （`ToolCandidate`：doc_id、score、`CandidateItem`、`hard_matches`、
  `match_explanation`、耗时）、`total`、`quality`、`timings_ms`。
- `rerank_pool(RerankToolRequest) -> SearchToolResult`：对跨路径合并池做全局重排。

参考实现 `tools/memory.py::InMemorySearchTool` 完整实现了协议（WhereNode 求值、
语义 token 覆盖率打分、hard_matches 计算），用于离线演示与测试。

## 10. 可观测性

- **event_sink**：`Deps.event_sink(event, state)` 在每个阶段完成后触发
  （事件名即阶段名），降级时触发 `degrade:<阶段>`；sink 抛异常不影响主流程
  （`harness.py::_emit`）。CLI 的八行时间线就是它的一个实现。
- **agentTrace**：`core/trace.py` 组装，挂到 `SearchResponse.debug.agentTrace`，
  含迭代轨迹、合并池大小、重排是否使用、预算与用量、降级原因、工具调用明细。
- **指标**：`core/metrics.py::collect_metrics` 产出 5 组质量指标——
  召回成功（达标率 / 质量分布）、搜索轮次（每轮新增合格曲线）、匹配质量
  （能力覆盖 / 硬条件命中率）、推理力（来源假设转化）、反思力（每轮反思增益），
  外加 LLM 调用数 / 耗时 / 降级等 meta 信息。

## 11. 扩展点

| 想做什么 | 改哪里 |
|---|---|
| 接自己的检索后端 | 实现 `SearchTool` 两个方法，注入 `SearchAgentService(tool=...)` |
| 换 LLM / 换模型 | 实现 `LlmClient.complete_json`，或配 `SEARCH_AGENT_BASE_URL / MODEL` |
| 换执行后端（LangGraph 等） | 实现 `runner.py` 的 `HarnessRunner.run` 协议 |
| 调整难度路由策略 | `core/difficulty.py::difficulty_route / adaptive_budget` |
| 调整预算默认值 | 环境变量（见 README「配置」表）或直接构造 `Budget` |
| 调整阶段行为 | `stages/<阶段>.py` 单文件改动，配套测试在 `tests/` |
