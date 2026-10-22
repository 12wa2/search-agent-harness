# 部署与本地运行

> 本项目提供三种常用启动方式：零 Key 快速演示、本地完整在线、Docker Compose 全栈。配置变量见 [配置参考](CONFIGURATION.md)。

## 1. 两种运行预设

| 预设 | LLM | 检索 | 适用场景 |
|---|---|---|---|
| 快速演示 | 脚本化 LLM | 16 人内存候选人 | 演示前端、阶段流、会话和预算，不需要 Key |
| 完整在线 | OpenAI-compatible 模型 | Hybrid V2 + Elasticsearch 8 | 真实模型与 Elasticsearch 8 链路验证 |

完整在线模式验证真实模型与 Elasticsearch 8 链路；默认仍使用虚构演示候选人，可通过 `--data` 导入自有语料。真实模型输出和评测数值不保证逐次完全一致。

Redis 是可选项。配置后用于跨进程会话与结果分页；未配置、初始化失败或运行期连接中断时自动回退到进程内存储。

### 安全边界

参考服务不内置用户认证。生产部署必须在反向代理/API 网关完成鉴权，并由网关注入 `companyId`、`hrId` 和 `permission`；不要信任浏览器直接提交这些字段。建议设置：

```bash
SEARCH_AGENT_REQUIRE_PERMISSION=1
```

开启后，搜索必须携带 permission，分页接口必须带匹配的 `companyId`。`GET /search/stream` 无法携带 permission JSON，应改用 `POST /search/stream`。

## 2. 本地快速演示

要求 Python 3.12+。

```bash
git clone https://github.com/12wa2/search-agent-harness.git
cd search-agent-harness
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -e ".[api]"
search-agent serve --offline
```

打开 <http://127.0.0.1:8000>。快速演示不会请求外部模型，也不要求 Elasticsearch。

Windows 可以使用启动器：

```powershell
.\scripts\start-dev.cmd
```

启动器会：

- 读取 Windows 用户/机器环境变量；
- 再读取仓库根目录 `.env`，但已有的进程环境变量优先；
- 如果检测到上游 Key，则自动启动或复用本地 LLM 网关；
- 默认以快速演示模式启动 Web API，并在健康后打开浏览器。

常用参数：

```powershell
.\scripts\start-dev.cmd -StartOnline   # 首次启动即使用完整在线预设
.\scripts\start-dev.cmd -SkipApi       # 只准备网关，不启动 API
```

## 3. 本地完整在线

### 依赖

- 任意 OpenAI-compatible 模型端点；
- Elasticsearch 8；
- 可选 Redis。

只启动仓库自带的 Elasticsearch：

```bash
docker compose up -d elasticsearch
```

Redis 可以使用你自己的实例，或在本机另起一个 Redis 兼容服务。然后在 `.env` 中填写地址。

### 配置与索引

```powershell
pip install -e ".[api,openai,es,redis]"
Copy-Item .env.example .env

# 在 .env 中填写模型、网关和 Elasticsearch 配置
# examples/gateway.docker.json 的模型名只是模板；必须替换成账号实际可用的模型，
# 否则网关会返回 Model not found。
$env:SEARCH_AGENT_HYBRID_V2_ES_URL = "http://127.0.0.1:9200"
$env:SEARCH_AGENT_HYBRID_V2_ES_INDEX = "search-agent-demo"

search-agent hybrid-v2 index --recreate
search-agent hybrid-v2 doctor
```

`hybrid-v2 index` 会创建索引并灌入演示候选人；换成真实数据时使用 `--data <jsonl-or-json>`。`doctor` 检查集群、索引、mapping、向量和文档数量。

### 启动

Windows 推荐：

```powershell
.\scripts\start-dev.cmd -StartOnline
```

其他平台或直接使用 CLI 时，需要先把 `.env` 中的变量导出到当前 shell，再运行：

```bash
search-agent serve --backend hybrid_v2
```

> `scripts/start-dev.ps1` 会自动加载 `.env`；直接运行 `search-agent` 不会隐式读取该文件。

## 4. Docker Compose 全栈

```bash
cp .env.example .env
```

至少填写 `PRIMARY_LLM_API_KEY`；需要主备切换时再填写 `FALLBACK_LLM_API_KEY`：

```dotenv
PRIMARY_LLM_API_KEY=your-primary-key
# FALLBACK_LLM_API_KEY=your-fallback-key
SEARCH_AGENT_GATEWAY_API_KEY=local-gateway-key
```

然后启动：

```bash
docker compose up -d --build
curl http://127.0.0.1:8000/healthz
```

Compose 包含：

| 服务 | 端口 | 作用 |
|---|---:|---|
| `api` | `8000` | FastAPI、Web 控制台、SSE |
| `elasticsearch` | `9200` | Hybrid V2 候选索引 |
| `gateway` | `4000` | 主备模型网关 |

查看日志和停止：

```bash
docker compose logs -f api
docker compose logs -f gateway
docker compose down
```

当前 Compose 没有为 Elasticsearch 声明持久化 volume。重新创建容器后需要再次执行 `hybrid-v2 index --recreate`，或者自行挂载持久化目录。

## 5. 运行模式切换

Web API 同时持有快速演示与完整在线两套预设：

- 页面右上角可以在两者之间切换；
- 切换到完整在线前会显示费用与数据发送提示；
- 模式切换只影响后续搜索，不会重跑当前结果；
- `-StartOnline` 让 API 首次启动就使用完整在线；
- 若 Elasticsearch / Hybrid V2 未配置，切换到在线会返回 `409`。

运行状态可通过以下接口检查：

```bash
curl http://127.0.0.1:8000/healthz
```

响应包含 `mode`、`backend`、`offlineBackend`、`onlineBackend`、`llm` 和 `onlineConfigured`。

## 6. 常见问题

### 页面只能进入快速演示

检查 `healthz.onlineConfigured`。完整在线至少需要可访问的 Elasticsearch 和 Hybrid V2 配置，以及可用的模型端点。

### 启动器提示找不到上游 Key

设置 `PRIMARY_LLM_API_KEY` 或 `FALLBACK_LLM_API_KEY`，或写入 `.env`。快速演示不依赖这些 Key。

### 网关返回 Model not found

`examples/gateway.docker.json` 中的 `qwen3.8-flash`、`deepseek-chat` 等名称是示例值。请改成当前账号和上游端点真实可用的模型名；主备模型都要逐个验证。

### 在线模式返回 409

通常是 Elasticsearch 未配置、索引不存在，或搜索正在执行。先运行 `search-agent hybrid-v2 doctor`，再检查 API 日志。

### Redis 不可用

会话、L2 归档和分页会在 Redis 初始化 ping 或后续读写失败时自动切回进程内存储。单进程演示可用，但重启后数据丢失；多进程部署应配置高可用 Redis，并监控降级日志。

### 端口冲突

默认端口为 API `8000`、Elasticsearch `9200`、Gateway `4000`。停掉占用端口的旧进程，或修改启动参数与 Compose 映射。

## 7. 相关文档

- [配置参考](CONFIGURATION.md)
- [Hybrid V2](HYBRID_V2.md)
- [LLM Gateway](LLM_GATEWAY.md)
- [HTTP API](API.md)
