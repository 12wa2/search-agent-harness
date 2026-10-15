# search-agent-harness

**把「一次检索」升级为「会反思、会自我修正、有预算纪律」的检索 Agent。**

> 一个可嵌入的八阶段检索 Agent Harness（以人才搜索为端到端示例场景）。
> 纯 Python，唯一运行时依赖是 pydantic；自带离线演示，不需要任何 API Key。

[![CI](https://github.com/12wa2/search-agent-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/12wa2/search-agent-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![pydantic](https://img.shields.io/badge/pydantic-v2-e92063)
![Tests](https://img.shields.io/badge/tests-160%20offline%20%2B%2022%20ES-brightgreen)
![Lint](https://img.shields.io/badge/lint-ruff%20clean-261230)
![License](https://img.shields.io/badge/license-MIT-green)
![version](https://img.shields.io/badge/version-v0.1.0-blue)

## 这是什么

传统检索是「一问一答」：解析 → 搜索 → 排序 → 返回。这个项目把它变成一个**推理循环**：

```text
understand → plan → execute → judge → evaluate → reflect → revise → finalize
     ↑                                                                  │
     └──────────── 反思循环（有预算、可降级、防重复）─────────────────────┘
```

在循环里，Agent 会：

- 把自然语言查询拆成 岗位画像 + 人才画像 + 难度评估（不是拿裸 query 直搜）
- 规划多条**不同召回意图**的搜索路径（硬条件精准匹配 / 语义拓宽 / 来源假设 …）
- 用工具返回的事实（而非模型想象）对候选人做能力级判断
- 每轮反思「缺什么」，修订路径再搜，直到达标或预算耗尽
- 全程可降级：任何一步失败都不会让整个请求崩掉

## 设计动机（这个项目从哪来）

起点是一个很具体的现象：招聘 JD 写的是抽象能力（比如「高并发系统设计能力」），简历写的是具体项目（订单系统、支付系统、分布式架构）——同一种能力可能来自完全不同的岗位、行业或产品线；反过来，岗位名称不同，经验却是可迁移的。纯关键词检索在这种情况下天然会漏人。

我起初在做这条链路的上游：把自然语言解析成检索 IR（NL2IR）。解析准确率提上去之后，天花板反而更清楚了：**解析得再准，也只是把一句话翻译成一个查询。**整条链路（自然语言 → IR → ES 召回 → LLM 重排 → 返回）是一次性的——首轮条件过严或者搜索方向选错，系统不会根据结果主动修正，也没有任何一个环节回答「召回到的这些人够不够、有没有覆盖岗位的必备能力」。所以缺的不是检索能力，ES 本身已经很强；缺的是**搜索策略的决策能力**——是「搜什么、从哪搜、够不够、要不要换」这一层。

再看真人猎头是怎么干活的：他不会只敲一次关键词，而是先想「什么样的人可能具备这种能力」，然后从公司、行业、岗位、项目几个不同来源分别去找，看完结果再判断要不要换方向。这层决策恰好是 LLM 擅长的，但它又不能失控——招聘搜索对成本和延迟有要求，不能让它无限反思。

所以我的做法不是「把搜索换成 Agent」，而是在确定性的混合检索之上加一层**受控的推理闭环**：用显式状态机编排八个阶段，LLM 只负责岗位理解、路径规划、能力判断和反思；工程层负责状态流转、预算控制、签名防重和终止条件；底层的 IR 编译、ES 召回、重排全部通过 `SearchTool` 协议复用原有能力，不重写执行层。

一句话总结：原来的系统解决的是「一次查询怎么执行」，我解决的是「这次搜索策略对不对」。

### 为什么仓库里没有直接放原来的检索工具

`SearchTool` 这条边界抽象自一个真实的人才检索系统：那边 `SearchAgentService(tool=...)` 背后是内部的混合检索服务（Elasticsearch 8 混合召回 + 大模型重排），Agent 只负责「理解 → 规划 → 执行 → 判断 → 反思 → 修订」这层编排。那套服务依赖公司内网的存储与数据，不属于个人资产，因此不在本仓库内。

也正因如此，「换引擎不改 Agent 代码」这句话需要用**可运行的参考实现**来证明——仓库内置的内存 / SQLite / Elasticsearch 三个后端就是干这个的：它们在同一份 `SearchTool` 协议下可互换，并在同一语料上结果逐位一致（跨后端一致性测试）。

## 30 秒上手

```bash
git clone https://github.com/12wa2/search-agent-harness.git
cd search-agent-harness
pip install -e ".[dev]"

search-agent demo        # 离线演示：脚本化 LLM + 16 个合成候选人，零 API key
search-agent eval        # 离线评测：单趟基线 vs Agent 全循环（确定性、可复现）
search-agent serve --offline   # 可选：HTTP 服务（需 pip install -e ".[api]"）
```

接入真实 LLM（任何 OpenAI 兼容端点：OpenAI / Qwen / DeepSeek / vLLM …）：

```bash
pip install -e ".[openai]"
export SEARCH_AGENT_API_KEY=sk-...                       # Windows: $env:SEARCH_AGENT_API_KEY="sk-..."
export SEARCH_AGENT_BASE_URL=https://api.openai.com/v1   # 可选
search-agent search "招聘推荐算法工程师，要求 5 年以上，有召回排序经验"
```

## 运行效果

`search-agent demo` 的实测输出（本机离线运行、纯 CPU、单进程）：

```text
查询 1: 找有 3 年以上经验、做过高并发系统的后端工程师，最好来自大厂
  [1/8 understand] 岗位=后端工程师  必须能力=['高并发系统设计', '服务端开发']  硬条件=1 条
  [2/8 plan] 生成 2 条搜索路径 → p1: 硬条件精准匹配 | p2: 放宽硬条件的语义拓宽路径
  [3/8 execute] 并行执行 2 条路径，召回 30 人次（p1:rich, p2:rich）
  [4/8 judge] 能力级判断完成，累计合格候选人 12
  [5/8 evaluate] → reflect
  [6/8 reflect] 共性=['普遍命中：服务端开发', '普遍命中：高并发系统设计']  缺口=['高并发系统设计']  新增软信号=2
  [7/8 revise] 画像软信号=6  收敛=False  迭代=1
  [8/8 finalize] 最终 10 人（收敛再搜 14 人次）

  共 10 人，展示前 10 人：
   #  姓名       年限     分数  命中能力                           来源
  ------------------------------------------------------------------------------
   1  赵敏        4   3.01  高并发系统设计、服务端开发         硬条件精准匹配
   2  李娜      6.5   2.75  高并发系统设计、服务端开发         硬条件精准匹配
   3  张伟        8   2.59  高并发系统设计、服务端开发         硬条件精准匹配
   ...
  运行指标: LLM 调用 6 次 · 迭代 1 轮 · 召回完成度 0.65（13/20） · 降级=无
  用时 91 ms
```

（演示数据为 16 个虚构候选人；真实模式下同一循环直接对接你自己的检索后端。）

## 评测：单趟基线 vs 全循环

`search-agent eval` 在合成数据上用脚本化 LLM 做**离线确定性**对比：
「单趟基线」（understand → 单条硬条件路径 → judge → 合并重排）与「Agent 全循环」
（八阶段反思循环）跑同一批查询，输出合格候选人数、召回完成度与成本：

| 指标（3 个查询合计） | 单趟基线 | Agent 全循环 | 变化 |
|---|---|---|---|
| 合格候选人 | 25 人 | 36 人 | +11（+44%） |
| 平均召回完成度 | 0.42 | 0.60 | +0.18 |
| LLM 调用次数 | 6 次 | 16 次 | ×2.7 |
| 累计耗时 | 4.7 ms | 8.7 ms | ×1.9 |

> 数字来自本机离线运行，毫秒级会有抖动；重新生成：`search-agent eval --markdown`。
> 目的不是刷分，而是展示**效果与成本的定量权衡**，以及可回归的评测入口。

## HTTP 服务（可选）

```bash
pip install -e ".[api]"
search-agent serve --offline    # http://127.0.0.1:8000；不传 --offline 时按环境变量走真实 LLM
```

| 端点 | 说明 |
|---|---|
| `POST /search` | `SearchRequest` → `SearchResponse`（camelCase JSON；`details=true` 附带 `agentTrace` / `agentMetrics`） |
| `GET /search/stream?query=…&topK=10` | SSE 事件流：每个阶段一帧，最后 `result` 帧返回完整结果 |
| `GET /healthz` | 健康检查：版本 / 模式 / 后端与 LLM 类型 |

```bash
curl -s http://127.0.0.1:8000/search -H "Content-Type: application/json" \
  -d '{"query": "找有 3 年以上经验的后端工程师", "topK": 5}'
```

`create_app(tool=..., llm_client=...)` 同样接受自定义后端，与本地调用共用同一套 `SearchTool` 协议。

## Docker 全栈（API + Elasticsearch）

一条命令起「HTTP 服务 + Elasticsearch 后端」：

```bash
docker compose up -d --build            # 首次要构建镜像，之后可去掉 --build
curl -s http://127.0.0.1:8000/healthz   # → backend=ElasticsearchSearchTool, mode=offline
curl -s -X POST http://127.0.0.1:8000/search -H "Content-Type: application/json" -d '{"query": "找有 3 年以上经验的后端工程师", "topK": 5}'
docker compose down                     # 停止（加 -v 一并清掉 ES 数据卷）
```

镜像为多阶段构建（build 阶段产出 wheel，runtime 阶段只装 wheel、以非 root 运行、内置
`HEALTHCHECK`）；compose 里 `api` 服务等 ES 健康检查通过后才启动，启动时把 16 条演示语料
灌进 ES（`--recreate-index`，可用 `command:` 覆盖）。想切真实 LLM，取消 compose 里
`SEARCH_AGENT_API_KEY` 的注释即可——容器会自动从 offline 模式换成 OpenAI 兼容端点。

## 架构

```mermaid
flowchart TD
    Q([自然语言查询]) --> U["1 · understand<br/>岗位画像 + 人才画像 + 难度评估"]
    U -->|需澄清| CL["返回澄清问题"]
    U -->|难度路由| P["2 · plan<br/>多条不同召回意图的搜索路径"]
    P --> E["3 · execute<br/>线程池并行调用 SearchTool"]
    E --> J["4 · judge<br/>能力级判断（基于工具硬事实）"]
    J --> V{"5 · evaluate<br/>达标？预算还有？"}
    V -->|继续| R["6 · reflect<br/>共性 / 缺口 / 来源假设"]
    R --> RV["7 · revise<br/>收紧 / 放宽 / 新增路径（签名防重）"]
    RV --> E
    V -->|终止| F["8 · finalize<br/>收敛再搜 + 全局重排"]
    F --> OUT(["结果 + agentTrace + 指标"])
    U -.->|LLM 失败| D1["降级：基线单趟检索"]
    E -.->|全部路径失败| D1
    J -.->|LLM 失败| D2["降级：跳过判断，用工具事实排序"]
```

- 状态机、降级矩阵、预算与合并策略细节：见 `docs/ARCHITECTURE.md`
- 关键设计取舍（ADR 风格）：见 `docs/DECISIONS.md`
- 变更记录：见 `CHANGELOG.md`；贡献指南：见 `CONTRIBUTING.md`

## 项目结构

```text
search-agent-harness/
├── src/search_agent/
│   ├── harness.py           # 外层循环：阶段调度、降级应用、trace 组装
│   ├── service.py           # SearchRequest → SearchResponse 适配层
│   ├── runner.py            # 可替换执行后端（默认 LoopRunner）
│   ├── graph.py             # 可选：LangGraphRunner（同一批阶段 → StateGraph，行为等价）
│   ├── server.py            # 可选：FastAPI HTTP 服务（SSE 阶段事件流）
│   ├── stages/              # 8 个阶段（每个都是纯函数，可独立单测）
│   ├── core/                # 签名 / 合并 / 预算 / 降级 / 难度路由 / 指标 / trace
│   ├── contract/tool.py     # SearchTool 协议 —— 与检索后端的唯一边界
│   ├── tools/memory.py      # 参考实现：内存检索后端（离线演示用）
│   ├── tools/sqlite_fts.py  # 零依赖 SQLite/FTS5 后端（SQL 编译 where 树）
│   ├── tools/text.py        # 共享语义层：分词 / 匹配 / 打分（多后端同源）
│   ├── tools/elasticsearch.py # 真实 ES 8 后端（DSL 编译 + docker compose）
│   ├── llm/                 # LLM 客户端（OpenAI 协议）+ 各阶段 prompt
│   ├── models/              # 意图 IR / 推理工件 / API 模型（pydantic v2）
│   ├── demo.py              # 离线脚本化 LLM + 合成候选人数据
│   ├── eval.py              # 离线评测：单趟基线 vs 全循环（效果 / 成本对比）
│   └── cli.py               # 命令行入口：demo / search / eval / serve
│   ├── tools/embedding.py   # 确定性哈希向量（换真实模型只改这一个函数）
├── tests/                   # 160 离线 + 22 ES 集成
├── examples/                # custom_backend.py（最小接入）· es_end_to_end.py（真实 ES 全链路）· es_hybrid_recall.py（混合召回）
├── docs/                    # ARCHITECTURE.md · DECISIONS.md · demo.tape（演示录制脚本）
├── Dockerfile               # 多阶段镜像：wheel 构建 → 非 root 运行 + HEALTHCHECK
└── docker-compose.yml       # 单节点 ES 8 + API 服务（docker compose up -d --build）
```

### 混合召回通道（BM25 + 向量 + RRF）

真实系统的召回层通常做「关键词 + 向量」双通道再融合，这里把同一套漏斗也做进了 ES 后端：

```bash
python examples/es_hybrid_recall.py        # BM25 / kNN / RRF 三列对比
```

实测输出（同一个查询）：

```text
[BM25 关键词通道 (operator=and)] 0 条
  （无命中）

[kNN 向量通道 (dense_vector + cosine)] 5 条
  张伟     c001  score=0.7024
  许诺     c016  score=0.6833

[RRF 混合召回 (rank_constant=60)] 5 条
  张伟     c001  rrf=0.0164  bm25=-  knn=1
  许诺     c016  rrf=0.0161  bm25=-  knn=2
```

长查询在 AND 语义的关键词通道下 0 命中、向量通道仍能召回——这正是混合召回存在的理由。
向量由 `tools/embedding.py` 的确定性哈希向量生成（不下载模型、CI 里可复现）；换成真实
embedding 模型只需替换 `embed_text` 并重建索引，索引映射 / kNN 检索 / RRF 融合都不用动。
Agent 主循环默认仍走「引擎侧过滤 + 共享语义层打分」，所以三后端逐位一致的测试不受影响。

## 接入你自己的检索后端

Harness 与检索后端之间只有一个协议边界：

```python
class SearchTool(Protocol):
    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult: ...
    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult: ...
```

实现这两个方法（ES / 向量库 / 内部检索服务皆可），组装
`SearchAgentService(tool=..., llm_client=...)` 即可。参考实现见
`src/search_agent/tools/memory.py`，最小示例见 `examples/custom_backend.py`。
此外仓库内置三个后端：内存（`tools/memory.py`）、SQLite/FTS5（`tools/sqlite_fts.py`，仅标准库）与 Elasticsearch 8（`tools/elasticsearch.py`，见下节），在同一语料上结果逐位一致（`tests/test_sqlite_fts.py`、`tests/test_elasticsearch_backend.py`）。

## 接入真实后端（Elasticsearch）

仓库自带可直接运行的 Elasticsearch 8 后端（`tools/elasticsearch.py`），与内存参考后端在同一语料上结果逐位一致：

```bash
docker compose up -d elasticsearch   # 单节点 ES 8.15：关闭安全认证，512m 堆
pip install -e ".[es]"
python examples/es_end_to_end.py     # 建索引 → 跑完整 Agent 循环 → 与内存后端逐位比对
```

`WhereNode` 树会先编译成真实的 ES DSL（`terms` / `range` / `bool.must_not` / `minimum_should_match`），`details=True` 时该 DSL 原样出现在 `es_query` 里；引擎侧过滤后用共享语义层复检与打分。集成测试未检测到 ES 时自动跳过（`SEARCH_AGENT_ES_URL` 可覆盖默认地址）：

```bash
pytest -q -m es
```

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `SEARCH_AGENT_API_KEY` / `OPENAI_API_KEY` | — | 真实模式必需 |
| `SEARCH_AGENT_BASE_URL` / `OPENAI_BASE_URL` | 官方端点 | 任意 OpenAI 兼容端点 |
| `SEARCH_AGENT_MODEL` | `gpt-4o-mini` | 模型名 |
| `SEARCH_AGENT_MAX_ITERATIONS` | `3` | 反思循环最大迭代数 |
| `SEARCH_AGENT_MAX_PATHS_PER_ITERATION` | `4` | 每轮最大搜索路径数 |
| `SEARCH_AGENT_TARGET_QUALIFIED` | `max(top_k*2, 20)` | 合格候选人目标数 |
| `SEARCH_AGENT_TIME_BUDGET_SEC` | `12.0` | 单次请求墙钟预算（秒） |
| `SEARCH_AGENT_LLM_CALL_BUDGET` | `40` | 单次请求 LLM 调用上限 |
| `SEARCH_AGENT_REDIS_URL` / `REDIS_URL` | — | 可选：会话记忆 / 结果分页 |
| `SEARCH_AGENT_ES_URL` | `http://127.0.0.1:9200` | Elasticsearch 后端地址（`tools/elasticsearch.py`） |

## Roadmap

- [x] 八阶段循环 + 预算 + 降级矩阵 + 并行执行
- [x] 离线演示（脚本化 LLM + 内存后端），零 API key 可跑
- [x] 160 个离线单元测试（+22 个 ES 集成用例）；ruff 全绿；GitHub Actions CI（ruff + pytest）
- [x] LangGraphRunner：把同一批阶段编译成 StateGraph，与默认循环行为等价（可选 extra，见 `graph.py`）
- [x] FastAPI 服务（HTTP + SSE 阶段事件流）：`search-agent serve`（`server.py`，可选 extra）
- [x] 评测脚本：Agent 全循环 vs 单趟 baseline 的效果 / 成本对比（`search-agent eval`）
- [x] SQLite/FTS5 后端（`tools/sqlite_fts.py`，标准库）：where 树编译 SQL + 跨后端一致性测试。
- [x] Elasticsearch 真实后端 + `docker compose` 端到端示例（ES DSL 编译，`examples/es_end_to_end.py`）。
- [x] 向量通道：`dense_vector` + kNN，与 BM25 做 RRF 混合召回（`examples/es_hybrid_recall.py`）。
- [x] Docker 全栈：多阶段镜像 + compose（`api` 等待 ES 健康后启动，自动灌入演示语料）。
- [ ] 演示 GIF（`docs/demo.tape` 已就绪，待录制）与完整英文 README（现为 README 内折叠版）

## 诚实声明

- 本仓库代码为独立实现：不含任何公司代码、数据或内部文档；演示数据为 16 个虚构候选人（`c001`–`c016`），其中的公司 / 院校名仅作为行业背景用于演示，与真实个人无关。
- 仓库内置内存 / SQLite / Elasticsearch 三个检索后端（前两个零依赖，ES 需 `docker compose`）；向量库等其它生产组件通过 `SearchTool` 协议对接，不在本仓库内。
- 未在生产环境部署过；上面的耗时数字来自本机离线 demo（纯 CPU、单进程）。

<details>
<summary>English</summary>

An eight-stage reflective retrieval agent harness for talent search:
`understand → plan → execute → judge → evaluate → reflect → revise → finalize`.

Offline runnable without any API key:

```bash
pip install -e ".[dev]"
search-agent demo     # offline showcase: scripted LLM + synthetic candidates
search-agent eval     # single-pass baseline vs the full loop (effect vs cost)
```

See `docs/ARCHITECTURE.md` for the state machine, budget and degradation
design, and `docs/DECISIONS.md` for the ADR-style trade-offs.

Design rationale: the missing piece is not retrieval — Elasticsearch is already
strong — but the search-policy layer that decides what to search, where, whether
it is enough, and when to change direction. This harness adds a budget-governed
reflective loop on top of a deterministic hybrid retrieval backend, behind a
two-method `SearchTool` protocol; the internal production retrieval service it
abstracts from is not part of this repository.

> Honesty note: an independent implementation with no company code or data;
> the demo ships 16 fictional candidates and runs without any API key.

</details>

## License

[MIT](LICENSE)
