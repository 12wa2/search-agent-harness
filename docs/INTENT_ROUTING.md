# 意图路由与执行路径

> 本文说明一个自然语言请求在进入搜索循环之前如何被分类，以及为什么会走 3、5 或 8 个阶段。规则以 `src/search_agent/intent_router.py` 和 `src/search_agent/core/difficulty.py` 为准。

## 1. 总览

```text
自然语言请求
   │
   ├─ POST /search ─────────────────────────────> SearchAgentService
   │
   └─ GET / POST /search/stream ──> IntentRouter
                                      │
                                      ├─ 问候 / 身份 / 帮助 / 明确无关任务 -> reply
                                      │
                                      └─ SEARCH
                                           ├─ 结构化直连门槛 -> 3 阶段
                                           └─ Harness -> understand
                                                          ├─ 低难度 -> 5 阶段
                                                          ├─ 中等难度 -> 精简反思
                                                          └─ 高难度 -> 完整反思
```

`POST /search` 是纯搜索 API，不经过面向对话的能力路由。需要问候、身份、帮助、范围外拦截和 SSE 阶段流时，使用 `/search/stream` 或 Web 控制台。

## 2. 零 LLM 的请求路由

`IntentRouter` 是无状态规则层，不调用 LLM，也不访问 `SearchTool`。它只负责能力边界与成本保护，不是通用聊天机器人。

| 意图 | 典型输入 | 处理方式 |
|---|---|---|
| `GREETING` | “你好”“hello” | 返回能力介绍与招聘需求示例 |
| `IDENTITY` | “你是谁”“介绍一下你自己” | 返回产品定位与输入建议 |
| `HELP` | “你能做什么”“怎么搜索候选人” | 返回能力边界与使用方法 |
| `OUT_OF_SCOPE` | “帮我写一首诗”“明天天气怎么样” | 说明只处理招聘搜索，不伪造结果 |
| `SEARCH` | 招聘需求，或无法确定的输入 | 进入直连门槛或自适应 Harness |

### 规则顺序

1. 空输入；
2. 完整匹配问候语；
3. 完整匹配身份询问；
4. 完整匹配帮助询问；
5. 明确的写诗、翻译、天气、通用知识等范围外任务；
6. 其余输入默认放行到 `SEARCH`。

路由刻意保持保守：只有**明确**的无关任务会被拒绝。模糊输入默认进入搜索，由 Harness 继续澄清，避免“帮我找一下手机”或“招聘音乐制作人”因为缺少固定职位词而被误伤。

“推荐”是弱信号：`推荐一本书` 属于范围外，`推荐几位候选人`、`推荐算法工程师` 仍然进入招聘搜索。

### 非搜索回复

非搜索请求只产生一帧终止 `reply`，没有 `stage`、`result`、`metrics` 或 `trace`，因此不会消耗模型与检索预算。回复仍会以 `status=reply` 写入会话归档，便于左侧“最近会话”恢复。

## 3. 搜索路径选择

### 3.1 直连搜索：3 阶段

`SearchAgentService` 在构造 Harness 前先做保守直连判断。只有查询同时具备：

- 明确的招聘搜索动作；
- 技能、岗位、年限或城市等结构化字段；
- 不含“最好 / 优先 / 可迁移 / 重点考察 / 系统设计”等开放信号；

才会走：

```text
Hybrid V2 parser -> CandidateSearchIR -> SearchTool
direct_search -> execute -> finalize
```

完整口径是 **Agent LLM = 0，Hybrid V2 parser LLM ≥ 1**。parser 通常调用一次模型；失败时重试一次，仍失败则回退到本地规则，并记录 `degradedReason=hybrid_v2_parse_failed`。工具侧的 reranker 成本单独计算，不并入 Agent 或 parser 计数。

| 输入 | 走向 | 原因 |
|---|---|---|
| 找 5 年以上、做过高并发的上海后端工程师 | 直连 | 搜索动作和结构化字段明确 |
| 帮我找会 Python 的 | 直连 | 搜索动作与具体技能明确 |
| 找可迁移到基础架构的后端，最好有大厂背景 | 反思 Agent | “可迁移”“最好”引入开放判断 |
| 你是谁 | 意图回复 | 身份询问，不启动搜索 |

### 3.2 Harness：5 或 8 阶段

通过直连门槛后，`understand` 会生成岗位画像、人才画像和难度评估。路由规则如下：

| 难度 | 内部路由 | 实际阶段 | 预算特征 |
|---|---|---|---|
| 低 | `single_pass` | `understand → execute → judge → evaluate → finalize` | 单路径、无反思 |
| 中 | `reduced` | `understand → plan → execute → judge → evaluate → reflect → revise → finalize` | 最多 1 轮反思、每轮最多 2 条路径 |
| 高 | `standard` | 同完整 8 阶段 | 使用默认迭代与路径预算 |

低难度仍会执行 `understand / judge / evaluate`，因此不是“零 LLM 搜索”；只是跳过了 `plan / reflect / revise`。

## 4. 可观测字段

SSE 搜索事件会暴露：

- `requestIntent`：`IntentRouter` 的最终意图；
- `intentRule`：命中的规则；
- `difficultyRoute`：`direct_search`、`single_pass`、`reduced` 或 `standard`；
- `plannedStages`：本次计划执行的阶段；
- `executedStages` / 前端“实际阶段”：以真实 SSE 事件为准，不把未执行阶段标成完成。

## 5. 相关文档

- [架构设计](ARCHITECTURE.md)：状态机、预算、降级、合并与 finalize；
- [HTTP API](API.md)：`/search`、`/search/stream`、SSE 与分页协议；
- [评测](EVALUATION.md)：不同链路的成本与质量边界。
