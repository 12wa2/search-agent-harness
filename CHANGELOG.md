# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

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
