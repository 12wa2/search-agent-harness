<div align="center">

# Search Agent Harness

**把人才搜索从“一次查询”升级为“有预算的搜索策略循环”**

明确条件直连 · 复杂需求自适应反思 · 证据驱动判断 · 全链路可观测

[简体中文](README.md) · [English](README.en.md)

[![CI](https://github.com/12wa2/search-agent-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/12wa2/search-agent-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688)
![Pydantic](https://img.shields.io/badge/pydantic-v2-e92063)
![Elasticsearch](https://img.shields.io/badge/Elasticsearch-8-005571)
![Web UI](https://img.shields.io/badge/Web_UI-zero--build-2f6f61)
![Tests](https://img.shields.io/badge/tests-568-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

<img src="docs/console.png" alt="Search Agent 对话式人才搜索界面" width="100%">

</div>

> 一个面向人才搜索场景的自适应 Search Agent：明确条件先由 Hybrid V2 parser 生成检索 IR，再执行受控直连检索；需要能力判断的需求进入轻量 Agent；复杂需求通过多路径规划、候选人证据判断与反思修订，形成预算可控的搜索闭环。项目提供零 API Key 演示，并支持真实 LLM 和 Elasticsearch 8。

[快速开始](#快速开始) · [架构总览](#架构总览) · [执行模型](#执行模型) · [评测摘要](#评测摘要) · [核心设计](#核心设计) · [文档导航](#文档导航)

---

## 为什么做这个项目

这个项目来自一个具体的观察：JD 写的是抽象能力，例如“高并发系统设计”；简历写的是具体项目，例如订单、支付、分布式架构。同一种能力可能来自完全不同的岗位、行业或产品线；反过来，岗位名称不同，经验也可能是可迁移的。关键词检索在这种错位下天然会漏人。

我最初在做 NL2IR，想把“自然语言 → 检索 IR”这一环做准。但做到后面发现一个天花板：**解析得再准，也只是把一句话翻译成一个查询。** 传统链路 `NL → IR → ES 召回 → LLM 重排 → 返回` 是一次性的——首轮条件过严或方向选错，系统不会根据结果主动修正，也没有环节判断“召回的人够不够、是否覆盖岗位必备能力”。

缺的不是检索能力，ES 已经很强；缺的是**搜索策略的决策能力**：搜什么、从哪搜、够不够、要不要换。一句话：**不是检索不行，是“决定怎么搜”这件事一直没人做。**

围绕这层策略决策，项目重点回答三个问题：

- 一条路径不够时，应该放宽、换来源，还是及时停止？
- 模型是在判断简历中的真实证据，还是被关键词相似度误导？
- 反思到底带来多少质量提升，又付出了多少 token 与延迟？

Search Agent Harness 在确定性检索之上增加一层受控的策略循环。LLM 负责理解、规划、判断和反思；状态流转、预算、签名防重、终止条件、取消与降级由工程层控制。底层检索通过 `SearchTool` 协议接入，不需要重写 Agent。

## 项目亮点

- **自适应执行**：结构化查询执行 3 阶段直连；明确需求执行 5 阶段轻量 Agent；复杂需求最多执行 8 阶段反思循环。
- **证据优先**：Judge 使用工具返回的候选人事实；卡片展示命中能力、缺口、硬条件与来源路径。
- **人数优先的硬条件软门控**：硬条件区分“满足 / 违规 / 待核实”，违规与待核实者后置展示但不从名单删除，交付摘要显式暴露复核缺口。
- **受控循环**：显式状态机、迭代/路径/LLM/Token/墙钟预算、签名防重，以及阶段级降级。
- **可观测与会话**：SSE 阶段事件、实际执行阶段、原始 Trace；L1 最近上下文、L2 会话归档、L3 候选人排除。
- **策略效果可核验**：每轮展示触发缺口、策略动作、新增召回 / 判断 / 必备能力全通过人数与 LLM / 耗时成本；最终收敛检索单独统计，累计值与增量值不再混淆。
- **受控并发**：进程内准入控制限制活跃运行与排队数，超限返回 `429` 和 `Retry-After`，健康检查暴露峰值与拒绝计数。
- **可插拔且可复现**：内存、SQLite/FTS5、Elasticsearch、Hybrid V2 共用同一契约；568 项测试与零 Key 演示可直接运行。

## 架构总览

<a href="docs/query-flow.zh.svg"><img src="docs/query-flow.zh.svg" alt="Search Agent Harness Query 主链路总览" width="100%"></a>

点击图片查看原始尺寸 · 查询主链路看全局；状态机、预算、降级和 finalize 见 [详细架构设计](docs/ARCHITECTURE.md)。

一次请求从 Web Console 进入 FastAPI/SSE，先在 `IntentRouter` 过滤非搜索请求，再由 `EntryRouter` 选择直连或 Agent。Agent 侧由显式状态机驱动 3 / 5 / 8 阶段，工程层统一管理预算、签名防重、降级、取消和 Trace；检索通过 `SearchTool` 协议进入 Hybrid V2，最终由 `finalize` 收敛为可解释的候选人名单。

> 直连路径不执行 Agent 编排阶段，但 Hybrid V2 parser 仍可能调用模型；实际执行阶段以 SSE 事件为准。状态机、预算和降级矩阵见 [架构设计](docs/ARCHITECTURE.md)；实现级系统架构见 [architecture.zh.svg](docs/architecture.zh.svg)。

## 执行模型

```text
自然语言输入
     │
     ▼
IntentRouter ─── 问候 / 身份 / 帮助 / 明确无关任务 ───> reply
     │ SEARCH
     ▼
难度判断
     ├── 结构化条件 ──> Hybrid V2 parser -> CandidateSearchIR -> SearchTool
     │                   direct_search -> execute -> finalize
     │
     ├── 清晰需求 ────> understand -> execute -> judge -> evaluate -> finalize
     │
     └── 复杂需求 ────> understand -> plan -> execute -> judge -> evaluate
                        -> reflect -> revise -> finalize

每次执行：预算检查 · 签名防重 · 阶段级降级 · 可取消边界 · 全程 Trace
```

| 路由 | 实际阶段 | 适合什么请求 |
|---|---|---|
| 直连搜索 | `direct_search → execute → finalize` | 技能、岗位、年限、城市等结构化条件明确 |
| 轻量 Agent | `understand → execute → judge → evaluate → finalize` | 需求清楚，但仍需能力判断与覆盖检查 |
| 完整反思 | `understand → plan → execute → judge → evaluate → reflect → revise → finalize` | 开放式偏好、抽象能力、可迁移经验或多路径探索 |

完整的 8 阶段反思序列还分为 `reduced` 与 `standard` 两种预算档：二者阶段序列相同，但 `reduced` 默认最多 1 轮、每轮 2 条路径，`standard` 使用完整反思预算。实际执行阶段以 SSE 事件为准，不能因为导航展示 8 个阶段就视为全部已执行。

直连路径不运行 Agent 编排的理解、规划、判断与反思阶段，因此 **Agent LLM 调用为 0**；强直连信号直接进入 Hybrid V2 parser，弱直连信号最多增加 1 次轻量入口复核模型调用。Hybrid V2 parser 通常仍调用 1 次模型，失败时显式回退到本地规则。完整路由规则见 [意图路由](docs/INTENT_ROUTING.md)。

## 快速开始

### 零 Key 体验

要求 Python 3.12+。

```bash
git clone https://github.com/12wa2/search-agent-harness.git
cd search-agent-harness
python -m venv .venv
```

Windows：

```powershell
.venv\Scripts\activate
pip install -e ".[api]"
search-agent serve --offline
```

macOS / Linux：

```bash
source .venv/bin/activate
pip install -e ".[api]"
search-agent serve --offline
```

打开 <http://127.0.0.1:8000>。快速演示使用脚本化 LLM 和 16 个虚构候选人，不请求外部模型，也不依赖 Elasticsearch。

Windows 也可以直接运行：

```powershell
.\scripts\start-dev.cmd
```

启动器会读取 `.env`；检测到模型 Key 时会自动准备本地网关，但页面默认仍从快速演示启动，可在右上角切换到完整在线。

### 完整在线

完整在线需要任意 OpenAI-compatible 模型端点、Elasticsearch 8，以及可选的 Redis。Redis 未配置时，会话与分页会降级到进程内存储。

```powershell
pip install -e ".[api,openai,es,redis]"
docker compose up -d elasticsearch
Copy-Item .env.example .env
# 在 .env 中填写模型地址、Key、模型名与 Elasticsearch 配置
# gateway 示例里的上游模型名必须替换成账号实际可用的模型，否则会报 Model not found
$env:SEARCH_AGENT_HYBRID_V2_ES_URL = 'http://127.0.0.1:9200'
$env:SEARCH_AGENT_HYBRID_V2_ES_INDEX = 'search-agent-demo'

search-agent hybrid-v2 index --recreate
search-agent hybrid-v2 doctor
.\scripts\start-dev.cmd -StartOnline
```

直接运行 `search-agent ...` 不会自动读取 `.env`；上面的 `$env:` 只导出索引初始化所需的 Elasticsearch 地址和索引名。若 `.env` 还修改了 embedding、模型或其他 Hybrid V2 参数，请先按[部署指南](docs/DEPLOYMENT.md)把对应变量导入当前 shell。`start-dev.cmd -StartOnline` 会自动读取 `.env`。

完整配置、Docker 全栈、Redis 与常见问题见 **[部署指南](docs/DEPLOYMENT.md)**；所有环境变量见 **[配置参考](docs/CONFIGURATION.md)**。

> 安全边界：本仓库是参考服务，不内置用户认证。生产环境必须由可信 API 网关完成鉴权，并注入 `companyId`、`hrId` 和 `permission`；不要信任浏览器直接提交这些字段。建议设置 `SEARCH_AGENT_REQUIRE_PERMISSION=1` 启用 fail-closed 校验。

## 界面

默认界面是对话式 Agent 体验：左侧恢复最近会话，中间展示需求解析、实际执行路径和候选人证据，右侧交付名单。侧栏可收起为带竖排“展开会话”的导航条，点击后恢复完整会话列表。窄屏下侧栏以抽屉形式打开，并在顶栏保留会话入口。顶部还可切换到执行观测，查看实际阶段、检索路径、运行指标和原始 Trace。两种视图共享同一条 SSE 运行，切换不会重新搜索。

https://github.com/user-attachments/assets/058130c9-098d-4746-8535-1457ff141344

[候选人交付截图](docs/console-run.png) · [执行观测截图](docs/console-observe.png)

演示连接真实 LLM + Hybrid V2 + Elasticsearch，完整走通 `understand → plan → execute → judge → evaluate → reflect → revise → finalize`，共 2 轮检索、交付 50 人；模型等待段按 4× 加速。

## 评测摘要

仓库保留两套公开评测口径：

- 本节的真实 LLM A/B：100 个合成候选人、10 条复杂查询、top-5，用于观察反思循环的质量与成本；
- CI 的 Oracle / 消融门禁：120 个合成候选人、Seed 7、top-10，用于验证机制贡献并防止回归。

两套数据用途不同，不互相替代。真实 LLM 实验受模型版本、服务实现和生成随机性影响；指标仅用于分析工程机制与效果成本权衡，不代表生产环境表现。

真实 LLM + Hybrid V2 A/B（100 个 V2 原生字段合成候选人、10 条复杂查询、top-5）：基线与 Agent 共用客户端网关别名 `search-agent-primary`、Hybrid V2 和 Elasticsearch，唯一结构变量是反思循环。历史产物只记录了客户端别名，未记录逐次解析到的上游模型或 fallback 状态，不能用仓库示例网关配置反推实验所用模型。以下为 2026-10-21 的最新一次复核结果。

| 指标 | 单趟基线 | Agent 反思链路 |
|---|---:|---:|
| 平均池召回 | 0.788 | 0.875 |
| 平均 Recall@5 | 0.334 | 0.425 |
| 平均 Precision@5 | 0.660 | 0.820 |
| LLM 调用 | 20 | 65 |
| Tokens | 113,495 | 412,812 |
| p50 延迟 | 25.5 s | 86.7 s |

- 池召回提升的 bootstrap 95% CI 为 `+0.004 ~ +0.170`，不包含 0；Recall@5 提升为 `+0.092`，区间 `-0.001 ~ +0.194` 跨过 0，因此 10 条查询只能说明观察到提升，不能声称统计显著。
- Agent 新增交付候选人 21 个，其中 14 个相关（0.667）；LLM 调用约为基线的 3.25 倍、tokens 约为 3.64 倍，p95 延迟 32.2 → 116.0 s，本次 10 条查询均未触发预算终止。
- 并非每条查询都提升：`stability_capacity` 从 0.500 降到 0.375，`microservice_backend` 从 0.250 降到 0.188；`payment_distributed_tx` 最终 Recall 从 0.062 升到 0.250，但池召回反而从 0.562 降到 0.438。
完整表格、置信区间与逐 Query 失败分析见 **[评测报告](docs/EVALUATION.md)**。

## 核心设计

- **自适应 Harness**：阶段是纯函数，默认使用显式循环；可选 LangGraph runner 复用同一批阶段，不改变行为。
- **预算与降级**：状态机统一管理迭代次数、搜索路径、LLM 调用、Token 和墙钟时间五类资源约束；对已覆盖的阶段级失败提供降级路径或可解释的终止结果。
- **会话分层**：L1 只注入最近结构化上下文，L2 持久化完整会话，L3 记录已看与显式排除的候选人。
- **检索解耦**：只实现下面两个方法，即可替换为自己的搜索系统，不需要改 Agent 阶段代码。

```python
class SearchTool(Protocol):
    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult: ...
    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult: ...
```

最小接入示例见 [`examples/custom_backend.py`](examples/custom_backend.py)。更多细节见 [架构设计](docs/ARCHITECTURE.md) 与 [Hybrid V2](docs/HYBRID_V2.md)。

## 文档导航

| 文档 | 内容 |
|---|---|
| [意图路由](docs/INTENT_ROUTING.md) | 五类意图、3/5/8 路径选择与可观测字段 |
| [架构设计](docs/ARCHITECTURE.md) | 状态机、预算、降级矩阵、合并与 finalize |
| [HTTP API](docs/API.md) | 搜索、SSE、会话、候选人排除、分页与并发准入协议 |
| [部署指南](docs/DEPLOYMENT.md) | 快速演示、本地完整在线、Docker Compose 与排障 |
| [配置参考](docs/CONFIGURATION.md) | 模型、网关、Redis、预算、ES 与 Hybrid V2 变量 |
| [评测报告](docs/EVALUATION.md) | 真实 LLM top-5 A/B、区间与结果边界 |
| [并发验收报告](docs/CONCURRENCY_REPORT.md) | 并发闸门、离线 10/25/50、真实在线冒烟与突发结果 |
| [排序设计草案](docs/TO_PLAN.md) | Agent 综合顺位、未来后端排序元数据与双视图设计 |
| [Hybrid V2](docs/HYBRID_V2.md) | NL2IR、ES mapping、BM25/kNN、embedding 与重排 |
| [LLM Gateway](docs/LLM_GATEWAY.md) | 模型别名、主备降级、鉴权与用量 |
| [案例走查](docs/CASE_STUDY.md) | 一次完整查询的逐阶段演示 |
| [设计决策](docs/DECISIONS.md) | ADR 风格的取舍与替代方案 |
| [技术规划](docs/ROADMAP.md) | 未完成优化项的假设、验证方法与收益边界 |
| [贡献指南](CONTRIBUTING.md) | 开发环境、测试与扩展方式 |
| [变更记录](CHANGELOG.md) | 已实现能力与行为变化 |

## 质量与边界

- 测试集当前共 `568` 项；本地完整环境为 `568 passed`，CI 会在独立作业中执行 ES 集成用例。
- CI 包含 Ruff、pytest、前端语法、Web 资源校验、Demo 冒烟、评测门禁、Elasticsearch 集成与 Docker 运行时依赖冒烟。
- 仓库未部署到生产环境，不包含真实简历、企业数据、内部主机或凭据；演示数据均为虚构。
- 不同模型提供方的质量、成本和延迟差异很大，接入真实数据前应重新评测。

## License

[MIT](LICENSE)
