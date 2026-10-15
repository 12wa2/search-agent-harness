# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

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
