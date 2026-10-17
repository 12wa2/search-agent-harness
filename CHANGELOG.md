# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- **网关一键启动**（`docker-compose.yml`、`scripts/start-dev.ps1`）：Compose 新增
  `gateway` 服务，与 Elasticsearch、API 一起后台启动并配置 `restart: unless-stopped`；
  Compose 使用 `examples/gateway.docker.json`，本地 CLI 继续使用 `gateway.local.json`。
  新增 Windows 本地脚本和 `.cmd` 包装器，可后台启动网关和 API，日志写入忽略的 `logs/`。

- **真实模型延迟控制**（`llm/client.py`、`gateway/`）：新增
  `SEARCH_AGENT_DISABLE_THINKING` 与 `SEARCH_AGENT_LLM_TIMEOUT_SEC`，用于关闭
  Qwen 等思考型模型的隐藏推理并调整单次调用超时；网关的每个 upstream target
  支持 `extra_body`，可把 provider 专属参数限制在对应模型，不影响 fallback。

- **可选 LLM 网关**（`src/search_agent/gateway/`）：新增 OpenAI-compatible
  `search-agent gateway` 服务，支持模型别名、主备 provider 降级、Bearer 鉴权、
  请求 ID、超时控制和按 alias/model/tenant 聚合的 token 使用统计；接入说明见
  `docs/LLM_GATEWAY.md`。

- **独立 Hybrid V2 检索引擎**（`hybrid_v2/`、`integrations/hybrid_v2.py`）：
  将公开可迁移部分完整放入仓库——IR 工作年限/空窗/司龄归一化、硬条件 DSL、本地
  O*NET 别名、BM25 + 多字段 kNN、`_source.payload` 兼容、可解释 hardMatches 和有界
  重排。ES 地址、认证、索引字段、embedding、标签/薪资目录全部环境变量注入；无配置时
  仍默认 `memory`，配置 ES 后默认 `hybrid_v2`。新增假 ES 服务、payload 转换、硬匹配和
  默认选择测试，接入文档见 `docs/HYBRID_V2.md`。
- **Hybrid V2 索引生命周期**（`hybrid_v2/indexing.py`、`cli.py`）：新增与查询编译器同步的
  ES 8 mapping（索引型 `dense_vector`、CJK 分析、keyword 子字段、work_stats、flat/nested 向量布局）、
  demo/自定义 JSONL 转换与批量导入、建索引/刷新/计数、`search-agent hybrid-v2 init-index|index|doctor`
  运维入口，以及 `examples/hybrid_v2_end_to_end.py` 真实 ES 全链路示例。默认向量模式改为
  `flat` 以保证标准 ES 8 兼容，`nested` 保留为显式高级模式；新增 mapping 完整性、两种向量布局、
  批量导入、维度不一致和真实 ES 回环测试。已用 Elasticsearch 8.15.0 验证 CLI 建库、`doctor`、
  FastAPI 检索和端到端回环，并修正 `track_total_hits` 参数冲突。
- **能力感知请求路由**（`intent_router.py`、`server.py`）：新增确定性 `IntentRouter`，在 Harness 之前
  区分 `GREETING` / `HELP` / `OUT_OF_SCOPE` / `SEARCH`。问候、帮助和明确范围外请求通过单个
  `reply` SSE 帧返回，不启动 Agent、不调用 LLM 或 `SearchTool`；招聘需求和模糊输入默认进入
  八阶段搜索。补充推荐对象、候选人/岗位词、特殊岗位和非招聘搜索动作（如天气查询）边界，
  并新增 GET/POST 路由集成测试及零 LLM / 零检索调用断言。
- **Web 控制台**（`src/search_agent/web/`、`server.py`）：`GET /` 直接托管零构建单页——
  左侧提交招聘需求，中间通过 SSE 实时呈现八阶段时间线与逐阶段细节（可展开原始事件），
  右侧展示交付候选人、命中能力、来源路径与匹配证据，同时显示 LLM 调用 / tokens / 迭代 /
  耗时 / 降级；新增 `POST /search/stream`（查询不进 URL / 访问日志），保留 GET 变体；
  截图 `docs/console.png`；新增 2 个服务端用例（静态页可加载、POST 流与 GET 一致）。
- **指标分层：池召回 vs 交付召回**（`oracle.py` / `eval.py`）：Oracle 报告与消融表都拆成
  「探索层（池召回：路径召回池 ∪ 交付列表里看到过的合格人）」与「交付层（top-k 召回）」两行——
  全循环池召回 0.70 → 0.76（多路径买探索宽度），交付 top-10 0.32 → 0.30（粗粒度词法打分排不上来，
  `--top-k 30` 时交付 0.62），把「没找到」与「没排上」分开归因；消融门禁基线同步覆盖 `pool_recall`，
  并修正报告中「仅语义可见接近 0」的旧注释（实测 0.36，粗粒度 bigram 会漏进一部分共享语素的人）。
  新增池召回断言（`tests/test_eval.py`、`tests/test_ablation.py`）。
- **案例研究**（`docs/CASE_STUDY.md`）：一条 query 的端到端走查——逐阶段贴真实中间产物
  （画像 / 路径与签名 / 判决 / 反思 JSON / 收敛再搜与最终排名），并给出「这个案例没说明什么」
  的诚实解读、60 秒面试口播稿与追问预案；`docs/demo.tape` 更新为演示录制脚本（demo + 消融表）。
- **评测口径修正**（`eval.py`）：口径① 的「合格候选人」实为**进入 judge 的池子规模**，
  已改标为「判定候选（judged）」并在表下注明它不是质量口径（质量看 oracle 与消融表）；
  修复 `--markdown` 合计表里 Agent 行丢失 tokens 单元格的 bug。
- **消融实验与评测门禁**（`core/ablation.py`、`eval.py`）：`search-agent eval --ablate` 在同一语料 /
  同一 seed 上逐行只翻转一个结构开关（去收敛再搜 / 去签名防重 / 只跑一轮 / 只规划单路径 / 单趟基线），
  输出 oracle 召回、精确率、关键词可见 vs 仅语义可见、独有发现与 LLM/token 成本对照表；
  消融默认把目标合格数抬到语料规模以强制循环迭代（否则首轮达标会让开关不可区分）。
  `--save-baseline` 落盘 `docs/eval_baseline.json`，`--baseline` 比对（比例指标容差 0.02、成本容差 10%、
  降级即失败），CI 新增 `eval-gate` 作业；新增 22 个用例（`tests/test_ablation.py`，含跨轮确定性校验）。
- **策略记忆**（`core/experience.py`）：每次运行后把合格候选人按来源路径归因
  （`SearchPath.origin`：`plan` / `default` / `fallback` / `loosen` / `tighten` / `add_path`，收敛再搜记 `converged`），
  按“must 能力集哈希”聚合的查询族累计 `runs / qualified / tokens`；
  plan / reflect 的 prompt 新增可选 `strategyPriors`（无观测时整键省略）。
  存储同 `conversation_memory` 一样可降级：配 `SEARCH_AGENT_REDIS_URL` / `REDIS_URL` 走 Redis 字段计数器（30 天 TTL），
  否则进程内；读写异常只记日志，不影响本次搜索。CLI 新增 `search-agent demo --repeat N`（跑 N 次同一查询并打印策略记忆快照）；
  新增 24 个用例（`tests/test_experience.py`）与一个 CLI smoke。
- **Token / 成本记账**（`llm/client.py`、`core/budget.py`）：`LlmUsage` 精确读取 provider 的
  `prompt_tokens` / `completion_tokens`（网关不返回 usage 时退回字符估算并标记 `tokensEstimated`）；
  `MeteredLlmClient` 把每次调用记进预算，`Budget.max_tokens`（`SEARCH_AGENT_TOKEN_BUDGET`，0 = 只记账）
  命中即终止（`token_budget`）；评测报告与指标新增 token 列（`agentMetrics.meta.totalTokens`）。
- **LLM 客户端韧性**（`llm/client.py`）：瞬时错误（超时 / 连接中断 / 429 / 5xx）按
  `SEARCH_AGENT_LLM_RETRIES` 指数退避 + 抖动重试；非法 JSON 先剥离代码块与前后文，
  再走 `SEARCH_AGENT_LLM_JSON_REPAIRS` 修复轮；重试次数进入指标（`llmRetries`）；
  新增 `tests/test_llm_client.py`（注入假 `openai` 模块，零网络覆盖重试 / 退避 / 修复）。
- **Oracle 基准与去自证评测**（`corpus.py` / `oracle.py`）：种子化合成语料（每条简历按能力逐个掷骰，
  决定「技能关键词可见」还是「只用项目语言表达」），真值来自生成器而非 Agent 自评；
  `search-agent eval --generated N --seed S` / `--oracle DIR`，`search-agent corpus --out DIR` 落盘语料；
  报告给出交付列表召回 / 精确率、关键词可见 vs 仅语义可见召回、以及「仅全循环发现」增量指标（新增 15 个测试）。
- **宽召回池语义**：`SearchToolRequest.recall_pool_size` 落地到三个后端——每路径召回
  `max(top_k*3, 30)`（`SEARCH_AGENT_RECALL_POOL_SIZE` 可调），judge 全量池、最终只交付 top_k；
  单路径构造器（计划降级 / single_pass 路由 / 评测基线）补上人才画像的关键词与软信号，
  避免大语料上退化成按 docId 抽签。
- **修复**：无硬约束查询的单趟基线路径此前会构建失败（`loose_fallback`），评测里会直接抛错。
- **SQLite/FTS5 后端**（`tools/sqlite_fts.py`，仅标准库）：`WhereNode` 树编译为 SQL（归一化表 + FTS5 虚表），
  过滤后由共享语义层复检与打分；与内存后端在同一语料上结果逐位一致（新增 27 个测试）。
- **共享语义层**（`tools/text.py`）：分词 / 条件匹配 / 软打分 / 结果组装集中一处，所有后端复用同一公式（行为不变重构）。
- **Elasticsearch 后端**（`tools/elasticsearch.py`，`.[es]` extra）：`WhereNode` 树编译成 ES DSL，`details=True` 时原样返回；
  `docker-compose.yml` 单节点 ES 8.15；`examples/es_end_to_end.py` 端到端演示；集成测试 `-m es` 门控，CI 新增 ES 作业。
- **后端工厂与 CLI 选择**（`tools/factory.py`）：`search-agent search|serve --backend memory|sqlite|es`，ES 索引名可走 `SEARCH_AGENT_ES_INDEX`。
- **Docker 全栈**：多阶段 `Dockerfile`（wheel 构建 → 非 root 运行 + `HEALTHCHECK`）+ compose `api` 服务
  （等待 ES 健康后启动并自动灌入演示语料）：`docker compose up -d --build` 一条命令起「HTTP 服务 + ES」。
- **向量通道与混合召回**（`tools/embedding.py`、`tools/elasticsearch.py`）：确定性哈希向量 + ES `dense_vector` kNN，
  与 BM25 按 RRF 融合（`search_semantic` / `hybrid_search`，示例 `examples/es_hybrid_recall.py`）；
  新增 5 个离线向量测试与 5 个 ES 集成用例。

### Fixed

- **SQLite 后端并发安全**（`tools/sqlite_fts.py`）：execute 阶段会并行调用多个搜索路径，
  SQLite 共享连接现在通过锁串行化查询与关闭操作，避免 CI/多线程环境下偶发返回空结果；
  新增 32 路并发搜索回归测试。

## [0.1.0] - 2026-10-15

首个公开版本。

### Added

- **八阶段推理循环**（`harness.py`）：understand → plan → execute → judge → evaluate →
  reflect → revise → finalize，`phase` 字段驱动，60 步兜底。
- **预算治理**（`core/budget.py`）：迭代 / 路径 / LLM 调用 / 墙钟 4 维预算，5 种显式终止原因。
- **降级矩阵**（`core/degrade.py`）：任一阶段失败映射到预定义降级动作，`degradedReason` 透传。
- **Intent Signature 防重**（`core/signature.py`）：归一化后端 sha1 签名，同一意图不重复执行。
- **Grounding 判断**：judge 基于工具返回的硬事实（hardMatches），prompt 禁止编造经历。
- **收敛再搜**：finalize 用反思进化后的画像做最终检索 + 全局重排。
- **并行 execute + 路径质量分级**：rich / thin / empty 驱动后续策略。
- **可观测性**：`event_sink` 时间线、`agentTrace`、5 组质量指标随响应返回。
- **可选 Redis 持久化**：多轮对话记忆 + 结果分页（不配置也能完全离线跑）。
- **离线演示**：脚本化 LLM + 16 个合成候选人，`search-agent demo` 零 API key。
- **离线评测**：`search-agent eval`，单趟基线 vs 全循环（合格候选人 +44%，LLM 调用 ×2.7）。
- **HTTP 服务**（`.[api]` extra）：`search-agent serve`，`POST /search`、SSE `GET /search/stream`、
  `GET /healthz`。
- **LangGraphRunner**（`.[langgraph]` extra）：同一批阶段编译成 StateGraph，与默认循环行为等价
  （有对比测试）。
- **CLI**：`demo` / `search` / `eval` / `serve` 四个子命令。
- **工程化**：ruff + pytest GitHub Actions CI、117 个单元测试、MIT License、
  ADR 风格设计文档（`docs/DECISIONS.md`）。

[0.1.0]: https://github.com/12wa2/search-agent-harness/releases/tag/v0.1.0
