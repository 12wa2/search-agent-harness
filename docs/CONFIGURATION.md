# 配置参考

> `.env.example` 是可复制模板；本页按功能解释主要变量。代码默认值以 `src/search_agent/config.py`、`hybrid_v2/config.py` 和 `llm/client.py` 为准。

## 1. 配置加载顺序

Windows 启动器 `scripts/start-dev.ps1` 的优先级为：

1. 当前进程已有的环境变量；
2. Windows `User` / `Machine` 环境变量中的上游 Key；
3. 仓库根目录 `.env` 中尚未设置的变量；
4. 代码默认值。

直接运行 `search-agent ...` 时**不会自动读取 `.env`**。需要先导出变量，或使用 `scripts/start-dev.cmd`。Docker Compose 会使用 shell 环境与 `.env` 做变量替换。

不要提交 `.env`、真实 API Key、候选人数据或企业索引信息。

## 2. 模型与 LLM

| 变量 | 默认 | 说明 |
|---|---|---|
| `SEARCH_AGENT_API_KEY` | 空 | 模型 API Key |
| `OPENAI_API_KEY` | 空 | 上者的兼容别名 |
| `SEARCH_AGENT_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible 端点 |
| `OPENAI_BASE_URL` | 空 | 上者的兼容别名 |
| `SEARCH_AGENT_MODEL` | `gpt-4o-mini` | 模型名 |
| `SEARCH_AGENT_DISABLE_THINKING` | `false` | Qwen 等模型可设为 `1` 发送 `enable_thinking=false` |
| `SEARCH_AGENT_LLM_TIMEOUT_SEC` | `60` | 单次模型请求超时 |
| `SEARCH_AGENT_LLM_RETRIES` | `2` | 瞬时错误重试次数 |
| `SEARCH_AGENT_LLM_JSON_REPAIRS` | `1` | JSON 修复轮数 |
| `SEARCH_AGENT_LLM_BACKOFF_SEC` | `0.5` | 指数退避基数 |
| `SEARCH_AGENT_RESPONSE_FORMAT` | `auto` | `auto` / `json_schema` / `json_object` / `none` |

Qwen、DeepSeek、vLLM 等 OpenAI-compatible 端点可直接接入，但上游必须支持所用请求字段。客户端默认优先发送 strict `json_schema`；如果上游明确拒绝 `response_format`，会依次降级到 `json_object` 和仅靠 prompt 约束的 JSON。需要固定行为时用 `SEARCH_AGENT_RESPONSE_FORMAT` 指定格式。Claude 需要提供 OpenAI-compatible 兼容层的服务或网关。

## 3. 本地 LLM 网关

| 变量 | 默认 | 说明 |
|---|---|---|
| `SEARCH_AGENT_GATEWAY_CONFIG` | 空 | Gateway JSON 配置路径；CLI 也支持 `--config` |
| `SEARCH_AGENT_GATEWAY_API_KEY` | 启动器/Compose 使用 `local-gateway-key` | 网关入站 Bearer Token |
| `PRIMARY_LLM_API_KEY` | 空 | 主模型上游 Key，由 `gateway.local.json` 引用 |
| `FALLBACK_LLM_API_KEY` | 空 | 备用模型上游 Key，由网关配置引用 |

网关别名、上游地址和 fallback 逻辑见 [LLM Gateway](LLM_GATEWAY.md)。模型 Key 应保存在环境变量或未提交的 `.env` 中。

## 4. 会话与 Redis

| 变量 | 默认 | 说明 |
|---|---|---|
| `SEARCH_AGENT_REDIS_URL` | 空 | 会话、策略记忆与分页的 Redis 地址 |
| `REDIS_URL` | 空 | 上者的兼容别名 |
| `SEARCH_AGENT_CONVERSATION_TTL_SEC` | `2592000` | L2 会话归档保留 30 天 |
| `SEARCH_AGENT_CONVERSATION_MAX_TURNS` | `50` | 每个会话最多保留轮数 |

未配置 Redis 时自动使用进程内存储；配置了 Redis 但初始化 ping 或后续读写失败时，也会切回进程内存储。Redis 正常返回“未命中”时以 Redis 为准，不会用本地副本复活已删除或过期的数据。进程重启后进程内会话与分页会丢失；结果分页默认保留 1 小时。多进程部署仍应使用高可用 Redis。

## 5. 预算与召回

| 变量 | 默认 | 说明 |
|---|---|---|
| `SEARCH_AGENT_MAX_ITERATIONS` | `3` | 最大反思迭代数 |
| `SEARCH_AGENT_MAX_PATHS_PER_ITERATION` | `4` | 每轮最多路径数 |
| `SEARCH_AGENT_TARGET_QUALIFIED` | `max(topK*2, 20)` | 目标合格候选人数 |
| `SEARCH_AGENT_RECALL_POOL_SIZE` | `max(topK*3, 30)` | 每路径召回池大小；`0` 恢复窄池 |
| `SEARCH_AGENT_TIME_BUDGET_SEC` | `12.0` | 墙钟预算；在线启动器会设为 `180` |
| `SEARCH_AGENT_LLM_CALL_BUDGET` | `40` | 单次请求 LLM 调用上限 |
| `SEARCH_AGENT_TOKEN_BUDGET` | `0` | Token 上限；`0` 表示不单独限制 |

真实模型链路的延迟和成本通常明显高于脚本化演示，建议按查询族设置独立预算。

## 6. Elasticsearch 与 Hybrid V2

### 基础连接

| 变量 | 默认 | 说明 |
|---|---|---|
| `SEARCH_AGENT_BACKEND` | `hybrid_v2` | `serve` 的默认检索后端 |
| `SEARCH_AGENT_ES_URL` | `http://127.0.0.1:9200` | 简版 ES 与 Hybrid V2 的回退地址 |
| `SEARCH_AGENT_ES_INDEX` | `search-agent-demo` | 简版 ES 索引；Hybrid V2 也把它作为回退 |
| `SEARCH_AGENT_HYBRID_V2_ES_URL` | 回退 `SEARCH_AGENT_ES_URL` | Hybrid V2 地址 |
| `SEARCH_AGENT_HYBRID_V2_ES_INDEX` | 回退 `SEARCH_AGENT_ES_INDEX`，再回退 `candidate` | Hybrid V2 索引 |
| `SEARCH_AGENT_ES_USERNAME` | 空 | 用户名 |
| `SEARCH_AGENT_ES_PASSWORD` | 空 | 密码 |
| `SEARCH_AGENT_ES_API_KEY` | 空 | API Key |
| `SEARCH_AGENT_ES_VERIFY_CERTS` | `true` | 是否校验 TLS 证书 |
| `SEARCH_AGENT_ES_REQUEST_TIMEOUT` | `30` | ES 请求超时秒数 |
| `SEARCH_AGENT_ES_TENANT_ID` | `1` | 简版 ES 索引写入的租户 ID；查询时同时按请求 `companyId` 过滤 |

### 字段与召回

| 变量 | 默认 | 说明 |
|---|---|---|
| `SEARCH_AGENT_HYBRID_V2_TENANT_FIELD` | `company_id` | 租户字段；设为空可关闭 |
| `SEARCH_AGENT_HYBRID_V2_ENTITY_JOIN_FIELD` | `entity_join` | profile 文档关联字段 |
| `SEARCH_AGENT_HYBRID_V2_ENTITY_JOIN_VALUE` | `profile` | 关联值 |
| `SEARCH_AGENT_HYBRID_V2_SOFT_DELETE_FIELD` | `deleted` | 软删除字段 |
| `SEARCH_AGENT_HYBRID_V2_VECTOR_MODE` | `flat` | `flat` 或 `nested` |
| `SEARCH_AGENT_HYBRID_V2_BM25_ENABLED` | `true` | 是否启用 BM25 通道 |
| `SEARCH_AGENT_HYBRID_V2_KNN_K` | `100` | kNN 返回数 |
| `SEARCH_AGENT_HYBRID_V2_KNN_NUM_CANDIDATES` | `500` | kNN 候选数 |
| `SEARCH_AGENT_HYBRID_V2_CHEAP_POOL_SIZE` | `100` | 昂贵重排前的候选池 |
| `SEARCH_AGENT_HYBRID_V2_MATCH_RATE_BASELINE` | `40` | 匹配率映射基线 |

字段约定、mapping 和索引命令见 [Hybrid V2](HYBRID_V2.md)。简版 `--backend es` 也会应用租户和 talent-pool 权限过滤；生产场景仍推荐 Hybrid V2，以便复用完整 mapping、BM25/kNN 和重排。

## 7. Embedding

| 变量 | 默认 | 说明 |
|---|---|---|
| `SEARCH_AGENT_EMBEDDING_PROVIDER` | `hash` | `hash`（离线确定性）或 `openai` |
| `SEARCH_AGENT_EMBEDDING_MODEL` | `text-embedding-3-small` | embedding 模型 |
| `SEARCH_AGENT_EMBEDDING_DIMS` | `256` | 向量维度 |
| `SEARCH_AGENT_EMBEDDING_BATCH_SIZE` | `16` | 批量大小 |
| `SEARCH_AGENT_EMBEDDING_API_KEY` | 回退 LLM Key | embedding Key |
| `SEARCH_AGENT_EMBEDDING_BASE_URL` | 回退 LLM Base URL | OpenAI-compatible embedding 地址 |

默认 `hash` 向量用于离线验证与回归，不代表真实语义 embedding 效果。

## 8. 重排

| 变量 | 默认 | 说明 |
|---|---|---|
| `SEARCH_AGENT_HYBRID_V2_RERANK_PROVIDER` | `local` | `local` 或 `openai` |
| `SEARCH_AGENT_HYBRID_V2_RERANK_MODEL` | `gpt-4o-mini` | LLM reranker 模型 |
| `SEARCH_AGENT_RERANK_API_KEY` | 回退 LLM Key | reranker Key |
| `SEARCH_AGENT_RERANK_BASE_URL` | 回退 LLM Base URL | reranker 端点 |
| `SEARCH_AGENT_HYBRID_V2_RERANK_SCAN_CAP` | `100` | 最多送入重排的候选数 |
| `SEARCH_AGENT_HYBRID_V2_RERANK_TARGET_N` | `20` | 重排目标数量 |
| `SEARCH_AGENT_HYBRID_V2_RERANK_SHARD_SIZE` | `20` | 每个请求的候选数 |
| `SEARCH_AGENT_HYBRID_V2_RERANK_MIN_CONFIDENCE` | `0.5` | 采用 LLM 结果的最低置信度 |
| `SEARCH_AGENT_HYBRID_V2_RERANK_TIMEOUT_SEC` | `30` | 每个分片超时 |
| `SEARCH_AGENT_HYBRID_V2_RERANK_WORKERS` | `4` | 并发分片数 |

## 9. 本地词表

| 变量 | 默认 | 说明 |
|---|---|---|
| `SEARCH_AGENT_HYBRID_V2_TAG_DICT` | 空 | 技能/标签词表 JSON |
| `SEARCH_AGENT_HYBRID_V2_SALARY_BANDS` | 空 | 薪资区间 JSON |
| `SEARCH_AGENT_HYBRID_V2_WORK_STATES` | 空 | 工作状态词表 |
| `SEARCH_AGENT_HYBRID_V2_ONET_ALIASES` | 空 | 岗位别名词表 |

## 10. HTTP 权限边界

| 变量 | 默认 | 说明 |
|---|---|---|
| `SEARCH_AGENT_REQUIRE_PERMISSION` | `false` | 设为 `1` 时，搜索必须携带 `permission`，且会话分页 token 必须带匹配的 `companyId` |

参考服务不内置用户认证。生产部署应由可信 API 网关注入身份和 permission，不要信任浏览器直接提交的 `companyId` / `hrId` / `permission`。

### 10.1 并发准入

`/search` 和 `/search/stream` 共用进程内准入控制。每个 uvicorn worker 各自持有限额；
多 worker 或多实例部署不会自动共享全局配额。

| 变量 | 默认 | 说明 |
|---|---|---|
| `SEARCH_AGENT_MAX_CONCURRENT_RUNS` | `4` | 同时执行的 Agent 运行数，最小 1 |
| `SEARCH_AGENT_MAX_QUEUED_RUNS` | `16` | 等待运行名额的最大请求数；`0` 表示不排队 |
| `SEARCH_AGENT_RUN_QUEUE_TIMEOUT_SEC` | `3.0` | 排队等待秒数；超时返回 `429` 和 `Retry-After` |

超限响应体由 `server.py` 统一生成，`detail.code=server_busy`，`detail.reason` 为
`queue_full` 或 `timeout`。`GET /healthz` 的 `concurrency` 字段提供 `active`、`queued`、
`peakActive`、`accepted`、`completed`、`rejected`、`timeouts` 和平均排队时间，便于压测与排障。
这些限额保护单进程内下游 LLM/ES 资源，不提供跨进程的持久队列或公平调度；需要这些能力时再引入
共享网关或消息队列。

## 11. 容器

| 变量 | 默认 | 说明 |
|---|---|---|
| `SEARCH_AGENT_PORT` | `8000` | 容器内服务端口 |

完整示例与注释见 [`.env.example`](../.env.example)；部署流程见 [部署指南](DEPLOYMENT.md)。
