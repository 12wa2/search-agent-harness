# Hybrid V2：独立的 Elasticsearch 检索引擎

Hybrid V2 是本仓库内置、按生产实践设计的检索后端。配置 Elasticsearch
地址后会自动启用，也可以通过 `--backend hybrid_v2` 显式选择。

该引擎负责五个步骤：

1. 通过注入的 `LlmClient` 将自然语言解析为 `CandidateSearchIR`
   （提示词、JSON schema、IR 修复，以及一次有界重试）；
2. 归一化 IR（工作经历事实和岗位别名）；
3. 将硬条件编译为 Elasticsearch filter；
4. 使用 BM25 与多字段 kNN 召回；
5. 本地重排，或使用有界并发的 OpenAI-compatible 分片重排。

parser 位于 `hybrid_v2/parser.py`，不绑定具体模型供应商：Agent 会注入当前运行时
客户端，因此完整在线模式使用配置好的网关，快速演示则使用同一 IR 契约下的脚本化
客户端。常规 Agent 路径也可以从 `understand` 阶段获得 IR；两条路径最终都会汇合到
同一套 normalize / compile / recall / rerank 引擎。

所有凭据、字段名、词表和服务地址都由使用者在环境变量中提供。仓库不包含凭据、
主机相关的默认配置或真实候选人记录。

## 快速开始

```powershell
pip install -e ".[es,api]"
docker compose up -d elasticsearch
$env:SEARCH_AGENT_HYBRID_V2_ES_URL = "http://127.0.0.1:9200"
$env:SEARCH_AGENT_HYBRID_V2_ES_INDEX = "candidate"

search-agent hybrid-v2 index --recreate
search-agent hybrid-v2 doctor

search-agent search "找 3 年以上、做过高并发系统的后端工程师" `
  --backend hybrid_v2 --offline --top-k 5

search-agent serve --backend hybrid_v2
```

`hybrid-v2 index` 会创建内置 mapping，并默认导入
`src/search_agent/data/demo_candidates.jsonl`。使用自己的语料时，可以传入
`--data <jsonl-or-json>`、`--company-id`、`--vector-mode` 和
`--embedding-dims`。索引与查询时使用的 embedding provider 和维度必须保持一致。
仓库内置路径已使用 Elasticsearch 8.15.0 验证。

`SEARCH_AGENT_HYBRID_V2_ES_URL` 的优先级高于 `SEARCH_AGENT_ES_URL`。两者都
未设置时，默认仍使用零依赖的 `memory` 后端，因此公开演示不需要任何基础设施即可
运行。

检索引擎本身不依赖 Redis。如果需要跨进程共享会话或策略记忆，可将
`SEARCH_AGENT_REDIS_URL`（或 `REDIS_URL`）指向自己的 Redis 实例；未配置时，
Harness 会把这些状态保留在进程内。

## 索引契约

`search-agent hybrid-v2 init-index` 会生成下面的 Elasticsearch 8 mapping，包含
可索引的 `dense_vector` 字段、CJK 文本分析、小写 keyword 子字段、工作统计和所选
向量布局。`search-agent hybrid-v2 index` 会在批量导入前执行同样的初始化。查询
构造器依赖这些字段；手动修改 mapping 后，请运行
`search-agent hybrid-v2 doctor` 检查。

| 区域 | 期望字段 | 说明 |
|---|---|---|
| 身份 | `_id`、`candidate_profile_id` | `_id` 会作为 `docId` 返回 |
| 租户 | `company_id`、`entity_join` | 可配置；将字段名设为空可关闭对应过滤 |
| 可见性 | `deleted`、`profile_pool_id`、`collaborator_id` | 软删除和可选权限过滤 |
| 画像 | `overview.name`、`overview.work_years`、`overview.age`、`overview.city.name` | 候选人摘要字段 |
| 技能 | `skills.name` | 兼容 nested 结构 |
| 工作经历 | `work_experiences.company`、`work_experiences.job`、`work_experiences.description`、开始/结束时间 | 硬文本过滤和 BM25 |
| 项目经历 | `project_experiences.project_name`、`project_experiences.description` | BM25 和项目向量 |
| 教育经历 | `educations.college.name`、`educations.major.name`、`educations.degree.name`、`educations.degree.code`、`educations.school_tier` | 学历门槛使用 code；`school_tier` 表示单段教育经历的 985/211 层次 |
| 求职意向 | `intention.positions`、`intention.industries`、`intention.cities`、`intention.expected_salary`、`intention.work_state` | 接受 nested name/code 对象 |
| 工作统计 | `work_stats.*` | 任职时长、空档、分段数量、岗位任期 |
| 标签 | `talent_tags.tag_id`、`tags` | 标签词表可选且仅保存在本地 |
| 排序 | `candidate_authority_time`、`candidate_profile_id` | 用于没有语义通道的查询排序 |

命中文档也可以把画像以 JSON 对象或 JSON 字符串的形式存放在 `_source.payload` 下；
转换器会在映射公开的 `CandidateItem` 之前将它合并进文档。

## 向量模式

设置 `SEARCH_AGENT_HYBRID_V2_VECTOR_MODE`，使其与索引 mapping 一致：

- `flat`（默认）：向量位于文档的 `work_vector`、`project_vector` 和
  `education_vector`。这是仓库端到端示例使用的广泛兼容路径。
- `nested`：向量位于 `work_segments.vector`、`project_segments.vector` 和
  `education_segments.vector`。仅当目标 ES 8 部署和查询路径已经验证支持 nested
  kNN 时使用；`doctor` 会校验 mapping 形态和维度，但不能证明集群级 nested-kNN
  支持。

无论使用哪种模式，`self_evaluation_vector` 和 `skills_vector` 都是文档级字段。
所有 dense vector 的维度必须与所配置的 embedding provider 一致。

默认 `hash` embedder 是确定性且离线的，仅用于演示和测试，不适合与使用托管模型
构建的索引进行比较。对于真实索引，请设置生成这些向量时所用的 provider 和维度：

```powershell
$env:SEARCH_AGENT_EMBEDDING_PROVIDER = "openai"
$env:SEARCH_AGENT_EMBEDDING_MODEL = "text-embedding-3-small"
$env:SEARCH_AGENT_EMBEDDING_DIMS = "1536"
$env:SEARCH_AGENT_EMBEDDING_API_KEY = "..."
$env:SEARCH_AGENT_EMBEDDING_BASE_URL = "https://api.openai.com/v1"
```

可以使用任何 OpenAI-compatible embedding 端点。API Key 只从环境变量读取，
不会写入 Trace 或响应。

## 重排与安全

默认 reranker 是本地且确定性的：

```text
0.45 * 归一化 ES 分数 + 0.35 * 词法覆盖率 + 0.20 * 硬条件匹配率
```

设置 `SEARCH_AGENT_HYBRID_V2_RERANK_PROVIDER=openai` 可启用分片式 LLM 重排。
每个分片都有固定大小、墙钟超时、worker 并发上限，并在超时、JSON 无效、候选缺失
或供应商报错时回退到本地分数。提示词要求模型把候选人画像字段视为不可信数据，而
不是指令。日志不会写入完整画像；Trace 只包含计数、耗时和状态。

LLM reranker 是可选项，不会改变 Elasticsearch 查询的硬过滤语义。

## 配置分组

完整变量列表见 `.env.example`。最常用的配置项如下：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `SEARCH_AGENT_HYBRID_V2_ES_URL` | 未设置 | 启用 Hybrid V2 |
| `SEARCH_AGENT_HYBRID_V2_ES_INDEX` | `candidate` | 候选人索引 |
| `SEARCH_AGENT_ES_USERNAME` / `SEARCH_AGENT_ES_PASSWORD` | 未设置 | Basic Auth |
| `SEARCH_AGENT_ES_API_KEY` | 未设置 | API Key 认证 |
| `SEARCH_AGENT_ES_VERIFY_CERTS` | `true` | 校验 TLS 证书 |
| `SEARCH_AGENT_ES_REQUEST_TIMEOUT` | `30` | Elasticsearch 请求超时（秒） |
| `SEARCH_AGENT_HYBRID_V2_TENANT_FIELD` | `company_id` | 租户过滤字段 |
| `SEARCH_AGENT_HYBRID_V2_VECTOR_MODE` | `flat` | 向量 mapping 布局 |
| `SEARCH_AGENT_HYBRID_V2_BM25_ENABLED` | `true` | 启用 BM25 召回通道 |
| `SEARCH_AGENT_HYBRID_V2_KNN_K` | `100` | 每个语义查询请求的 kNN 邻居数 |
| `SEARCH_AGENT_HYBRID_V2_KNN_NUM_CANDIDATES` | `500` | kNN 候选窗口 |
| `SEARCH_AGENT_HYBRID_V2_CHEAP_POOL_SIZE` | `100` | 进入高成本重排前保留的候选数 |
| `SEARCH_AGENT_HYBRID_V2_MATCH_RATE_BASELINE` | `40` | 分数缩放前的最低展示匹配率基线 |
| `SEARCH_AGENT_EMBEDDING_PROVIDER` | `hash` | `hash` 用于本地确定性向量，`openai` 使用外部模型 |
| `SEARCH_AGENT_EMBEDDING_MODEL` | `text-embedding-3-small` | embedding 模型名 |
| `SEARCH_AGENT_EMBEDDING_DIMS` | `256` | 必须与索引中的向量维度一致 |
| `SEARCH_AGENT_EMBEDDING_BATCH_SIZE` | `16` | 查询/索引 embedding 的批大小 |
| `SEARCH_AGENT_HYBRID_V2_RERANK_PROVIDER` | `local` | 本地或 OpenAI-compatible 重排 |
| `SEARCH_AGENT_HYBRID_V2_RERANK_SCAN_CAP` | `100` | 送入重排的最大候选池 |
| `SEARCH_AGENT_HYBRID_V2_RERANK_TARGET_N` | `20` | 重排目标数量 |
| `SEARCH_AGENT_HYBRID_V2_RERANK_SHARD_SIZE` | `20` | 每个重排请求的候选数 |
| `SEARCH_AGENT_HYBRID_V2_RERANK_MIN_CONFIDENCE` | `0.5` | 质量统计使用的置信度阈值 |
| `SEARCH_AGENT_HYBRID_V2_RERANK_TIMEOUT_SEC` | `30` | 每个重排分片的超时时间（秒） |
| `SEARCH_AGENT_HYBRID_V2_RERANK_WORKERS` | `4` | 有界并行的重排 worker 数 |
| `SEARCH_AGENT_HYBRID_V2_TAG_DICT` | 未设置 | 本地 JSON 标签名到 ID 的映射 |

表中的默认值与 `hybrid_v2/config.py` 一致；`.env.example` 同时列出了这些运行时
配置，以及 Agent 级 LLM、预算、Redis 和容器设置。

## 范围与限制

- 该引擎有意不包含图扩展、供应商托管词表或存储适配器。
- 公开测试使用确定性的假 ES 响应和 hash embedder，不声称针对真实招聘语料取得了
  基准性能。
- 生产部署应将 Elasticsearch 放在网络控制之后，使用按租户隔离的凭据或过滤条件，
  并增加索引级监控。引擎提供 `health()`，用于执行不返回敏感信息的集群检查。
- `hybrid-v2 init-index` 和 `hybrid-v2 index` 是运维命令。它们可以创建或替换
  索引，不能作为公开 HTTP 端点暴露。
