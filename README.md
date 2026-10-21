<div align="center">

# Search Agent Harness

**把人才搜索从“一次查询”升级为“有预算的搜索策略循环”**

明确条件直连 · 复杂需求自适应反思 · 证据驱动判断 · 全链路可观测

[简体中文](README.md) · [English](README.en.md)

[![CI](https://github.com/12wa2/search-agent-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/12wa2/search-agent-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![Pydantic](https://img.shields.io/badge/pydantic-v2-e92063)
![Tests](https://img.shields.io/badge/tests-484-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

<img src="docs/console.png" alt="Search Agent 对话式人才搜索界面" width="100%">

</div>

> 一个面向人才搜索场景的自适应 Search Agent：明确条件先由 Hybrid V2 parser 生成检索 IR，再执行受控直连检索；需要能力判断的需求进入轻量 Agent；复杂需求通过多路径规划、候选人证据判断与反思修订，形成预算可控的搜索闭环。项目提供零 API Key 演示，并支持真实 LLM 和 Elasticsearch 8。

[快速开始](#快速开始) · [执行模型](#执行模型) · [评测摘要](#评测摘要) · [核心设计](#核心设计) · [文档导航](#文档导航)

---

## 为什么做这个项目

Elasticsearch 已经能完成召回与排序。真正困难的是检索之前的策略决策：

- 自然语言招聘需求应如何变成可执行检索条件？
- 一条路径不够时，应该放宽、换来源，还是及时停止？
- 模型是在判断简历中的真实证据，还是被关键词相似度误导？
- 多轮对话里哪些条件应继承，哪些候选人已经被明确排除？
- 反思到底带来多少质量提升，又付出了多少 token 与延迟？

Search Agent Harness 在确定性检索之上增加一层受控的策略循环。LLM 负责理解、规划、判断和反思；状态流转、预算、签名防重、终止条件、取消与降级由工程层控制。底层检索通过 `SearchTool` 协议接入，不需要重写 Agent。

## 项目亮点

- **自适应执行**：结构化查询执行 3 阶段直连；明确需求执行 5 阶段轻量 Agent；复杂需求最多执行 8 阶段反思循环。
- **证据优先**：Judge 使用工具返回的候选人事实；卡片展示命中能力、缺口、硬条件与来源路径。
- **受控循环**：显式状态机、迭代/路径/LLM/Token/墙钟预算、签名防重，以及阶段级降级。
- **可观测与会话**：SSE 阶段事件、实际执行阶段、原始 Trace；L1 最近上下文、L2 会话归档、L3 候选人排除。
- **可插拔且可复现**：内存、SQLite/FTS5、Elasticsearch、Hybrid V2 共用同一契约；484 项测试与零 Key 演示可直接运行。

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

直连路径不运行 Agent 编排的理解、规划、判断与反思阶段，因此 **Agent LLM 调用为 0**；Hybrid V2 parser 通常仍调用 1 次模型，失败时显式回退到本地规则。完整路由规则见 [意图路由](docs/INTENT_ROUTING.md)。

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

search-agent hybrid-v2 index --recreate
search-agent hybrid-v2 doctor
.\scripts\start-dev.cmd -StartOnline
```

完整配置、Docker 全栈、Redis 与常见问题见 **[部署指南](docs/DEPLOYMENT.md)**；所有环境变量见 **[配置参考](docs/CONFIGURATION.md)**。

## 界面

默认界面是对话式 Agent 体验：左侧恢复最近会话，中间展示需求解析、实际执行路径和候选人证据，右侧交付名单。顶部可切换到执行观测，查看实际阶段、检索路径、运行指标和原始 Trace。两种视图共享同一条 SSE 运行，切换不会重新搜索。

[候选人交付截图](docs/console-run.png) · [执行观测截图](docs/console-observe.png)

## 评测摘要

公开评测基于合成候选人、固定数据集与明确实验配置，不使用企业真实简历数据。离线脚本化实验可确定性复现；真实 LLM 实验受模型版本、服务实现和生成随机性影响，不能保证每次运行得到完全相同的结果。所有指标仅用于分析工程机制与效果成本权衡，不代表生产环境表现。

| 基准 | 单趟基线 | Agent 全循环 | 代价 |
|---|---:|---:|---:|
| 离线消融 · 池召回 | 0.70 | 0.76 | LLM 调用 12 → 29；tokens 49.7k → 109.5k |
| 离线消融 · 交付 top-10 召回 | 0.32 | **0.41** | 同上 |
| 真实 LLM A/B · 池召回 | 0.735 | **0.937** | 调用 20 → 59；tokens 148,428 → 446,704 |
| 真实 LLM A/B · recall@5 | 0.392 | **0.473** | p50 延迟 50.7s → 139.9s |
| 真实 LLM A/B · precision@5 | 0.740 | **0.840** | 同上 |

关键结论：

- 反思循环明显扩大了探索层；交付 top-10 的 `0.32 → 0.41` 来自强制迭代消融，默认目标下结果更保守。
- 真实模型 A/B 中，池召回提升区间不包含 0；recall@5 的 bootstrap 区间跨过 0，10 条查询不足以证明稳定的平均交付提升。
- 成本约为 3 倍，且并非每条查询都提升；下一阶段应优先修 judge / rerank，而不是无限增加路径。

完整表格、置信区间、逐查询失败分析与复现命令见 **[评测报告](docs/EVALUATION.md)**。

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
| [HTTP API](docs/API.md) | 搜索、SSE、会话、候选人排除与分页协议 |
| [部署指南](docs/DEPLOYMENT.md) | 快速演示、本地完整在线、Docker Compose 与排障 |
| [配置参考](docs/CONFIGURATION.md) | 模型、网关、Redis、预算、ES 与 Hybrid V2 变量 |
| [评测报告](docs/EVALUATION.md) | 离线消融、真实 LLM A/B、区间与结果边界 |
| [Hybrid V2](docs/HYBRID_V2.md) | NL2IR、ES mapping、BM25/kNN、embedding 与重排 |
| [LLM Gateway](docs/LLM_GATEWAY.md) | 模型别名、主备降级、鉴权与用量 |
| [案例走查](docs/CASE_STUDY.md) | 一次完整查询的逐阶段演示 |
| [设计决策](docs/DECISIONS.md) | ADR 风格的取舍与替代方案 |
| [贡献指南](CONTRIBUTING.md) | 开发环境、测试与扩展方式 |
| [变更记录](CHANGELOG.md) | 已实现能力与行为变化 |

## 质量与边界

- 测试集共 `484` 项；本地无 Elasticsearch 时为 `460 passed, 24 skipped`，CI 会在独立作业中执行 ES 集成用例。
- CI 包含 Ruff、pytest、Web 资源校验、离线 Demo 冒烟、消融门禁与 Elasticsearch 集成测试。
- 仓库未部署到生产环境，不包含真实简历、企业数据、内部主机或凭据；演示数据均为虚构。
- 不同模型提供方的质量、成本和延迟差异很大，接入真实数据前应重新评测。

## Roadmap

- [ ] 接入真实 embedding，验证语义召回收益
- [ ] 提升 judge / rerank 的证据粒度，修复“探索有货、排序丢货”
- [ ] 按查询族分配收敛再搜预算，降低真实模型链路约 3 倍的成本
- [ ] 录制 README 演示 GIF；`docs/demo.tape` 已就绪

## License

[MIT](LICENSE)
