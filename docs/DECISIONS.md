# 设计决策（ADR 风格）

> 每个决策记录：背景 → 选择 → 取舍。按重要性排序，面试可逐条展开。

## D1. 自研显式循环，不套 Agent 框架

**背景**：Agent 编排有大量现成框架（LangGraph 等），但它们引入运行时依赖与
编排语义的隐式行为，调试和单测成本高。

**选择**：8 个阶段写成纯函数，`Harness.run` 是一个显式 `while` 循环
（步数上限兜底），`phase` 字段驱动流转。

**取舍**：放弃了框架的现成可视化与并行调度；换来阶段行为可测试、默认执行路径不依赖 Agent 编排框架、状态流转可解释。相关行为由单元测试与集成测试持续验证；LangGraph 作为可选 Runner，通过 `runner.py` 的 `HarnessRunner` 协议接入，不改变默认执行路径。

## D2. SearchTool 协议作为唯一后端边界

**背景**：检索后端差异很大（ES 关键词、向量召回、混合、自建服务），
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

**选择**：5 类资源预算（迭代 / 路径 / LLM 调用 / Token / 墙钟）进入循环的每一次决策，
终止原因显式枚举（6 种，含 `token_budget`）；`target_qualified` 是合格候选人目标阈值，
不是资源预算，单独驱动 `target_reached` 终止。难度路由还会按查询难度自适应收缩预算
（`low → single_pass`、`mid → reduced`；`high` 保留完整反思循环）。

**取舍**：预算模型本身成为需要维护的概念；换来成本可控与行为可预测。

## D5.1 明确结构化查询在 Harness 之前直连 SearchTool

**背景**：像“帮我找会 Python 的”这类查询字段明确，若仍走 Agent 的岗位理解、规划、judge 和
反思阶段，会增加无意义的延迟和成本。但把所有搜索都改成规则解析，又会失去对模糊、偏好重和
可迁移能力需求的推理能力。

**选择**：在 Service 层增加保守的确定性直连路由。只有技能、岗位、年限、城市等字段足够明确，
且不含“最好 / 优先 / 可迁移 / 重点考察 / 系统设计”等开放信号时，才进入 Hybrid V2 简单搜索：
先由 `hybrid_v2.parser.parse_query_to_ir()` 通过运行时注入的 `LlmClient` 生成
`CandidateSearchIR`，再调用一次 `SearchTool` 完成 normalize、compile、召回与重排。SSE 固定为
`direct_search → execute → finalize`。Agent 编排 LLM 调用为 0，但 V2 parser LLM 通常为 1 次，
最多重试 2 次；Token、耗时与重试单独记账。parser 不可用时显式回退到本地规则 IR，并记录
`degradedReason=hybrid_v2_parse_failed`；无法安全直连时回落到原 Harness。

**取舍**：需要维护一份窄规则和边界测试（例如“那北京的呢”必须留在 Agent 以承接会话）；
换来明确查询的低延迟、零 Agent 编排 LLM 成本，以及复杂查询仍保留完整反思能力。代价是简单
搜索仍保留一次 V2 parser 调用，这与生产 Hybrid V2 的 NL2IR 口径一致。

## D6. 降级矩阵：阶段级失败可控、可观测

**背景**：一次搜索涉及 5+ 次 LLM 调用与多条工具路径，任一点失败若直接冒泡，
用户体验是「整单失败」。

**选择**：把 `(阶段, 失败)` 映射为预定义动作（`core/degrade.py`），
降级后循环继续或收敛；失败信息以 `degradedReason` 透传，可观测、可统计。

**取舍**：需要仔细定义每个降级动作的语义与测试（`tests/test_degradation.py`）；换来阶段级失败的
明确处理路径、可诊断的终止状态，以及可统计的降级质量指标。对于已覆盖的阶段级失败，系统尽可能通过
降级继续执行或以可解释的方式终止，并通过 `degradedReason` 和 Trace 保留诊断信息。

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

**选择**：离线 demo 使用 16 个虚构候选人（`c001`–`c016`），真实模型 A/B 使用种子化生成的
100 个虚构候选人（`g001`–`g100`，`seed=2026`），均贴合真实简历形态
（技能 / 工作经历 / 教育 / 期望）。100 人基准的标签在生成时由能力集合与最低年限规则导出，
标记为 `synthetic_oracle`，不是人工标注或公司数据。

**取舍**：演示的多样性有限；换来零合规风险、可重建语料和可完全离线校验的生成器。真实模型实验
仍需使用者自己的模型 key 与 Elasticsearch，但结果边界会在报告中明确标注。

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

**选择**：`Dockerfile` 两阶段——build 阶段 `pip wheel ".[api,es]"` 产出全部 wheel，runtime 阶段只装 wheel（镜像里没有编译工具链）、非 root 运行、内置 `HEALTHCHECK`；compose 增加 `api` 服务，等待 Elasticsearch 与 gateway 健康后启动，先执行 `hybrid-v2 index --recreate` 重灌 16 人演示索引，再以 `--offline` 启动快速演示预置；配置主模型 key 后，compose 把在线预置接到内部 gateway。后端选择收敛到 `tools/factory.py`，CLI `--backend` 与容器入口共用一条路径。

**取舍**：镜像默认「脚本化 LLM + 16 人内存库」的快速演示，零 API key 也能复现完整自适应搜索链路；配置上游 key 后可在页面切到「真实 LLM + Hybrid V2 + ES」。`--recreate` 对单实例演示是自愈，多副本部署应改成一次性 init job——这点写进了 README 的说明。

**影响**：`docker compose up -d --build` 之后 `/healthz` 的初始 `backend` 是 `InMemorySearchTool`，`onlineBackend` 是 `HybridV2SearchService`；配置 key 并通过 `/runtime/mode` 切换后，检索走容器内 Elasticsearch。后续加向量后端只需在 factory 里多一个分支。

## D18. 向量通道：dense_vector + kNN，与 BM25 做 RRF 融合

**背景**：三个后端逐位一致证明了「换引擎不改 Agent」，但召回手段只有关键词一条——真实的人才检索里，长查询在 AND 语义下 0 命中是常态，向量召回是刚需，也是面试必问。

**选择**：新增 `tools/embedding.py`（blake2b 分桶的词袋向量 + 次线性 TF + L2 归一化，256 维，零依赖）；ES 映射加 `dense_vector`（cosine，`index: true`，旧索引启动时补 `put_mapping`）；新增 `search_semantic`（kNN，可带 `where` filter）与 `hybrid_search`（BM25 + kNN 各取 `branch_k`，按 RRF `1/(60+rank)` 融合，逐行保留两支路的 rank / score）。

**取舍**：不引入 embedding 模型依赖——不下载权重、CI 可跑、零 API key，代价是「语义」只到词袋级别，因此代码与文档都明确写成「确定性向量，替换 `embed_text` 即可接真实模型」，不夸大效果。融合放在召回层（RRF）而不是打分层，`search_with_intent` 一行未动，三后端逐位一致的测试继续成立。

**影响**：仓库现在覆盖「关键词 → 向量 → 混合召回」这条链路，`examples/es_hybrid_recall.py` 可直接演示「关键词 0 命中、向量召回 5 人、RRF 给出融合排序」；后续接 pgvector / Milvus 也只需再实现一个后端并复用 `embed_text`。

## D19. 宽召回池、窄交付（对齐原系统漏斗）

**背景**：多路径的价值取决于每条路径能看到多少人。早期实现里每条路径只回
`top_k`，多路径退化成「同一批人换关键词重排」，评测里几乎看不到增量。

**选择**：`SearchToolRequest.recall_pool_size` 落地到三个后端——每路径默认召回
`max(top_k*3, 30)`（`SEARCH_AGENT_RECALL_POOL_SIZE` 可调），judge 全量池、
finalize 只交付 `top_k`；单路径构造器（计划降级 / single_pass / 评测基线）
带上人才画像的关键词与软信号，避免大语料下退化成按 docId 抽签。

**取舍**：judge 的 LLM 调用覆盖更大候选集、token 成本上升（所以 D20 的 token
记账要先有）；换来的是「多路径」这件事在数据上真正成立。

## D20. Token 记账进入预算，而不是事后统计

**背景**：成本如果只存在于账单里，循环就无法「按钱停」；对照实验显示全循环的
token 消耗约为单趟的 2.8 倍，这个交易需要被显式量化。

**选择**：`LlmUsage` 从 provider response 读 `prompt_tokens/completion_tokens`
（网关不返回 usage 时退回字符估算，并标记 `tokensEstimated`）；`MeteredLlmClient`
把每次调用记进 `BudgetState`；`Budget.max_tokens`（`SEARCH_AGENT_TOKEN_BUDGET`，
0 = 只记账不设限）命中即终止（`token_budget`）。不可解析但已产生的调用同样记账。

**取舍**：预算模型多一维；换来「效果 vs 成本」可以在同一次评测里被量化。

## D21. LLM 客户端韧性：重试与 JSON 修复都在边界层

**背景**：真实网关会出现超时、429、5xx、连接中断，以及「返回 markdown 代码块
或夹带解释」的 JSON。这些都不该让循环感知。

**选择**：`OpenAILlmClient` 对瞬时错误（超时 / 连接 / 408 / 429 / 5xx）按
`SEARCH_AGENT_LLM_RETRIES` 指数退避 + 抖动重试；解析失败先剥离代码块与前后文，
再用一轮 `SEARCH_AGENT_LLM_JSON_REPAIRS` 把模型自己的输出回传要求重写；重试次数
进入指标（`llmRetries`）。整条路径用注入的假 `openai` 模块单测覆盖，零网络。

**取舍**：重试会放大延迟与成本，因此有次数上限、只对瞬时错误生效；「非法 JSON
直接失败」的严格模式留给未来配置项。

## D22. 评测去自证：真值来自生成器，而不是 Agent 的 judge

**背景**：用 Agent 自己的 judge 评自己会系统性高估——judge 的判定就是交付依据。
对外展示的数字需要一个外部真值。

**选择**：`corpus.py` 生成种子化合成语料——每条简历按能力逐个掷骰，决定该能力
用技能关键词还是只用项目语言表达；`oracle.py` 以生成器的 `relevant()` /
`semantic_only()` 为真值打分（交付列表召回 / 精确率、关键词可见 vs 仅语义可见
召回），并统计「仅全循环发现」的增量。`search-agent corpus --out DIR` 落盘固定
语料，`eval --oracle DIR` 读入复跑，`eval --generated N --seed S` 即时生成。

**取舍**：合成语料不能替代真实数据——它的价值是「量出天花板」：无真实 embedding 时，
「仅语义可见」人群只能靠共享语素的粗粒度命中被偶然捞到（micro 召回 0.36）、且排不进
前 k，多路径救不了；这个结论直接指向 Roadmap 里的真实 embedding，而不是把短板藏起来。

## D23. 策略记忆：记「哪一类策略有效」，而不记查询原文

**背景**：反思循环每次都从冷启动开始——同一类岗位跑过十次，第十一次的 plan / reflect
仍然不知道「loosen 在这类岗位上比 add_path 更产出」这件事；每次实验都在重复一次相同的试错。

**选择**：`core/experience.py` 把每次运行的“真正合格”候选人按来源路径 `origin` 归因，
按查询族（must 能力名排序后 sha1 取前 12 位）累计 `runs / qualified / tokens`；plan / reflect 的
payload 在有观测时多一个 `strategyPriors` 字段（含 `runs` 与 `avg_qualified`，无观测时整键省略）。

**取舍**：
- 族键用能力集哈希而不是查询原文：近似 JD 共享先验，且无查询文本 / 候选人数据离开进程；
  代价是粒度粗（同一能力集下的差异化查询共用先验）——用占位的信号换隐私与可泛化。
- 只记 qualified（真正合格）而不记 judge 自评的分数：否则把自评的偏差写进了跨请求的长期记忆，误差会复利。
- 存储与会话记忆同构（Redis 可选 + 进程内降级，30 天 TTL），读写失败只记日志；
  策略记忆是“有更好，没也能跑”的增强，不能成为新的故障点。

## D24. 消融表：把「循环有没有用」拆成每个机制值多少钱

**背景**：单独一句「全循环 vs 单趟基线」的差值无法归因——多路径、反思、签名防重、收敛再搜
是四个独立的结构赌注，效果混在一个数字里，说不清谁在起作用；而对外展示时"我做了循环"这种
说法也无法被验证。

**选择**：`core/ablation.py` 定义六个网格行，每行只翻转一个开关（或只调一个预算上限）；
`Deps.ablation`（默认 `None`）是唯一注入点，不开开关时流水线与生产路径完全一致。
`eval --ablate` 在同一语料 / seed / top_k 上跑完整网格并输出对照表；`--save-baseline` /
`--baseline` 把结果落盘成 CI 门禁（比例指标容差 0.02、成本容差 10%、降级即失败）。

**两个关键细节**：
- 消融默认把 `target_qualified` 抬到语料规模：首轮就达标时 `should_finalize` 会直接结束，
  reflect / revise / 收敛再搜根本不会执行，关掉它们自然"没有区别"——这会让消融表撒谎。
  强制迭代后各机制才可观察。
- 门禁只比确定性指标（池召回 / 召回 / 精确率 / 词法可见 / 独有合格数 / LLM 调用 / token），
  绝不比耗时（机器相关）；`tests/test_ablation.py` 专门校验跨轮确定性，否则门禁本身会抖。

**取舍**：结论不全是好消息。当前 120 人、Seed 7、top-10 基准中，完整循环相对单趟基线的
池召回为 `0.6970 → 0.7879`、交付 Recall@10 为 `0.3182 → 0.4697`；但聚合值同时包含
多路径、反思和收敛再搜，不能把增量全部归因于单一机制。单独关闭多路径的 `single_path_loop`
行与完整循环相同（`0.7879 / 0.4697`），说明这套合成数据上尚未观察到多路径额外收益；
关闭收敛再搜则把 Recall@10 降到 `0.3030`。这条中性/负向结果保留在文档里，下一步用真实
embedding 和更互补的路径重新验证。展示一个"部分无效"的机制比只展示胜利更难，但也更难被面试官问倒。

## D25. 池召回 vs 交付召回：把「没找到」和「没排上」分开

**背景**：全循环交付 Recall@10 为 0.4697，高于单趟基线的 0.3182；但只看这个差值仍然
无法区分两种失败——检索层没把人带进池子，和排序层把人丢在 top-k 之外。想知道该修哪一层，
必须把池召回与交付召回拆开量。

**选择**：`OracleScore` 增加 `pool_found` / `pool_recall`（探索层：路径召回池 ∪ 交付
列表里出现过的合格人）；文本 / Markdown 报告与消融表都按「池召回 → 交付召回」两行
展示；`eval_baseline.json` 门禁一并覆盖 `pool_recall`。当前基准中，关闭收敛再搜使
Recall@10 `0.4697 → 0.3030`、池召回 `0.7879 → 0.7576`；`single_path_loop` 与完整循环
暂未观察到质量差异，因此不能把多路径写成已验证的普遍增益。

**取舍**：多一个指标多一分维护成本（基线 / 门禁 / 测试都要同步）；换来的是每个数字
可归因——交付低时说清是「探索没够」还是「排序丢了」，并直接指向真实 embedding 打分。

## D26. Hybrid V2 作为独立引擎进入仓库，而不是运行时外挂

**背景**：如果只保留 `SearchTool` 适配器，公开仓库仍然无法证明 IR 编译、硬过滤、
BM25/kNN 与重排能一起运行；面试官只能看到接口，看不到执行层。

**选择**：把可公开的部分迁入 `src/search_agent/hybrid_v2/`，保留硬条件编译、工作年限/
空窗/司龄规则、本地 O*NET 别名、多字段混合召回、可解释 hardMatches 和有界重排；不包含
图扩展、托管词典、外部存储或内置凭据。ES 地址、索引、认证、字段名、embedding、标签和
目录文件全部由环境变量注入，公开测试使用假 ES 客户端和确定性 hash embedder。

**取舍**：Hybrid V2 需要使用者自己准备 Elasticsearch 8 和匹配的索引映射，不能像内存
后端一样零基础设施启动；因此 CLI 在未配置 ES 时继续默认 `memory`，显式配置 ES 后才
默认 `hybrid_v2`。这保证公开 demo 仍然可用，同时不把「能在本机跑」误写成「已在生产
部署」。

**影响**：仓库现在有四条可运行检索路径；Hybrid V2 与 Agent 之间仍只有 `SearchTool` 协议，
不会把检索实现细节泄漏进 Agent 状态机。接入和字段契约见 `docs/HYBRID_V2.md`。

## D27. 三层会话记忆：工作记忆、服务端归档、候选人排除

**背景**：招聘搜索天然是多轮的：“最好有大厂背景”“不要外包”“换个方向”都需要以上一轮
条件为起点；同时招聘者需要重新打开历史会话，并明确告诉 Agent 某位候选人不再考虑。把完整
聊天、完整简历或长期用户画像全部塞进 Prompt，会放大 token、延迟、隐私和跨租户泄漏风险。

**选择**：

- **L1 工作记忆**：Prompt 只注入最近 3 轮的结构化条件摘要，承接当前搜索所需条件。
- **L2 会话归档**：Redis（未配置时进程内降级）保存最多 50 轮、TTL 30 天；每轮保存 query、
  助手摘要、route/status、queryToken、Top 候选人 ID 和最多 10 个精简候选人卡片字段。
  前端左侧列出最近会话，恢复时从服务端取回完整历史；新建检索不删除旧会话。
- **L3 候选人记忆**：记录 `seenCandidateIds` 与 `excludedCandidateIds`。只有显式排除项进入
  `SearchToolRequest.exclude_doc_ids`，并额外对最终结果做防御性过滤；看过但未排除的人不会
  被自动永久过滤。卡片上的“排除/恢复”通过 PATCH API 持久化。

所有会话严格绑定 `(companyId, hrId)`，`hrId=None` 也是独立作用域。结果分页继续由独立的
`AgentSessionStore` 保存本次 `final_items`，TTL 1 小时，翻页只切片，不重新检索或调用 LLM。

**取舍**：保存恢复所需的最小候选人快照，而不是完整简历或完整 Trace，牺牲“任意历史卡片都可
展开全部原始字段”的能力，换来更小的 Redis 体积和更低的隐私面。只自动过滤显式排除项，牺牲
部分跨轮去重收益，避免因看过一次就永久漏掉后来重新合适的候选人。不做跨会话用户画像。

**影响**：新增会话列表、详情、删除和候选人排除 API；`conversationId` 与 `pageSize` 仍是可选
增强，不传时保持旧搜索契约。Redis 不可用时进程内降级；多进程部署需显式配置
`SEARCH_AGENT_REDIS_URL`，否则各进程看到的历史不一致。记忆读写失败只记日志，不能阻断主搜索。

## D28. 简单直连与复杂链路不强行统一排序分

**背景**：明确查询走 Hybrid V2 直连，一次检索后由 V2 reranker 给出可直接排序的分数；复杂查询
需要 Agent 先覆盖多条搜索路径、再由 judge 判断能力是否真正命中。这两种流程的证据来源和优化目标
不同，强行套用同一个绝对分数会掩盖 judge 的结论，或为了统一口径增加无必要的 LLM 调用。

**选择**：直连链路直接采用 Hybrid V2 的 rerank 分数；复杂链路最终按
`_selection_key` 排序，优先看 must 能力覆盖、缺失能力、必需硬条件违规、简历证据和是否已判断，
后端/重排分数只作为后续 tie-breaker。回退路径仍可使用 `rerank_pool` 或 per-path 分数，但最终选择
继续遵循同一能力优先顺序。API 与前端统一称其为“排序分”，并明确它只在同一结果集内有效。

**取舍**：两种链路的分数不再具备跨模式可比性，展示层也不能把它解释成概率或匹配率；换来的是复杂
链路保留 Agent 的精度排序、直连链路保留低延迟与低 token 成本。对外比较应使用 recall、precision
等评测指标，而不是直接比较两条链路的原始 `score`。
