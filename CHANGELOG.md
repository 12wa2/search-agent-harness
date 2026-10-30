# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- **搜索运行并发闸门**（`core/concurrency.py`、`config.py`、`server.py`、`web/`）：为
  `/search` 与 `/search/stream` 增加进程内有界准入控制，限制活跃运行、排队请求与等待超时。
  超限返回 HTTP `429`、`Retry-After` 和结构化 `server_busy` 原因；同步与 SSE 路径均幂等释放
  运行租约，`/healthz` 暴露 `active`、`queued`、`peakActive`、`accepted`、`completed`、
  `rejected`、`timeouts` 与平均排队时间。新增 `SEARCH_AGENT_MAX_CONCURRENT_RUNS`、
  `SEARCH_AGENT_MAX_QUEUED_RUNS`、`SEARCH_AGENT_RUN_QUEUE_TIMEOUT_SEC`，以及并发专项测试、
  标准库压测脚本和验收报告，并完成真实在线单次冒烟与 25 客户端突发验证。当前限额按 uvicorn
  进程生效，不引入消息队列。
- **每轮搜索策略效果**（`core/round_metrics.py`、`harness.py`、`graph.py`、`service.py`、
  `server.py`、`web/`）：在不改变检索、排序与硬条件软门控的前提下，按 `doc_id` 去重记录每一轮
  的新增召回、新判断、必备能力全通过人数、新增路径 / 软信号 / 来源假设，以及 LLM 与墙钟成本。
  第一轮作为初始策略基线，后续轮次展示触发缺口、策略动作、收益和结束原因；最终收敛检索单独记录，
  避免把 finalize 成本重复计入上一轮。新增 `debug.roundSummaries`、`debug.finalSearch` 与 SSE
  `evaluate` / `revise` 事件中的 `roundSummary`，对话结果区同步展示策略效果卡片。同时修正
  `rounds.newQualifiedPerRound` 为真实的逐轮增量，并保留 `qualifiedTotalPerRound` 累计口径；
  明确区分“执行轮次”（`roundSummaries` 的已完成次数）与“反思迭代”（`rounds.iterations`），
  修复单轮收敛时 `0 检索轮次` 与 `1 轮` 的口径冲突；Agent 体验层用“策略调整”呈现反思迭代，
  运行账本与执行观测保留“执行轮次 / 反思迭代”的技术口径。
- **弱直连信号的 LLM 入口复核**（`entry_router.py`、`service.py`、`server.py`、`docs/INTENT_ROUTING.md`）：
  强直连规则保持零额外调用；只有缺少岗位/技能、仅含城市或年限等弱直连计划，才调用一次
  `agent_entry_route` 轻量分类器复核 `direct_search | agent`。高置信度遵从模型判断，低置信度安全回退
  Agent，模型不可用或格式非法时保留原规则直连。直连指标新增 `entryLlmCalls` / `parserLlmCalls`
  分项，SSE 与 debug 暴露 `entryRoute`、来源和置信度，并补充单元与 Service 回归测试。
- **L1/L2/L3 会话记忆与历史会话恢复**（`conversation_memory.py`、`service.py`、`server.py`、`web/`）：
  L1 只向理解 Prompt 注入最近 3 轮结构化条件；L2 将完整轮次归档到 Redis（未配置时进程内降级），
  默认保留 30 天、最多 50 轮，并可恢复候选人与助手摘要；L3 记录已看候选人与显式排除项，只有显式排除
  会传给 `SearchToolRequest.exclude_doc_ids` 并额外过滤最终结果。左侧会话栏改为“最近会话”，支持恢复
  完整历史、候选人“排除/恢复”，新建检索不再删除旧归档。新增 `POST/GET/DELETE /conversations`、
  `GET /conversations/{id}` 与候选人排除 PATCH API，补充租户隔离、快照恢复、排除传递和 API 回归测试；
  新增 `SEARCH_AGENT_CONVERSATION_TTL_SEC` / `SEARCH_AGENT_CONVERSATION_MAX_TURNS` 配置。
  问候、帮助和范围外回复也会写入 `status=reply` 的会话轮次并即时出现在左侧，但不会进入 L1 搜索上下文，
  仍然不触发 Agent LLM 或检索工具。
- **Agent 工作区改版**（`src/search_agent/web/`）：对话视图增加固定会话侧栏，集中展示“新建检索”、
  本次会话轮次与检索原则；首屏改为大标题导语与“一条需求如何变成名单”流程说明的双栏布局，
  复杂需求示例和输入区保持可操作。执行观测新增独立页头，统一阶段、检索路径、账本与 Trace 的
  信息层级。顶部视图切换现在同步到 `?view=agent|console`，刷新后保持当前视图；补充键盘焦点、
  对比度、减少动效和响应式细节，并更新 `tests/test_server.py` 的页面结构与资源断言。
  同步刷新 `docs/console.png` 与 `docs/console-run.png`。
- **Hybrid V2 直连简单搜索**（`direct_search.py`、`hybrid_v2/parser.py`、`service.py`、`server.py`、
  `web/`）：明确技能、岗位、年限或城市的低不确定查询先经过保守直连门槛，再由 Hybrid V2 parser
  生成 `CandidateSearchIR`，最后只调用一次 `SearchTool` 完成 normalize、compile、BM25/kNN 召回
  和重排。新增 `direct_search → execute → finalize` SSE 路由，`difficultyRoute=direct_search`，
  不产生 `understand / plan / judge / reflect / revise`，Agent 编排 LLM 调用为 0；V2 parser LLM
  通常为 1 次（语义重试最多 2 次），Token、耗时和重试次数分别写入 `parserLlmCalls` /
  `promptTokens` / `completionTokens` / `llmRetries`。在线模式使用运行时网关，快速演示使用同一
  IR 契约的脚本化 parser；parser 两次失败时显式回退到 `local_rules_fallback`，并记录
  `degradedReason=hybrid_v2_parse_failed`。模糊、偏好重、会话承接和可迁移能力查询继续进入原
  自适应 Agent。补充直连门槛、V2 parser、失败降级、单次工具调用与服务/SSE 回归测试，并更新
  前端路由标签、CandidateSearchIR 展开详情、README 与架构说明。
- **运行模式切换保护**（`web/`）：从快速演示切入完整在线前显示轻量确认框，说明真实模型调用、
  潜在 API 费用、数据发送边界及“只影响后续搜索”；切回快速演示不增加步骤。同步校正澄清规则
  注释、补充泛化搜索动词的回归测试，并统一 README 测试徽章与 Roadmap 的实际测试口径。
- **按难度自适应路由**（`core/difficulty.py`、`harness.py`、`understand`、`web/`）：明确搜索动作 +
  岗位/具体技能走 5 阶段 `single_pass`，不调用 `plan` / `reflect` / `revise`；补充“微服务/
  机器学习/数据平台”等具体信息不会自动升级，只有开放式偏好、抽象能力或需要多路径探索时才进入
  反思链路。SSE 暴露 `requestIntent` / `intentRule` / `difficultyRoute` / `plannedStages` /
  `executedStages`，执行观测新增“本次路由”卡片，展示 `SEARCH → 轻量/缩减/完整反思` 与实际阶段数。
- **对话交付可解释性**（`core/clarification.py`、`understand` / `revise`、`web/`）：首轮只包含
  裸技能且没有搜索动作时，Agent 会先返回可点击的方向选项，而不是直接进入检索；已可执行的
  “找会 Python 的”会直接检索。有会话历史时不会重复打断。对话视图把完整执行过程压缩为“当前阶段 +
  实际阶段数 + 事件数”，详细事件默认折叠。
  候选人卡新增命中能力、未覆盖能力、硬条件警告、来源路径和排序分口径说明；交付区展示目标人数、
  实际交付、人数缺口与 `finalizeReason`，并用真实 `evaluate → reflect → revise` 事件解释本轮为何
  继续或结束。`SearchResponse` 新增 `topK`，`understand` 事件新增 `needsClarification` /
  `clarifyQuestions`，`revise` 补齐 `reflection_empty` / `no_path_actions` / `paths_exhausted` /
  `clarification_requested` 等结束原因；补充澄清边界、SSE 载荷与 revise 终止语义测试。
- **Web 双预设运行时切换**（`llm/runtime.py`、`tools/runtime.py`、`server.py`、`web/`）：HTTP 服务
  现在同时持有两个完整预设——快速演示（脚本化 LLM + `InMemorySearchTool` + 16 人内存库）与完整在线
  （OpenAI-compatible LLM + Hybrid V2 + Elasticsearch 索引）。新增 `POST /runtime/mode`，Web 顶栏
  的模式胶囊会同时切换 LLM 与 SearchTool；`/healthz` 返回 `mode`、`backend`、`offlineBackend`、
  `onlineBackend` 与 `onlineConfigured`。完整在线未配置或 Agent 正在执行时返回 `409`，不会静默
  假装切换成功；搜索运行期间用加锁计数器阻止模式变更，避免同一条 trace 混用两个预设。
- **本地启动默认快速演示**（`scripts/start-dev.ps1`、`.env.example`）：启动器现在每次自动读取仓库根目录
  `.env`，已有环境变量优先；Web API 默认追加 `--offline`，因此即使配置了上游 key，页面首次打开仍是
  快速演示，可在运行时切换到完整在线。检测到 `PRIMARY_LLM_API_KEY` / `FALLBACK_LLM_API_KEY` 时自动
  启动或复用网关；没有上游 key 时跳过网关，避免把不可用的在线模型误报为已配置。新增 `-StartOnline`
  用于需要初始完整在线启动的场景。
- **对话式 Agent 前端（默认体验）**（`src/search_agent/web/`）：`GET /` 的首屏从三栏监控台改为
  面向招聘用户的对话界面——提供 8 条复杂招聘示例、自然语言输入、需求解析标签、按真实 SSE
  阶段事件生成的可读进度、可折叠执行详情和内联候选人卡片。候选人证据、硬条件、来源路径与
  分页仍可展开查看；问候 / 帮助 / 范围外请求直接在对话中回复，不启动 Harness。
  新增“Agent 体验 / 执行观测”双模式切换，两种视图共享同一次运行、同一份结果、指标、Trace
  与会话状态，切换不会重跑搜索；原三栏控制台完整保留，用于技术审查。
  `understand` 阶段事件新增向后兼容的 `hardConstraintDetails`，前端据此展示解析后的经验、城市
  与排除条件；补充静态页、SSE 载荷和前端资源断言，并新增 `docs/console-run.png` 运行截图。
- **Web 控制台视觉重构**（`src/search_agent/web/`）：将深色终端式界面改为浅色“招聘决策台”，
  以岗位简报、Agent 审计轨迹和候选人档案组成三栏工作区；运行/停止/分页改用内联 SVG，
  候选人改为可展开的档案行，阶段轨道与运行账本使用等宽数字；补齐键盘焦点、跳过导航、
  `prefers-reduced-motion`、弱文本对比度和 320–1440px 响应式检查。移动端取消面板内层滚动，
  改为自然页面流；新增 Agent 待命预演和实时状态条（待命 / 执行 / 完成 / 停止 / 路由 / 错误），
  工作区最大宽度 1760px，避免超宽屏把轨迹与候选人面板拉散。中央轨迹进一步改为可阅读的
  Agent 任务线程：先显示用户任务，再逐阶段呈现决策摘要、SearchTool 调用卡、覆盖检查、
  缺口反思与策略改写，完成后追加交付闭环；非搜索请求由意图路由直接回复，不展示虚假的
  检索任务或候选人结果。接口、SSE 事件、会话记忆、停止语义与分页行为不变。
  同步更新 `docs/console.png`。
- **V2 原生字段简历基准**（`examples/bench_v2_100/`、`src/search_agent/v2_resume_corpus.py`、
  `tests/test_v2_resume_corpus.py`）：替换早期轻量字段语料，新增 100 份可复现简历，完整覆盖
  工作职责、项目经历、自我评价、技能、教育、求职意向和基础画像；导入 ES 时继续由现有索引器
  派生 `work_stats`、最高学历和 5 类文本向量。新数据保持 10 条复杂 query，并逐条重新生成
  `relevant` oracle；旧版 `examples/bench100/` 已删除。
- **早期真实模型 A/B 实验（旧版轻量字段基准）**（`examples/real_ab_experiment.py`、
  `examples/build_real_ab_bench.py`、`examples/real_ab_queries_100.json`）：
  历史结果保留在 `docs/real_llm_ab_results_100.*`。该实验使用 100 人种子化语料、10 条复杂 query
  和 `synthetic_oracle` 标签，固定同一 Qwen 网关、
  Elasticsearch 索引与 `top-k=5`，比较单趟基线和完整反思循环。正式复核（`qwen3.8-flash`）记录
  recall@5 `0.392 → 0.473`、precision `0.740 → 0.840`、池召回 `0.735 → 0.937`、
  新增相关候选 `17` 个（相关率 `0.708`）、LLM 调用 `20 → 59`、tokens
  `148,428 → 446,704`、Agent 降级 `2` 次。报告同时保留配对 bootstrap 区间、逐 query 明细和
  “单次合成观测、不可与公司指标混用”的边界。实验入口新增 `--workers`，支持按 query 隔离并发。
- **网关一键启动**（`docker-compose.yml`、`scripts/start-dev.ps1`）：Compose 新增
  `gateway` 服务，与 Elasticsearch、API 一起后台启动并配置 `restart: unless-stopped`；
  配置上游主 key 后，API 的在线预置通过 `http://gateway:4000/v1` 调用该服务；
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
- **轻量多轮会话与结果分页**（`conversation_memory.py`、`session.py`、`service.py`、`server.py`）：
  搜索响应新增自动生成的 `conversationId` 与 `conversationTurns`；传 `pageSize` 时再返回 `queryToken`
  与分页元数据。对话记忆最多保留 10 轮、只注入最近 3 轮、TTL 2 小时，并按 `(companyId, hrId)`
  精确校验归属；
  结果池由 Redis/进程内 Session 按 1 小时 TTL 保存，新增 `GET/DELETE /sessions/{queryToken}`。
  Web 控制台加入“新对话”、历史 query、轮次提示和候选人分页控件；不传 `pageSize` 时保持原 API 行为，
  也不创建多余的结果 Session。脚本化 LLM 对“最好 / 优先 / 不要”等追问做规则合并，离线 demo
  也能观察跨轮条件继承。
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
  （等待 ES 与 gateway 健康后启动并灌入 Hybrid V2 演示索引）：`docker compose up -d --build`
  一条命令起「HTTP 服务 + ES + LLM gateway」。
- **向量通道与混合召回**（`tools/embedding.py`、`tools/elasticsearch.py`）：确定性哈希向量 + ES `dense_vector` kNN，
  与 BM25 按 RRF 融合（`search_semantic` / `hybrid_search`，示例 `examples/es_hybrid_recall.py`）；
  新增 5 个离线向量测试与 5 个 ES 集成用例。

### Changed

- **README 重构为项目首页**（`README.md`、`README.en.md`、`docs/EVALUATION.md`）：将原 800 余行的说明文档收敛为问题背景、执行模型、快速开始、能力边界、实测摘要、接口与文档导航；新增独立英文入口与评测详情页，刷新 Agent 体验、执行观测和交付截图，并同步测试徽章、阶段口径与合成数据边界。
- **文档分层与导航收敛**（`README.md`、`README.en.md`、`docs/`）：首页进一步收敛为项目介绍、执行模型、最小启动与评测摘要；将意图路由、HTTP API、部署与完整配置拆到独立文档；架构文档删除重复路由表并引用意图路由；中英文首页采用同一套文档导航与 3/5/8 阶段口径；同步校准 Hybrid V2 直连的 LLM 调用说明、Token 预算口径和真实 LLM 实验的非完全确定性边界。
- **Hybrid V2 文档中文化**（`docs/HYBRID_V2.md`）：将完整英文说明重写为中文，保留索引契约、向量模式、重排机制、配置项、安全边界与全部命令示例。
- **评测摘要口径校准**（`README.md`、`README.en.md`）：将默认 Oracle 的交付 Recall@10 `0.32 → 0.30` 与强制迭代消融的 `0.32 → 0.41` 分开呈现，并把逐实验成本细节保留在评测文档，避免将不同实验条件混为同一结论。

### Fixed

- **Docker 完整在线依赖**（`Dockerfile`、`docs/ARCHITECTURE.md`、`docs/DECISIONS.md`）：
  build/runtime 阶段同时安装 `openai` extra，修复镜像切到完整在线后真实 LLM 调用缺少 SDK 的问题；
  CI 增加 Docker 运行时导入冒烟。
- **运行期降级与终端兼容**（`session.py`、`conversation_memory.py`、`eval.py`）：Redis 初始化执行
  `ping()`，运行期读写失败自动切回进程内存储；Redis 明确未命中时不会被本地副本复活；
  Windows GBK 控制台不再因评测门禁 emoji 崩溃。
- **权限与检索边界**（`server.py`、`config.py`、`session.py`、`tools/elasticsearch.py`）：新增
  `SEARCH_AGENT_REQUIRE_PERMISSION` fail-closed 模式；分页 token 可校验租户作用域；简版 ES 后端补齐
  ES 凭据/TLS 配置、租户过滤和 talent-pool 权限过滤。
- **LLM 兼容性与连接复用**（`llm/client.py`）：复用同一个 OpenAI client；上游拒绝 strict
  `json_schema` 时自动降级到 `json_object` 和 prompt-only JSON。
- **配置、CI 与文档对齐**（`.dockerignore`、`.github/workflows/ci.yml`、`CONTRIBUTING.md`、`docs/`）：
  排除本地密钥/临时文件；CI 校验前端语法、resume 静态资源和 Docker 运行时依赖；补齐完整在线安装、
  模型名替换、Redis 故障降级、权限边界与历史评测产物说明。
- **可执行 Query 误澄清**（`core/clarification.py`、`stages/understand.py`）：当查询已经包含岗位与年限/技能，或技能与搜索动作时，忽略模型误返回的 `needs_clarification=true`，避免明确的岗位搜索在 `understand` 阶段直接返回 0 人；补充单元测试与 Harness 回归测试。
- **Judge 输入与简单查询延迟**（`harness.py`、`llm/prompts/__init__.py`）：`single_pass` 路由把
  召回/判定候选池上限收紧为 `max(20, top_k * 2)`，默认 `topK=20` 时从 60 人降至 40 人；
  每位候选人只发送年限、技能名、最近两段岗位、学历摘要与截断后的概要，不再把完整工作经历
  和整份简历塞进单次 judge 请求。judge 只返回命中能力，未命中能力由程序推导，且不再生成
  未使用的逐候选人 summary。复杂查询的 `reduced` / `standard` 召回广度不变。本机
  `qwen3.8-flash` 同一单趟查询实测：总耗时 `40.2 s → 24.2 s`，judge `32.4 s → 12.7 s`，
  tokens `23,968 → 13,183`（单次观测，用于验证链路收益）。
- **Redis 5 兼容性**（`session.py`、`conversation_memory.py`、`core/experience.py`）：
  Redis 客户端显式使用 RESP2，兼容不支持 `HELLO 3` 的 Windows Redis 5，避免会话、对话记忆与
  策略记忆在运行期报 `unknown command HELLO`。
- **耗时单位统一**（`web/`）：运行账本、执行详情和阶段时间线统一把毫秒换算为秒并保留一位小数，
  例如 `40195 ms` 显示为 `40.2 s`，避免技术观测视图混用单位。
- **启动脚本与浏览器缓存**（`scripts/start-dev.ps1`、`server.py`、`web/`）：再次运行启动脚本时，
  如果仓库中的后端源码晚于当前 API 进程，脚本会自动停止旧进程并按请求模式重启，避免出现
  “代码已更新但页面仍由旧服务响应”；`/healthz` 新增 `startedAt` 供启动器判断进程版本。
  HTML/JS/CSS 改为 `no-cache, no-store, must-revalidate`，静态资源增加版本查询参数，确保执行观测
  的路由卡片不会因浏览器缓存而缺失；对话视图的阶段摘要也会同步显示意图与搜索策略。
- **配置、容器与文档对齐**（`.env.example`、`docker-compose.yml`、`Dockerfile`、`web/`、`docs/`）：
  补全 LLM 重试、预算、Redis 别名、embedding 批大小和 Hybrid V2 漏斗配置；Docker Compose
  现在把 API 接到同栈 gateway，并用 Hybrid V2 索引承载在线预置，启动前重建 16 人演示索引。
  修正 `/healthz` 初始 `backend` 应为 `InMemorySearchTool` 的文档口径，并同步启动脚本自动打开
  浏览器、前端未配置提示与网关/架构说明。
- **收敛搜索的最终交付与召回口径**（`stages/finalize.py`、`core/metrics.py`、`eval.py`）：
  收敛再搜不再只截取召回池前 `top-k` 后直接交付；完整收敛池会先进入 judge，再与路径池统一按 must
  能力覆盖、缺失能力、硬条件违规、简历证据与后端分数排序后截断。池召回指标同步纳入只出现在
  `accumulated` 的收敛候选，避免低估 Agent 的探索覆盖。新增 `tests/test_finalize.py` 与
  `tests/test_metrics.py` 回归用例，覆盖“相关候选位于召回池较后位置”和“可迁移岗位证据优先”两种情形。
  评测基线随新交付口径刷新：完整循环池召回 `0.7576 → 0.7879`、交付召回 `0.4091 → 0.4697`、
  精确率 `0.4500 → 0.5167`；完整收敛池进入 judge 也使 LLM 调用与 tokens 上升，属于预期成本变化。
- **难度路由语义**（`core/difficulty.py`、`llm/prompts/__init__.py`）：修正为
  `low → single_pass`、`mid → reduced`、`high → standard`，让明确简单的查询走单趟，
  抽象或存在漏召风险的查询进入完整反思循环；同步更新架构与决策文档的旧口径。
- **首轮空结果终止**（`core/budget.py`）：无待执行路径且首轮新增合格数为 0 时，先允许进入
  reflect，再由 `max_iterations` 约束后续轮次；避免把“首轮策略失败”误判为“没有更多策略”。
- **Hybrid V2 演示语料**（`data/demo_candidates.jsonl`）：为 16 个虚构候选人的工作经历补全
  `startDate` / `endDate`，使“后端经验 + 年限”编译出的 `work_stats.role_tenure` 过滤能够命中。
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
