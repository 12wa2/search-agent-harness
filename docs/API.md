# HTTP API

> FastAPI 参考服务提供同步搜索、SSE 阶段流、会话恢复、结果分页和运行时模式切换。模型定义见 `src/search_agent/models/api.py`，路由实现见 `src/search_agent/server.py`。

## 1. 启动服务

```bash
pip install -e ".[api]"
search-agent serve --offline                 # 快速演示：脚本化 LLM + 内存检索
search-agent serve --backend hybrid_v2       # 完整在线：真实 LLM + Hybrid V2
```

默认地址为 `http://127.0.0.1:8000`。FastAPI 自带的 OpenAPI 文档通常可通过 `/docs` 访问。

本仓库的参考服务不内置用户认证。生产环境应在反向代理或 API 网关层加入鉴权、限流和租户校验；请求里的 `companyId` / `hrId` 只用于数据隔离，不等于身份认证。

## 2. 端点总览

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/` | 零构建 Web 控制台 |
| `GET` | `/healthz` | 当前模式、LLM、后端和在线配置状态 |
| `POST` | `/runtime/mode` | 在快速演示与完整在线预设之间切换 |
| `POST` | `/search` | 同步执行一次搜索 |
| `GET` | `/search/stream` | 通过查询参数启动 SSE 搜索 |
| `POST` | `/search/stream` | 通过 JSON 请求体启动 SSE 搜索，避免查询进入 URL |
| `POST` | `/conversations` | 创建空会话 |
| `GET` | `/conversations` | 列出最近会话 |
| `GET` | `/conversations/{id}` | 恢复完整会话与候选人快照 |
| `DELETE` | `/conversations/{id}` | 删除会话 |
| `PATCH` | `/conversations/{id}/candidates/{docId}` | 排除或恢复候选人 |
| `GET` | `/sessions/{queryToken}` | 分页读取一次运行的结果池 |
| `DELETE` | `/sessions/{queryToken}` | 使分页结果失效 |

## 3. 搜索请求

### 请求字段

| 字段 | 类型 | 默认 | 说明 |
|---|---|---:|---|
| `query` | string | 必填 | 自然语言招聘需求 |
| `companyId` | int | `1` | 租户范围 |
| `hrId` | int/null | `null` | 招聘者范围 |
| `topK` | int | `20` | 交付人数，范围 1–5000 |
| `recallPoolSize` | int/null | 自适应 | 每路径召回池大小 |
| `details` | bool | `false` | 是否返回 Trace 与运行细节 |
| `permission` | object/null | `null` | 可选权限上下文 |
| `conversationId` | string/null | `null` | 续接已有会话 |
| `pageSize` | int/null | 服务默认 | 交付列表分页大小，范围 1–100 |

`permission` 可包含 `companyId`、`hrId`、`hasFullAccess`、`allowedPoolIds`、`allowedCollaboratorIds`。

### POST /search

同步请求，完成整个搜索后一次性返回 `SearchResponse`。

```bash
curl -s http://127.0.0.1:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query":"找 3 年以上、做过高并发系统的后端工程师","topK":5,"details":true}'
```

主要响应字段：

| 字段 | 说明 |
|---|---|
| `query` / `total` / `topK` | 查询文本、交付总数和目标人数 |
| `items` | 候选人列表，包含匹配证据、技能、经历和分数 |
| `debug` | Agent 指标、Trace、路由、降级原因；仅 `details=true` 时需要 |
| `clarification` | 需要用户补充信息时的问题与可选项 |
| `conversationId` / `conversationTurns` | 会话标识与轮次数 |
| `queryToken` / `page` | 结果池分页令牌与当前页信息 |

## 4. SSE 搜索

### GET /search/stream

支持查询参数 `query`、`topK`、`details`、`companyId`、`hrId`、`conversationId`、`pageSize`。

```bash
curl -N "http://127.0.0.1:8000/search/stream?query=找后端工程师&topK=5"
```

### POST /search/stream

请求体与 `/search` 相同，推荐用于包含敏感查询文本的场景。

```bash
curl -N http://127.0.0.1:8000/search/stream \
  -H "Content-Type: application/json" \
  -d '{"query":"找 3 年以上后端工程师","topK":5}'
```

### SSE 事件

| `event` | 数据 | 说明 |
|---|---|---|
| `stage` | `{"stage":"understand","detail":{...}}` | 一个真实阶段完成；`stage` 也可能是 `degrade:<阶段>` |
| `result` | 完整 `SearchResponse` | 搜索结束，包含候选人、指标和可选 Trace |
| `reply` | 意图回复对象 | 问候、身份、帮助或范围外请求，不启动搜索 |
| `error` | `{"message":"..."}` | 工作线程中的未处理错误 |

阶段事件示例：

```text
event: stage
data: {"stage":"execute","detail":{"paths":2,"recalled":19}}

event: result
data: {"query":"...","total":16,"items":[...],"debug":{...}}
```

前端应以真实 `stage` 事件为准，不能因为页面存在 8 个阶段轨道就把未执行阶段标记为完成。路由与阶段定义见 [意图路由](INTENT_ROUTING.md)。

## 5. 会话与候选人排除

### 创建会话

```bash
curl -s http://127.0.0.1:8000/conversations \
  -H "Content-Type: application/json" \
  -d '{"companyId":1,"title":"后端工程师搜索"}'
```

### 列出或恢复

```bash
curl -s "http://127.0.0.1:8000/conversations?companyId=1&limit=30"
curl -s "http://127.0.0.1:8000/conversations/<conversationId>?companyId=1"
```

### 排除或恢复候选人

```bash
curl -s -X PATCH \
  "http://127.0.0.1:8000/conversations/<conversationId>/candidates/<docId>?companyId=1" \
  -H "Content-Type: application/json" \
  -d '{"excluded":true}'
```

会话按 `companyId` 与 `hrId` 校验作用域；不匹配时返回 `404`。L1/L2/L3 记忆语义见 [架构设计](ARCHITECTURE.md)。

## 6. 结果分页

搜索响应中的 `queryToken` 保存了一次运行的合并结果池。翻页只切片，不重新运行 Agent：

```bash
curl -s "http://127.0.0.1:8000/sessions/<queryToken>?offset=20&limit=20"
```

分页结果默认保留 1 小时；Redis 不可用时回退到进程内存储。

## 7. 运行时模式

```bash
curl -s http://127.0.0.1:8000/runtime/mode \
  -H "Content-Type: application/json" \
  -d '{"mode":"online"}'
```

`mode` 只能是 `offline` 或 `online`。在线模式未配置 Hybrid V2 / Elasticsearch 时返回 `409`；搜索执行期间切换也会被拒绝。`GET /healthz` 可查看当前模式、LLM 类型、离线/在线后端与 `onlineConfigured`。

## 8. 状态码与边界

| 状态码 | 场景 |
|---|---|
| `200` | 成功 |
| `404` | 会话或分页令牌不存在，或作用域不匹配 |
| `409` | 在线模式未配置，或运行期间尝试切换模式 |
| `503` | 搜索服务暂时不可用 |

完整 JSON Schema 以运行中的 `/openapi.json` 和 `src/search_agent/models/api.py` 为准。
