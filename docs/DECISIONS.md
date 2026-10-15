# 设计决策（ADR 风格）

> 每个决策记录：背景 → 选择 → 取舍。按重要性排序，面试可逐条展开。

## D1. 自研显式循环，不套 Agent 框架

**背景**：Agent 编排有大量现成框架（LangGraph 等），但它们引入运行时依赖与
编排语义的隐式行为，调试和单测成本高。

**选择**：8 个阶段写成纯函数，`Harness.run` 是一个显式 `while` 循环
（步数上限兜底），`phase` 字段驱动流转。

**取舍**：放弃了框架的现成可视化与并行调度；换来完全可测（117 个单测）、
零框架依赖、行为可解释。框架接入点通过 `runner.py` 的 `HarnessRunner` 协议预留，
迁移不需要动阶段代码。

## D2. SearchTool 协议作为唯一后端边界

**背景**：检索后端差异很大（ES 关键词、向量召回、混合、内部服务），
Agent 逻辑不应该和任何一种耦合。

**选择**：定义两个方法的最小协议——`search_with_intent`（执行一条意图）与
`rerank_pool`（跨路径全局重排），Agent 只依赖协议 + IR 类型。

**取舍**：协议需要后端做一次「意图 → 原生查询」的翻译（参考实现见
`tools/memory.py`）；换来 Agent 与后端可以独立演化和独立测试。

## D3. LLM 输出一律结构化 JSON + 宽容解析

**背景**：LLM 输出格式漂移是常态，一个坏字段不该让整次搜索失败。

**选择**：所有 LLM 调用走 strict json_schema（`OpenAILlmClient`）；
解析层对每个条目做 try/except 级容错（alias、字符串归一、跳过坏条目并记日志）。

**取舍**：更多解析代码；换来「单点畸形不拖垮全局」，且降级路径始终可用。

## D4. Intent Signature 去重（防 LLM 重复检索）

**背景**：反思循环里 LLM 很容易换个说法提出同一条召回——重复检索既烧钱又无新信息。

**选择**：把意图规范化（values casefold+排序、and/or 子节点排序、丢弃不影响召回域的
weight/required/softText）后做 sha1 签名；`tried_signatures` 全局记录，
plan / revise 产出的新路径先查签名（`core/signature.py`）。

**取舍**：字符串级的「同义不同词」（如 "Python" vs "python3"）无法识别；
但覆盖了绝大多数重复模式，成本极低。

## D5. 预算内建，而不是超时兜底

**背景**：循环式 Agent 最大的风险是「停不下来」或「烧光额度」。

**选择**：4 维预算（迭代 / 路径 / LLM 调用 / 墙钟）进入循环的每一次决策，
终止原因显式枚举（5 种）；难度路由还会按查询难度自适应收缩预算
（`high → single_pass`、`low → reduced`）。

**取舍**：预算模型本身成为需要维护的概念；换来成本可控与行为可预测。

## D6. 降级矩阵：控制面永不抛异常

**背景**：一次搜索涉及 5+ 次 LLM 调用与多条工具路径，任一点失败若直接冒泡，
用户体验是「整单失败」。

**选择**：把 `(阶段, 失败)` 映射为预定义动作（`core/degrade.py`），
降级后循环继续或收敛；失败信息以 `degradedReason` 透传，可观测、可统计。

**取舍**：需要仔细定义每个降级动作的语义与测试（`tests/test_degradation.py`）；
换来线上永不 500，且降级率本身成为可监控的质量指标。

## D7. Grounding：判断基于工具事实

**背景**：让 LLM 直接「读简历下结论」会产生幻觉，尤其在证据不足时。

**选择**：judge 的输入是工具计算好的 hardMatches 事实 + 简历快照，prompt 明确
「不得编造经历、缺失标注缺失」；最终输出还做校验（硬条件违规检测，`_validate_output`）。

**取舍**：后端需要产出结构化事实（hard_matches），实现成本前移；换来判断可追溯。

## D8. 收敛再搜：finalize 用进化后的画像检索

**背景**：路径循环结束时，人才画像（软信号 / 来源假设）比开始时丰富得多，
但此前的召回都已发生。

**选择**：finalize 时若画像被反思循环充实过，用「硬条件 where + 全部收敛软信号
semantic」做一发决定性检索，再对新候选人做批量 grounding 判断；失败则回退到
跨路径合并 + 全局重排（两条路径确定性兜底）。

**取舍**：多一次后端检索与可能的 1 次 LLM 调用；换来「关键词搜不到的人能被
向量召回捞出来」的最终召回质量。

## D9. execute 并行 + 路径质量分级

**背景**：路径之间互不依赖，串行执行是纯浪费；但后续策略需要知道每条路径的产出质量。

**选择**：线程池并行执行 pending 路径（状态变更留在主线程）；每条路径按
召回/合格数分为 rich / thin / empty（`rich_min` 随目标自适应）。

**取舍**：需要保证后端线程安全；换来延迟近似 max(单路径) 而非 sum。

## D10. pydantic 贯穿数据边界，API 层 camelCase

**背景**：Agent 内部模型、LLM I/O、对外响应三种数据形态，需要明确的边界。

**选择**：全部用 pydantic v2；对外 API 模型用 alias generator 保持 camelCase
（便于 JSON 往返），内部工件用 snake_case。

**取舍**：模型定义量增大；换来校验、序列化、文档一致性自动化。

## D11. 环境变量配置，零配置可跑

**背景**：展示 / 评测 / 部署三种场景对配置的需求不同。

**选择**：所有可调项走 `SEARCH_AGENT_*` 环境变量且有合理默认值；无 API key 时
用脚本化 LLM + 内存后端完整跑通全循环（demo）。

**取舍**：没有配置文件层级；换来「clone 完就能跑」的展示体验。

## D12. 演示数据为合成数据

**背景**：真实候选人数据涉及隐私合规，不能进开源仓库。

**选择**：demo 使用 16 个虚构候选人（`c001`–`c016`），贴合真实简历形态
（技能 / 工作经历 / 教育 / 期望），保证演示效果真实可信。

**取舍**：演示的多样性有限；换来零合规风险，且测试可完全离线、确定性运行。

## D13. HTTP 服务作为可选 extra

**背景**：仓库定位是「核心零依赖、可嵌入的 harness」，但演示与集成需要一个 HTTP 入口。

**选择**：`fastapi` / `uvicorn` 放进 `[api]` extra（`server.py` + `search-agent serve` 子命令），
dev 依赖里带上它们以在 CI 跑接口测试；核心安装仍然只有 pydantic。

**取舍**：多一组可选依赖与接口测试需要维护；换来「HTTP 入口」与「零依赖核心」两不误。

## D14. LangGraphRunner：同一批阶段编译成图，行为等价

**背景**：评审常见问题「为什么不用 LangGraph」；需要证明 runner 抽象不是摆设。

**选择**：`graph.py` 把同一批阶段函数编译成 StateGraph（节点 = 阶段 + 预算守卫，
条件边 = 既有流转规则），`LangGraphRunner` 实现 `HarnessRunner` 协议；`langgraph`
放入可选 extra，默认路径仍是零依赖循环。等价性由 `tests/test_langgraph_runner.py`
保证（同一查询，两条路径结果一致）。

**取舍**：多一条需要同步维护的执行路径（阶段改动时图边几乎不变）；换来「框架切换」

## D15. SQLite/FTS5 后端：where 树编译 SQL（跨后端结果一致）

**背景**：项目核心卖点是「Agent 只依赖 `SearchTool` 协议，换引擎不改 Agent 代码」。只有内存参考实现时这个卖点无法被验证，也没有真实引擎的过滤 / 索引路径可展示。

**选择**：新增 `tools/sqlite_fts.py`（仅标准库）：`WhereNode` 树编译成 SQL（`candidates` + `candidate_terms` 归一化表，可建索引）；`latest_company` / `latest_work_job` 用 `position = 0` 精确对齐 `companies[:1]` 语义；FTS5 虚表为关键词通道提供 BM25 排序；`db_path` 传文件路径即可持久化。

**取舍**：SQL 编译器允许输出「上近似」（未知字段 → 恒真；`not` 子树不可精确编译 → 恒真），过滤后一律用共享语义层（`tools/text.py`）复检并打分——正确性优先于过滤率，代价是极端情况下退化为全表扫描；两个后端在同一语料上的排序与解释由 `tests/test_sqlite_fts.py` 逐位钉住（含 `not` / `or.minimumShouldMatch` 等树形）。

**影响**：`tools/text.py` 成为分词 / 条件匹配 / 打分 / 结果组装的唯一事实来源；任何新后端只需复用这些函数、并保证引擎侧过滤不弱于 Python 复检，就能自动获得一致的排序与解释；Elasticsearch 真实后端（DSL）按同一模式接入。


## D16. Elasticsearch 真实后端：同一套 IR 编译成 ES DSL

**背景**：秋招展示与真实企业场景都需要「真引擎」证据：DSL 生成、索引映射、端到端示例，而不只是内存假后端。

**选择**：新增 `tools/elasticsearch.py`（`.[es]` extra，客户端惰加载）：`WhereNode` 树编译成 ES DSL（`terms` / `range` / `bool.must_not` / `minimum_should_match`，`latest_*` 单独存 keyword 字段）；`docker-compose.yml` 一键起单节点 ES 8.15；`examples/es_end_to_end.py` 演示「建索引 → 完整 Agent 循环 → 与内存后端逐位比对」；集成测试用 `-m es` 门控（未起 ES 自动跳过），CI 加一个 ES service container 作业真跑集成用例。

**取舍**：命中集在引擎侧过滤、Python 侧复检与打分（与 SQLite 后端同一模式），保证「换引擎不改结果」可被测试钉死；语义条件不参与过滤（与内存后端一致），BM25 走独立的 `search_text` 通道，避免近似召回悄悄改变 Agent 的判定与解释。

**影响**：三个后端（内存 / SQLite / ES）共享同一语义层与同一套对比测试，`SearchTool` 协议作为唯一替换点的价值被真实引擎验证；后续向量检索或多路召回可按同一模式接入。

## D17. 容器化：多阶段构建 + compose 全栈

**背景**：README 的「一条命令跑起来」如果止步于 `pip install`，使用者仍要自己装 ES、配地址、灌数据；「能被部署」是 harness 从库变成系统的一道坎，也是最容易在面试里被追问的部分。

**选择**：`Dockerfile` 两阶段——build 阶段 `pip wheel ".[api,es]"` 产出全部 wheel，runtime 阶段只装 wheel（镜像里没有编译工具链）、非 root 运行、内置 `HEALTHCHECK`；compose 增加 `api` 服务，`depends_on` 带 `condition: service_healthy`，启动即 `--recreate-index` 重灌演示语料；后端选择收敛到 `tools/factory.py`，CLI `--backend` 与容器入口共用一条路径。

**取舍**：镜像默认「脚本化 LLM + 真 ES 后端」，零 API key 也能复现；切真实 LLM 只需注入 `SEARCH_AGENT_API_KEY`（`serve` 检测不到 key 时自动回落 offline）。`--recreate-index` 对单实例演示是自愈，多副本部署应改成一次性 init job——这点写进了 README 的说明。

**影响**：`docker compose up -d --build` 之后 `/healthz` 报 `ElasticsearchSearchTool`，`POST /search` 的前三名与本地内存后端逐位一致，说明「换引擎不改结果」在容器里同样成立；后续加向量后端只需在 factory 里多一个分支。

## D18. 向量通道：dense_vector + kNN，与 BM25 做 RRF 融合

**背景**：三个后端逐位一致证明了「换引擎不改 Agent」，但召回手段只有关键词一条——真实的人才检索里，长查询在 AND 语义下 0 命中是常态，向量召回是刚需，也是面试必问。

**选择**：新增 `tools/embedding.py`（blake2b 分桶的词袋向量 + 次线性 TF + L2 归一化，256 维，零依赖）；ES 映射加 `dense_vector`（cosine，`index: true`，旧索引启动时补 `put_mapping`）；新增 `search_semantic`（kNN，可带 `where` filter）与 `hybrid_search`（BM25 + kNN 各取 `branch_k`，按 RRF `1/(60+rank)` 融合，逐行保留两支路的 rank / score）。

**取舍**：不引入 embedding 模型依赖——不下载权重、CI 可跑、零 API key，代价是「语义」只到词袋级别，因此代码与文档都明确写成「确定性向量，替换 `embed_text` 即可接真实模型」，不夸大效果。融合放在召回层（RRF）而不是打分层，`search_with_intent` 一行未动，三后端逐位一致的测试继续成立。

**影响**：仓库现在覆盖「关键词 → 向量 → 混合召回」这条链路，`examples/es_hybrid_recall.py` 可直接演示「关键词 0 命中、向量召回 5 人、RRF 给出融合排序」；后续接 pgvector / Milvus 也只需再实现一个后端并复用 `embed_text`。

