# 并发准入与压力测试验收报告

> 验收日期：2026-10-27（Asia/Shanghai）  
> 代码基线：并发能力落点 `71eee64`；报告整理时仓库 HEAD 为 `6ed8a85`。后续提交未重跑本报告压测。
> 结论：**并发闸门已接入同步与 SSE 搜索入口；离线边界测试验证了 429、队列上限和等待超时，真实在线链路（LLM Gateway + Hybrid V2 + Elasticsearch）也完成了单次冒烟与 25 并发突发验证。**

## 1. 验收摘要

本轮要解决的问题不是“再加一个 Agent 节点”，而是让 HTTP 服务在并发请求超过下游能力时有一个
明确、可观测、可恢复的准入边界。实现采用进程内有界准入控制，不引入消息队列。

验收结果：

- `POST /search`、`GET /search/stream`、`POST /search/stream` 的搜索请求都在执行前取得运行租约。
- 活跃运行上限为 4、排队上限为 16、排队等待上限为 3 秒（均为默认配置）。
- 超限返回 HTTP `429`、`Retry-After`，并记录 `queue_full` 或 `timeout` 原因。
- SSE 在正常结束、客户端断连和后台清理时都会幂等释放名额。
- 默认离线演示的 10/25/50 客户端并发各 100 次请求全部成功，未触发 429；该 Demo 任务很短，未稳定占满限流器。
- 受控慢检索目标在 50 客户端并发、100 次请求下：12 次成功、88 次 429；健康快照记录峰值活跃 4、峰值排队 16、`queue_full` 80 次、`timeout` 8 次。
- 真实在线单次搜索：HTTP 200、端到端 `62.08s`、交付 5 人；实际走过 `understand → plan → execute → judge → evaluate → reflect → revise → execute → judge → evaluate → finalize`，说明不是只跑首轮执行。
- 真实在线 25 客户端、25 请求、无预热突发：4 次成功、21 次 429、0 次网络错误；峰值活跃 4、峰值排队 16，`queue_full=5`、`timeout=16`。拒绝均发生在搜索执行前，未继续消耗 LLM 与 ES。
- 自动化测试：并发专项 `8 passed`；报告落盘时完整套件 `558 passed`，当前完整套件为 `568 passed`。

一句话判断：**当前实现已经具备单进程内可观测、可恢复的并发保护，真实在线链路也验证了保护边界；但它仍不是跨 worker、跨主机的生产级全局配额，不能把本轮数据当作在线容量承诺。**

## 2. 本轮变更

| 文件 | 作用 |
|---|---|
| `src/search_agent/core/concurrency.py` | `BoundedRunLimiter`、`RunLease`、`ConcurrencyLimitError`；活跃/排队/超时计数与快照 |
| `src/search_agent/config.py` | 三个 `SEARCH_AGENT_*` 并发环境变量及默认值 |
| `src/search_agent/server.py` | `/search` 与 `/search/stream` 接入闸门；429 响应；SSE 清理；`/healthz` 并发快照 |
| `src/search_agent/web/app.js` | 将 429 显示为“当前搜索服务繁忙”，并按 `Retry-After` 提示用户 |
| `tests/test_concurrency.py` | 8 项并发与 HTTP/SSE 回归测试 |
| `scripts/load_test_concurrency.py` | 标准库 HTTP 压测客户端，采样 `/healthz` 并输出 JSON |
| `scripts/load_test_target.py` | 本地受控慢检索目标，仅用于验证限流边界 |

实现语义：

1. 请求进入搜索入口时先取得 `RunLease`；超过活跃上限后进入有界等待队列。
2. 队列已满时立即返回 `queue_full`；等到超时仍未拿到名额时返回 `timeout`。
3. `Retry-After` 取排队超时的向上取整秒数，至少为 1 秒。
4. 同步请求在 `finally` 中释放租约；SSE 由生成器 `finally` 和 `BackgroundTask` 双重兜底。
5. `/healthz` 额外暴露 `activeRuns` 和 `concurrency`；后者包含 `peakActive`、`rejected`、`timeouts` 等累计量。

## 3. 配置

| 变量 | 默认值 | 本轮验证值 | 说明 |
|---|---:|---:|---|
| `SEARCH_AGENT_MAX_CONCURRENT_RUNS` | 4 | 4 | 同时执行的 Agent 运行数 |
| `SEARCH_AGENT_MAX_QUEUED_RUNS` | 16 | 16 | 等待运行名额的最大请求数 |
| `SEARCH_AGENT_RUN_QUEUE_TIMEOUT_SEC` | 3.0 | 3.0 | 排队等待上限，单位秒 |

限额按 uvicorn 进程计数。部署多个 worker 或多个实例时，每个进程分别拥有一份限额；这不是全局配额。

## 4. 测试环境

### 4.1 离线边界测试

| 项目 | 值 |
|---|---|
| 操作系统 | Microsoft Windows 10.0.26200，x64 |
| 逻辑处理器 | 20 |
| Python | 3.12.7 |
| FastAPI | 0.143.1 |
| Starlette | 1.8.0 |
| Uvicorn | 0.54.0 |
| Pydantic | 2.14.1 |
| pytest | 9.1.1 |
| 服务模式 | `search-agent serve --offline`，单 uvicorn 进程 |
| 检索后端 | `InMemorySearchTool` + `ScriptedLlmClient`（无真实 LLM / ES 网络调用） |
| 查询 | `帮我找 5 年以上 Python 后端工程师，上海优先` |
| 交付人数 | `topK=5` |
| 压测客户端 | 标准库 `urllib`，请求超时 30 秒，预热 1 次 |

### 4.2 真实在线链路

| 项目 | 值 |
|---|---|
| 服务模式 | 在线模式，独立单 uvicorn 进程；单次冒烟使用 `8014`，并发突发使用 `8015` |
| 健康检查 | `mode=online`、`backend=HybridV2SearchService`、`llm=OpenAILlmClient`、`onlineConfigured=true` |
| LLM Gateway | `http://127.0.0.1:4000` |
| 模型别名 | `search-agent-primary`；本地配置以 Qwen 为主模型、DeepSeek 为 fallback |
| 检索后端 | `HybridV2SearchService` |
| Elasticsearch | `http://127.0.0.1:9200` |
| 索引与规模 | `search-agent-hybrid-v2-bench100`，1925 条文档 |
| 查询 | `帮我找 5 年以上 Python 后端工程师，上海优先` |
| 交付人数 | `topK=5` |
| 在线压测客户端 | 标准库 `urllib`，请求超时 240 秒，`warmup=0` |

在线请求通过 Gateway 客户端别名 `search-agent-primary` 路由。本轮没有逐请求审计实际命中的上游模型及 fallback 状态，因此这里只证明请求经过该 Gateway 别名并完成了真实在线链路，不声称 4 个成功请求全部由 Qwen 生成。真实 LLM、Elasticsearch 和网络延迟会随服务状态变化，本轮数据不是生产容量承诺。

## 5. 测试方法

### 5.1 默认演示负载

启动独立实例（不与现有 8000 端口服务冲突）：

```powershell
$env:SEARCH_AGENT_MAX_CONCURRENT_RUNS='4'
$env:SEARCH_AGENT_MAX_QUEUED_RUNS='16'
$env:SEARCH_AGENT_RUN_QUEUE_TIMEOUT_SEC='3'
.\.venv\Scripts\python.exe -m search_agent.cli serve --offline --host 127.0.0.1 --port 8010
```

分别以客户端并发 10、25、50 发送 100 次请求：

```powershell
.\.venv\Scripts\python.exe scripts\load_test_concurrency.py --url http://127.0.0.1:8010 --requests 100 --concurrency 10 --json-out docs\concurrency\c10.json
.\.venv\Scripts\python.exe scripts\load_test_concurrency.py --url http://127.0.0.1:8010 --requests 100 --concurrency 25 --json-out docs\concurrency\c25.json
.\.venv\Scripts\python.exe scripts\load_test_concurrency.py --url http://127.0.0.1:8010 --requests 100 --concurrency 50 --json-out docs\concurrency\c50.json
```

压测脚本每 50 ms 请求 `/healthz` 采样一次；`peakActive` 来自服务端 `BoundedRunLimiter` 的累计峰值，
不依赖采样间隔，因此不会漏掉两次采样之间的短暂峰值。

### 5.2 受控慢检索边界

默认 Demo 的搜索很短，10/25/50 并发并未稳定占满 4 个活跃槽位，因此全部成功不能证明队列上限会生效。
为验证真实 HTTP 路径下的边界，使用本地测试目标给每次 `SearchTool` 调用注入固定 0.5 秒延迟：

```powershell
.\.venv\Scripts\python.exe scripts\load_test_target.py --port 8013 --delay-sec 0.5 --max-concurrent-runs 4 --max-queued-runs 16 --queue-timeout-sec 3.0
.\.venv\Scripts\python.exe scripts\load_test_concurrency.py --url http://127.0.0.1:8013 --requests 100 --concurrency 50 --json-out docs\concurrency\overload-default-c50.json
```

该目标仅改变测试工具延迟，HTTP 入口、`SearchTool` 协议、并发闸门、SSE/同步路径均使用正式实现。

### 5.3 真实在线冒烟与突发

在已配置本地 `.env`（报告中不展示凭据）的前提下，分别启动独立在线实例：

```powershell
$env:SEARCH_AGENT_MAX_CONCURRENT_RUNS='4'
$env:SEARCH_AGENT_MAX_QUEUED_RUNS='16'
$env:SEARCH_AGENT_RUN_QUEUE_TIMEOUT_SEC='3'
.\.venv\Scripts\python.exe -m search_agent.cli serve --host 127.0.0.1 --port 8014
```

先通过同步接口做一次真实冒烟，并读取健康快照：

```powershell
curl.exe -s http://127.0.0.1:8014/healthz
```

冒烟请求使用 `POST /search`，查询为 `帮我找 5 年以上 Python 后端工程师，上海优先`，`topK=5`、`details=true`。
随后用另一个在线实例执行 25 客户端、25 请求、无预热突发；请求超时提高到 240 秒，以覆盖真实 Agent 端到端耗时：

```powershell
.\.venv\Scripts\python.exe -m search_agent.cli serve --host 127.0.0.1 --port 8015
.\.venv\Scripts\python.exe scripts\load_test_concurrency.py --url http://127.0.0.1:8015 --requests 25 --concurrency 25 --timeout 240 --warmup 0 --json-out docs\concurrency\online-c25.json
curl.exe -s http://127.0.0.1:8015/healthz
```

并发突发结束后将健康快照保存为 `docs/concurrency/online-health.json`。单次冒烟与并发突发使用不同的在线进程，健康计数不混合。

## 6. 结果

### 6.1 默认演示负载

| 场景 | 客户端并发 | 请求数 | 成功 | 429 | 总耗时 s | 成功 RPS | p50 ms | p95 ms | p99 ms | peakActive | 最大排队 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 默认 Demo | 10 | 100 | 100 | 0 | 0.851 | 117.55 | 84.5 | 110.4 | 113.7 | 3 | 0 |
| 默认 Demo | 25 | 100 | 100 | 0 | 0.845 | 118.35 | 175.0 | 277.1 | 285.0 | 3 | 0 |
| 默认 Demo | 50 | 100 | 100 | 0 | 0.718 | 139.22 | 324.5 | 369.8 | 374.2 | 3 | 0 |

解释：

- 三组请求均无网络错误、无其他 HTTP 错误。
- Demo 的实际工作是内存检索与脚本化 LLM，单次任务短，未达到 4 个并发槽位的饱和状态；因此没有 429 是预期现象。
- `peakActive=3` 比偶发采样的 `maxActive` 更可靠；它是限流器内部累计量，说明至少出现过 3 个同时活跃运行。

### 6.2 受控慢检索：50 客户端并发

| 指标 | 结果 |
|---|---:|
| 请求数 | 100 |
| 客户端并发 | 50 |
| 成功 | 12 |
| HTTP 429 | 88 |
| 网络错误 | 0 |
| 总耗时 | 3.115 s |
| 成功请求 RPS | 3.85 |
| p50 / p95 / p99 | 2078.2 / 3098.2 / 3099.0 ms |
| 健康快照峰值活跃 | 4 |
| 健康快照峰值排队 | 16 |

服务端在本次运行后的累计快照（包含 1 次预热）：

```text
accepted=13  completed=13  rejected=80  timeouts=8
peakActive=4  maxQueued=16  queueTimeoutSec=3.0  avgQueueWaitMs=939.8
```

`rejected=80` 对应 `queue_full`，`timeouts=8` 对应等待超时；两者合计 88，与客户端收到的 88 个
429 完全一致。`accepted=13` 比成功数 12 多 1，是因为预热请求也占用并完成了一个运行名额。

### 6.3 真实在线单次冒烟

| 指标 | 结果 |
|---|---|
| 请求 | `POST /search`，`topK=5`，`details=true` |
| HTTP 状态 | 200 |
| 端到端耗时 | 62.08 s |
| 交付人数 | 5 |
| 实际状态路径 | `understand → plan → execute → judge → evaluate → reflect → revise → execute → judge → evaluate → finalize` |
| 结束原因 | `finalizeReason=target_reached` |
| 服务健康 | `mode=online`、`backend=HybridV2SearchService`、`llm=OpenAILlmClient` |
| 运行计数 | `accepted=1`、`completed=1`、`peakActive=1` |

这条路径包含第二轮 `execute → judge → evaluate`，说明真实请求实际进入了反思与修订回路，而不是只执行一次查询。62.08 秒是真实模型与检索链路的端到端观察值，不作为 SLO 或容量承诺。

### 6.4 真实在线 25 客户端突发

| 指标 | 结果 |
|---|---:|
| 请求数 / 客户端并发 | 25 / 25 |
| 预热 | 0 |
| 成功 | 4 |
| HTTP 429 | 21 |
| 网络错误 / 其他 HTTP 错误 | 0 / 0 |
| 总耗时 | 43.467 s |
| 总请求 RPS / 成功请求 RPS | 0.58 / 0.09 |
| 成功延迟 min | 26342.6 ms |
| 成功延迟 mean | 32786.5 ms |
| 成功延迟 p50 / p95 / p99 | 30669.2 / 41704.0 / 43112.9 ms |
| 成功延迟 max | 43465.1 ms |
| 健康快照峰值活跃 | 4 |
| 健康快照峰值排队 | 16 |

本次运行后的健康快照：

```text
accepted=4  completed=4  rejected=5  timeouts=16
peakActive=4  maxQueued=16  queueTimeoutSec=3.0  avgQueueWaitMs=0.0
```

21 个 429 的构成为 `queue_full=5`、`timeout=16`。4 个初始请求占满 4 个活跃槽位；16 个请求进入队列后在 3 秒等待窗口内未获准运行；其余 5 个请求因队列已满被立即拒绝。`5 + 16 = 21`，与客户端收到的 429 数量一致，且全部发生在搜索执行前，没有继续消耗 LLM 与 Elasticsearch。`avgQueueWaitMs=0.0` 是因为 4 个获准运行的请求就是初始请求，没有经过排队。

这组数据说明在线模式下闸门保护生效，但也暴露了配置语义：真实运行约 26–62 秒，而默认排队窗口只有 3 秒，4 个槽位占满后，后续请求几乎必然收到 `timeout`。这适合“快速拒绝、保护下游”的演示目标，不等于能吸收 25 个在线并发任务。若产品目标是排队等待而不是快速拒绝，需要调大 `SEARCH_AGENT_RUN_QUEUE_TIMEOUT_SEC`、提高并发上限，或引入异步任务队列。

### 6.5 健康检查字段

`GET /healthz` 的 `concurrency` 快照至少包含：

| 字段 | 含义 |
|---|---|
| `limit` / `maxQueued` / `queueTimeoutSec` | 当前配置 |
| `active` / `queued` | 当前活跃和排队数量 |
| `peakActive` | 进程启动以来的最大活跃运行数 |
| `accepted` / `completed` | 已准入、已完成运行数 |
| `rejected` / `timeouts` | 队列已满拒绝数、排队超时数 |
| `avgQueueWaitMs` | 已准入请求的平均排队等待毫秒数 |

## 7. 429 响应与释放证据

超限时服务端不会进入搜索，而是返回：

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 3
Content-Type: application/json
```

响应体结构（动态快照字段省略）：

```json
{
  "detail": {
    "code": "server_busy",
    "reason": "queue_full",
    "message": "Agent 并发队列已满，请稍后重试。",
    "retryAfterSec": 3,
    "concurrency": {
      "limit": 4,
      "active": 4,
      "queued": 16,
      "maxQueued": 16
    }
  }
}
```

自动化测试额外验证：

- `test_search_rejects_with_429_and_releases_runtime_lock`：同步接口返回 429，且拒绝后运行名额回到 0，不遗留运行时切换锁。
- `test_stream_rejects_with_429_when_queue_is_full`：SSE 入口同样返回 429，且不泄漏运行时名额。
- `test_successful_stream_releases_slot_and_stays_streamable`：正常 SSE 流结束后活跃数回到 0。
- `test_limiter_times_out_waiting_for_an_active_slot`：等待超时会区分 `timeout`，不是 `queue_full`。

## 8. 自动化验证

| 检查 | 结果 |
|---|---|
| `pytest -q tests/test_concurrency.py` | `8 passed in 0.86s` |
| `pytest -q` | 验收时 `558 passed`；当前 `568 passed in 16.14s` |
| `ruff check .` | All checks passed |
| `node --check src/search_agent/web/app.js` | 通过 |
| `node --check scripts/generate-architecture-svg.mjs` | 通过 |

完整测试数已同步到 README、架构 SVG 和评测展示中的 `568`。

## 9. 边界与风险

1. **进程内限额**：每个 uvicorn worker 各自计数，不是跨进程全局配额；多 worker 部署时总并发上限会放大。
2. **非持久队列**：排队请求只存在于进程内，进程退出或重启会丢失；没有跨进程重试、死信和公平调度。
3. **在线验证范围**：真实链路只做了 1 次单请求冒烟和 1 次 25 客户端突发，没有做持续高并发、长时间 soak，也没有注入 Gateway 429/5xx、ES 超时/断连等故障。
4. **单机客户端**：压测客户端与服务在同一台 20 逻辑处理器机器上，网络和客户端调度会放大或掩盖延迟。
5. **受控延迟不是产品延迟**：`load_test_target.py` 的 0.5 秒延迟仅用于触发准入边界，不能作为真实业务 latency 指标。
6. **429 是保护性拒绝**：客户端必须遵循 `Retry-After` 退避；不能把 429 当成空结果，也不能立即无界重试。
7. **在线排队窗口需要按业务重新定标**：真实运行约 26–62 秒，默认排队等待只有 3 秒，4 个槽位占满后，排队请求几乎必然超时。这是保护机制，不是吞吐承诺；若要吸收突发，应调整并发/排队配置或改为异步任务。
8. **上游模型路由未逐请求审计**：本轮只确认请求经过 Gateway 别名 `search-agent-primary`，没有记录每次调用实际命中的 Qwen/DeepSeek 及 fallback 状态。

## 10. 什么时候才需要消息队列

本轮不建议引入消息队列，原因是当前目标是单进程演示和面试展示，消息队列不会改善候选人质量，反而会增加
部署、监控、幂等和重试复杂度。

满足以下任一条件时再考虑 Redis Streams、Kafka 或 RabbitMQ 等方案：

- 需要跨多个 uvicorn worker / 多台机器共享全局并发配额；
- 搜索需要异步提交、持久排队、断点恢复、死信队列或定时重试；
- 需要按租户/岗位/优先级做公平调度或削峰；
- 需要把长耗时 Agent 任务与 HTTP 请求生命周期解耦。

即使引入消息队列，仍应保留本轮的进程内闸门作为 worker 的最后一道保护；全局队列负责排队，worker 闸门负责保护本地下游。

## 11. 验收清单

- [x] 并发限流代码与配置已实现。
- [x] 同步 `/search`、SSE `/search/stream` 均接入。
- [x] 429 响应、`Retry-After`、`queue_full` / `timeout` 可区分。
- [x] SSE 正常结束和断连路径均可释放名额。
- [x] `/healthz` 暴露活跃、排队、峰值、拒绝与超时计数。
- [x] 默认 Demo 10/25/50 并发压测完成。
- [x] 受控慢检索 50 并发边界压测完成。
- [x] 真实在线单次搜索冒烟完成，确认走通反思修订回路。
- [x] 真实在线 25 客户端突发完成，429 与健康计数可对账。
- [x] 并发专项测试和完整测试通过。
- [x] 文档、`.env.example`、架构图和测试徽章已同步。
- [ ] Git 提交与推送：按你的验收结果执行，本轮未自动提交。

## 12. 原始数据与复现入口

| 文件 | 说明 |
|---|---|
| `docs/concurrency/c10.json` | 默认 Demo，10 客户端并发，100 请求 |
| `docs/concurrency/c25.json` | 默认 Demo，25 客户端并发，100 请求 |
| `docs/concurrency/c50.json` | 默认 Demo，50 客户端并发，100 请求 |
| `docs/concurrency/overload-default-c50.json` | 受控慢检索，50 客户端并发，100 请求，默认 4/16/3 配置 |
| `docs/concurrency/overload-c10.json` | 受控慢检索，10 客户端并发；1 秒排队窗口，附边界序列 |
| `docs/concurrency/overload-c25.json` | 受控慢检索，25 客户端并发；1 秒排队窗口，附边界序列 |
| `docs/concurrency/overload-c50.json` | 受控慢检索，50 客户端并发；1 秒排队窗口，附边界序列 |
| `docs/concurrency/online-smoke-health.json` | 真实在线单次冒烟后的 `/healthz` 快照 |
| `docs/concurrency/online-c25.json` | 真实在线，25 客户端并发、25 请求、无预热突发 |
| `docs/concurrency/online-health.json` | 真实在线突发后的 `/healthz` 快照 |

压测脚本 `scripts/load_test_concurrency.py` 与受控目标 `scripts/load_test_target.py` 均可直接重跑；
原始 JSON 中的 `healthBefore` / `healthAfter` 保留了每次运行的计数快照，便于复核。
