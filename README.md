# search-agent-harness

**把「一次检索」升级为「会反思、会自我修正、有预算纪律」的检索 Agent。**

> 一个可嵌入的八阶段检索 Agent Harness（以人才搜索为端到端示例场景）。
> 纯 Python，唯一运行时依赖是 pydantic；自带离线演示，不需要任何 API Key。

[![CI](https://github.com/12wa2/search-agent-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/12wa2/search-agent-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![pydantic](https://img.shields.io/badge/pydantic-v2-e92063)
![Tests](https://img.shields.io/badge/tests-373-brightgreen)
![Lint](https://img.shields.io/badge/lint-ruff%20clean-261230)
![License](https://img.shields.io/badge/license-MIT-green)
![version](https://img.shields.io/badge/version-v0.1.0-blue)

![Web console](docs/console.png)

> 内置 Web 控制台（零构建，FastAPI 直接托管）：`search-agent serve --offline` → <http://127.0.0.1:8000>。
> 左侧提交招聘需求，中间实时展示八阶段时间线，右侧是交付候选人、命中能力与来源路径。

### 本地查看 Web 控制台

目前**没有托管公网在线 Demo**；仓库提供的是可一键复现的本地演示，默认模式使用脚本化 LLM
和 16 个虚构候选人，**不需要任何 API Key**：

```bash
pip install -e ".[api]"
search-agent serve --offline
# 浏览器打开 http://127.0.0.1:8000
```

页面上的“停止”会断开 SSE 连接并通知后端取消：后端会在当前阶段结束后、下一次 LLM 调用前
或进入下一阶段检索前停止后续工作。已经在途的模型请求不会被强杀，因此界面文案使用“已请求停止”，不宣称立即终止。

### 有边界的请求路由

Web 控制台和两个 `/search/stream` 入口会先经过确定性的 `IntentRouter`，再决定是否启动八阶段 Agent：

| 输入类型 | 路由 | 行为 | LLM / 检索 |
|---|---|---|---|
| “你好” / “Hello” | `GREETING` | 返回能力介绍与示例需求 | 0 次 |
| “你能做什么？” / “怎么使用？” | `HELP` | 返回能力边界与输入建议 | 0 次 |
| 写诗 / 翻译 / 推荐书影音 / 天气 / 通用知识 | `OUT_OF_SCOPE` | 说明只处理招聘搜索，不伪造成 Agent 结果 | 0 次 |
| 招聘需求，或无法确定 | `SEARCH` | 进入 `understand → … → finalize` | 正常预算 |

设计上刻意保守：只有**明确**的问候、帮助问题和无关任务会在 Harness 之前被拦下；其余输入默认交给
Agent，避免把不常见但真实的招聘表达误判成聊天。路由是规则匹配，不调用 LLM，也不读取或写入
多轮会话记忆——这是路由的能力边界，不是通用聊天机器人。

“推荐”单独出现时仍视为模糊搜索信号；只有出现书籍、歌曲、电影等明确对象时才判为范围外。
因此“推荐一本书”与“推荐算法工程师”不会互相误判。

行业词本身不是拒绝依据：只要有招聘或搜索动作，路由就默认交给 Agent；因此
“招聘音乐制作人”“找电影剪辑师”这类不在常见岗位词表中的需求不会被行业词误伤。

SSE 的 `reply` 是终止帧，也是唯一一帧：不会出现 `stage`、`result`、`metrics` 或 `trace`。
`POST /search` 仍保持纯搜索契约，假定调用方传入的就是招聘需求。

## 这是什么

传统检索是「一问一答」：解析 → 搜索 → 排序 → 返回。这个项目把它变成一个**推理循环**：

```text
understand → plan → execute → judge → evaluate → reflect → revise → finalize
     ↑                                                                  │
     └──────────── 反思循环（有预算、可降级、防重复）─────────────────────┘
```

在循环里，Agent 会：

- 把自然语言查询拆成 岗位画像 + 人才画像 + 难度评估（不是拿裸 query 直搜）
- 规划多条**不同召回意图**的搜索路径（硬条件精准匹配 / 语义拓宽 / 来源假设 …）
- 用工具返回的事实（而非模型想象）对候选人做能力级判断
- 每轮反思「缺什么」，修订路径再搜，直到达标或预算耗尽
- 全程可降级：任何一步失败都不会让整个请求崩掉
- 客户端断开后可协作取消：在阶段边界和下一次 LLM 调用前停止，未完成结果不会冒充最终结果

## 设计动机（这个项目从哪来）

起点是一个很具体的现象：招聘 JD 写的是抽象能力（比如「高并发系统设计能力」），简历写的是具体项目（订单系统、支付系统、分布式架构）——同一种能力可能来自完全不同的岗位、行业或产品线；反过来，岗位名称不同，经验却是可迁移的。纯关键词检索在这种情况下天然会漏人。

我起初在做这条链路的上游：把自然语言解析成检索 IR（NL2IR）。解析准确率提上去之后，天花板反而更清楚了：**解析得再准，也只是把一句话翻译成一个查询。**整条链路（自然语言 → IR → ES 召回 → LLM 重排 → 返回）是一次性的——首轮条件过严或者搜索方向选错，系统不会根据结果主动修正，也没有任何一个环节回答「召回到的这些人够不够、有没有覆盖岗位的必备能力」。所以缺的不是检索能力，ES 本身已经很强；缺的是**搜索策略的决策能力**——是「搜什么、从哪搜、够不够、要不要换」这一层。

再看真人猎头是怎么干活的：他不会只敲一次关键词，而是先想「什么样的人可能具备这种能力」，然后从公司、行业、岗位、项目几个不同来源分别去找，看完结果再判断要不要换方向。这层决策恰好是 LLM 擅长的，但它又不能失控——招聘搜索对成本和延迟有要求，不能让它无限反思。

所以我的做法不是「把搜索换成 Agent」，而是在确定性的混合检索之上加一层**受控的推理闭环**：用显式状态机编排八个阶段，LLM 只负责岗位理解、路径规划、能力判断和反思；工程层负责状态流转、预算控制、签名防重和终止条件；底层的 IR 编译、ES 召回、重排全部通过 `SearchTool` 协议复用原有能力，不重写执行层。

一句话总结：原来的系统解决的是「一次查询怎么执行」，我解决的是「这次搜索策略对不对」。

### 为什么把检索层也一起放进仓库

Agent 只负责「理解 → 规划 → 执行 → 判断 → 反思 → 修订」这层编排，真正决定一次搜索能不能找到人的，是下面的检索执行层。因此仓库不只保留 `SearchTool` 接口，还把可独立运行的 **Hybrid V2** 引擎一并开源：IR 编译、硬条件过滤、BM25 + kNN 混合召回、可解释的硬匹配事实和受控重排都在 `src/search_agent/hybrid_v2/` 中。

它连接**使用者自己提供的 Elasticsearch 8**，地址、索引、认证、字段名、embedding、标签字典和重排模型全部由环境变量决定；默认使用确定性本地向量与本地重排，零 API key 也能跑。仓库不包含任何主机、凭据或真实简历，公开演示数据仍全部是虚构候选人。详细接法见 `docs/HYBRID_V2.md`。

因此，「换引擎不改 Agent 代码」不只靠接口声明，而是有四条可运行路径来证明：内存 / SQLite / Elasticsearch 简版 / Hybrid V2，复用同一个 `SearchTool` 契约。

## 30 秒上手

```bash
git clone https://github.com/12wa2/search-agent-harness.git
cd search-agent-harness
pip install -e ".[dev]"

search-agent demo        # 离线演示：脚本化 LLM + 16 个合成候选人，零 API key
search-agent eval        # 离线评测：单趟基线 vs Agent 全循环（确定性、可复现）
search-agent serve --offline   # 可选：HTTP 服务 + Web 控制台 → http://127.0.0.1:8000（需 .[api]）
```

接入真实 LLM（任何 OpenAI 兼容端点：OpenAI / Qwen / DeepSeek / vLLM …）：

```bash
pip install -e ".[openai]"
export SEARCH_AGENT_API_KEY=sk-...                       # Windows: $env:SEARCH_AGENT_API_KEY="sk-..."
export SEARCH_AGENT_BASE_URL=https://api.openai.com/v1   # 可选
search-agent search "招聘推荐算法工程师，要求 5 年以上，有召回排序经验"
```

使用 Qwen 等默认开启思考的模型时，建议关闭隐藏推理并放宽墙钟预算：

```powershell
$env:SEARCH_AGENT_DISABLE_THINKING = "1"
$env:SEARCH_AGENT_TIME_BUDGET_SEC = "180"
```

## 运行效果

`search-agent demo` 的实测输出（本机离线运行、纯 CPU、单进程）：

```text
查询 1: 找有 3 年以上经验、做过高并发系统的后端工程师，最好来自大厂
  [1/8 understand] 岗位=后端工程师  必须能力=['高并发系统设计', '服务端开发']  硬条件=1 条
  [2/8 plan] 生成 2 条搜索路径 → p1: 硬条件精准匹配 | p2: 放宽硬条件的语义拓宽路径
  [3/8 execute] 并行执行 2 条路径，召回 30 人次（p1:rich, p2:rich）
  [4/8 judge] 能力级判断完成，累计合格候选人 12
  [5/8 evaluate] → reflect
  [6/8 reflect] 共性=['普遍命中：服务端开发', '普遍命中：高并发系统设计']  缺口=['高并发系统设计']  新增软信号=2
  [7/8 revise] 画像软信号=6  收敛=False  迭代=1
  [8/8 finalize] 最终 10 人（收敛再搜 14 人次）

  共 10 人，展示前 10 人：
   #  姓名       年限     分数  命中能力                           来源
  ------------------------------------------------------------------------------
   1  赵敏        4   3.01  高并发系统设计、服务端开发         硬条件精准匹配
   2  李娜      6.5   2.75  高并发系统设计、服务端开发         硬条件精准匹配
   3  张伟        8   2.59  高并发系统设计、服务端开发         硬条件精准匹配
   ...
  运行指标: LLM 调用 6 次 · 迭代 1 轮 · 召回完成度 0.65（13/20） · 降级=无
  用时 91 ms
```

（演示数据为 16 个虚构候选人；真实模式下同一循环直接对接你自己的检索后端。）

## 评测：单趟基线 vs 全循环

仓库内置两套**离线确定性**评测（脚本化 LLM + 合成语料，零 API key）：

**口径 ①：成本 / 判定池对比**（16 个演示候选人 · 3 个查询）——循环多花多少钱、把候选池铺多宽：

| 指标（3 个查询合计） | 单趟基线 | Agent 全循环 | 变化 |
|---|---|---|---|
| 判定候选（进入 judge 的池子） | 30 人 | 48 人 | +18（+60%） |
| 平均召回完成度（池/目标） | 0.50 | 0.80 | +0.30 |
| LLM 调用次数 | 6 次 | 15 次 | ×2.5 |
| token 消耗 | ≈12.5k | ≈35.1k | ×2.8 |
| 累计耗时 | 6.5 ms | 13.2 ms | ×2.0 |

> 「判定候选」是**池子规模**不是质量：全循环的语义放宽路径会把候选池铺得更宽，judge 自然判得更多。
> 列表质量请看下面的 oracle 口径与消融表——这里只回答「多花了多少钱」。

**口径 ②：Oracle 基准**（真值来自生成器，不靠 Agent 自评）——把当前实现的**天花板**量出来：

```bash
search-agent eval --generated 120 --seed 7 --markdown   # 种子化合成语料 + oracle 真值
```

120 条合成候选人里，每条简历按能力逐个掷骰：一部分人用技能关键词表达能力，
另一部分人**只用项目语言**表达（「订单交易链路 / 洪峰压测」而不写「高并发」）。
生成器保证「仅语义可见」的人至少缺一项所需能力的证据词，但实现里的词法打分是更粗的
bigram 命中，技能名 / 岗位词里的共享语素（「服务」「开发」「系统」）仍会漏进一部分人——
所以这是一个被部分打穿的盲区、而不是 0 分神话；下表把它量成了具体数字：

| 指标（6 个查询 · micro） | 单趟基线 | Agent 全循环 |
|---|---|---|
| 池召回（探索层：看到过） | 0.70 | 0.76 |
| 召回（交付 top-10：交付到手） | 0.32 | 0.30 |
| 精确率（交付列表） | 0.35 | 0.33 |
| 关键词可见召回 | 0.19 | 0.12 |
| 仅语义可见召回 | 0.36 | 0.36 |
| 仅全循环发现 | — | 平均每条查询多发现 9.0 人，其中合格 0.7 人（7.4%） |

> **怎么读「池召回 → 交付召回」的落差**：池召回衡量探索层——全循环把它从 0.70 抬到 0.76
> （Q4 最典型：0.60 → 1.00，多捞进 4 个合格人），说明循环确实在扩大探索；但交付 top-10
> 反而 0.32 → 0.30——新捞到的人排不进前 10。把交付深度放到 `--top-k 30`，全循环交付召回
> 升到 0.62（同深度基线 0.67，全循环仍略低），说明损失发生在**打分与排序**：最终重排是
> 粗粒度的词法命中（每个能力只记 0/1、并列按 docId 排序），分不出「证据充分」与「偶然
> 共享语素」。三张表一起看：口径 ① 给成本；口径 ② 给「探索有货、排序丢货」；消融表把账
> 算到机制头上。要修的是打分，而不是继续堆路径数——`dense_vector` + RRF 通道已就位，
> 换掉 `tools/embedding.py` 的 `embed_text` 即可。

> **实验设计（为什么基线是单趟 Agent，而不是传统关键词检索）**：基线是同一条 Agent 链路去掉
> reflect + revise 的单趟版本（understand → plan → execute → judge），唯一变量就是反思闭环，
> 收益因此可以归因到具体模块；若直接对比「Agent vs 传统检索」，query 理解 / 多路径 / 语义检索 /
> 反思的贡献会混在一起，效果差异说不清来自哪里。指标定义与「仅全循环发现」一致：
> `A_only = A − B`（A = 全循环、B = 单趟基线），新增相关候选 = `|A_only ∩ R|`，
> 独有候选相关率 = `|A_only ∩ R| / |A_only|`（R = 标注相关集）。

### 消融：每个机制值多少钱

```bash
search-agent eval --generated 120 --seed 7 --ablate                                   # 六行对照表
search-agent eval --generated 120 --seed 7 --ablate --baseline docs/eval_baseline.json # CI 门禁
```

同一语料、同一 seed，每行只关掉一个结构开关（`core/ablation.py`；消融默认把目标合格数抬到语料规模，
强制循环真的迭代——否则首轮就达标，关掉 reflect 也看不出差别）：

| 配置 | 池召回（探索层） | 召回（交付 top-10） | 精确率 | 关键词可见 | 仅语义可见 | 仅该行发现（合格） | LLM 调用 | tokens |
|---|---|---|---|---|---|---|---|---|
| 完整八阶段循环 | 0.76 | 0.41 | 0.45 | 0.31 | 0.44 | 54 人（4 合格） | 29 | ~109.5k |
| 去掉收敛再搜 | 0.76 | 0.30（-0.11） | 0.33 | 0.12 | 0.36 | 54 人（4 合格） | 29 | ~109.5k |
| 去掉签名防重 | 0.76 | 0.41（+0.00） | 0.45 | 0.31 | 0.44 | 54 人（4 合格） | 30 | ~117.1k |
| 只跑一轮（无 reflect / revise） | 0.76 | 0.30（-0.11） | 0.33 | 0.12 | 0.36 | 54 人（4 合格） | 23 | ~95.9k |
| 每轮只规划 1 条路径（无多路径） | 0.70 | 0.42（+0.02） | 0.47 | 0.31 | 0.46 | — | 24 | ~69.9k |
| 单趟基线 | 0.70 | 0.32（-0.09） | 0.35 | 0.19 | 0.36 | — | 12 | ~49.7k |

怎么读这张表（结论不全是好消息——这才是它存在的意义）：

- **探索层和交付层分开读**：池召回 = 循环有没有把对的人带进池子；召回 = 有没有把他们排进
  交付 top-10。两列一起看，才能把「没找到人」和「找到了但没排上来」分开。
- **多路径买的是探索宽度**：关掉多路径，池召回 0.76 → 0.70（少看到 6% 的合格人），交付反而
  略高（0.42 vs 0.41）、成本减半（~70k vs ~110k）——多看到的这批人受限于词法打分排不上来。
  收益要等真实 embedding（语义路径才有差异化召回），这条直接写进 Roadmap。
- **反思 + 收敛再搜买的是交付**：关掉任意一个，池召回不变（0.76，人都看过），交付从 0.41
  掉到 0.30——机制的价值在「把池子里的人推到前 10」，不在扩大探索。
- **签名防重是廉价保险**：关掉后指标不变，但多 1 次 LLM、+7% tokens——它买的是「别重复做同一件事」。
- **可复现**：`docs/eval_baseline.json` 是门禁基线，CI 的 `eval-gate` 作业每次 push 比对——池召回 /
  召回 / 精确率掉超 0.02、成本涨超 10%、或多出一次降级，流水线直接变红。

> 数字来自本机离线运行，毫秒级会有抖动；重新生成：`search-agent eval --markdown`、
> `search-agent eval --generated 120 --seed 7 --markdown`（`--oracle DIR` 可读入
> `search-agent corpus --out DIR` 落盘的固定语料）。目的不是刷分，而是展示
> **效果与成本的定量权衡**与**可回归、去自证的评测入口**。

### 真实模型 + Hybrid V2 的 A/B 复核（100 人合成基准）

离线消融验证机制是否按设计工作；真实模型实验验证换到 Hybrid V2 + 真实 LLM 后结论是否还成立。
`examples/real_ab_experiment.py` 固定同一 Qwen 网关、Elasticsearch 索引、`top-k=5` 和同一标注集：
基线为 `understand → 单路径 → judge → finalize`，Agent 为完整反思循环；两条链路共享模型、检索工具、
语料和 top-k，唯一结构变量是 `reflect + revise`。

正式复核使用索引 `search-agent-hybrid-v2-bench100`、`qwen3.8-flash`（网关别名
`search-agent-primary`）、100 个种子化虚构候选人、10 条复杂 query 和 `synthetic_oracle` 标签。
单次观测结果如下：

| 指标 | 单趟基线 | Agent 反思循环 | 变化 |
|---|---:|---:|---:|
| 平均 recall@5 | 0.392 | 0.473 | +0.081（bootstrap 95% CI -0.031 ~ +0.201） |
| 平均池召回 | 0.735 | 0.937 | +0.202（CI +0.065 ~ +0.351） |
| 平均 precision@5 | 0.740 | 0.840 | +0.100 |
| 新增相关候选 | — | 17 人（1.7/query） | — |
| 新增候选相关率 | — | 0.708 | — |
| LLM 调用 | 20 | 59 | ×3.0 |
| tokens | 148,428 | 446,704 | ×3.0 |
| p50 延迟 | 50.7 s | 139.9 s | ×2.8 |
| 降级次数 | 0 | 2 | — |

结论要分两层读。**探索层**的变化更明确：池召回从 0.735 升到 0.937，bootstrap 区间不跨 0，
说明多路径规划与反思确实扩大了有效候选的覆盖面。**交付层**的平均 recall@5 也上升，但这 10 条 query
的配对区间跨 0，不能据此宣称稳定的统计显著提升；precision@5 从 0.740 升到 0.840，说明新增候选
并非单纯扩大噪声。与此同时，Agent 在 `microservice_backend` 上出现回退，并在 2 条 query 触发
`budget_exceeded`，成本约为主模型的 3 倍。下一轮应优先优化 judge / rerank、收敛搜索和预算分配，
而不是继续盲目增加反思轮数。

> 这是 100 个虚构候选人、10 条 query 的**单次 `synthetic_oracle` 实验**，不是人工标注基准或统计
> 显著性结论，也不能与简历中公司真实简历库的 13.4 个新增相关 / 79.8% 混用；后者不可公开复现。
> 完整逐条结果与环境见 [`docs/real_llm_ab_results_100.md`](docs/real_llm_ab_results_100.md)，
> 标注 query 见 [`examples/real_ab_queries_100.json`](examples/real_ab_queries_100.json)，
> 语料与重建/索引说明见 [`examples/bench100/README.md`](examples/bench100/README.md)。

复现入口：

```powershell
.\.venv\Scripts\python.exe examples\real_ab_experiment.py `
  --benchmark examples\real_ab_queries_100.json --top-k 5 --workers 4
```

## 策略记忆：跨请求复用「哪一类策略有效」

循环解决「这一次怎么搜」，策略记忆回答「这类岗位下次先试哪一招」。每次运行结束后，每个合格候选人
按来源路径归因（每条判断计一次，落在它第一个来源路径的 `origin` 上：`plan` / `loosen` / `tighten` /
`add_path` / `default` / `fallback`，收敛再搜记 `converged`），按查询族累计：

```bash
search-agent demo --repeat 3 --top-k 5     # 同一查询跑 3 次，打印策略记忆快照
```

```text
  策略记忆（查询族 → 策略 → runs / qualified / avg）:
    a847d082898f  plan       runs=3 qualified=15 avg=5.0
```

下一次同类岗位运行时，plan / reflect 的 prompt 多一个字段（没有观测时整键省略）：

```json
"strategyPriors": [{"strategy": "plan", "runs": 2, "avg_qualified": 5.0}]
```

- **族键 = must 能力集哈希**（能力名排序后 sha1 取前 12 位），不是查询原文：近似 JD 共享先验，
  且没有任何查询文本 / 候选人数据离开进程。
- **存储可降级**：配置 `SEARCH_AGENT_REDIS_URL` / `REDIS_URL` 时用 Redis 字段计数器（30 天 TTL，
  跨进程 / 跨重启共享）；否则进程内。任何读写异常只记日志，绝不影响本次搜索。
- **只给先验、不做决策**：prompt 约定样本不足（`runs < 2`）时忽略该行；取舍仍由 LLM 在当次上下文里
  完成，工程层只保证「有证据可参考」，不引入第二个决策者。

离线回归见 `tests/test_experience.py`（24 个用例：族键稳定性、归因口径、Redis 降级、注入链路）。
脚本化 LLM 是规则替身，离线 demo 里会忽略 `strategyPriors`——验证的是归因与注入链路，不是学习效果。

## HTTP 服务（可选）

```bash
pip install -e ".[api]"
search-agent serve --offline    # http://127.0.0.1:8000；浏览器打开即 Web 控制台
```

| 端点 | 说明 |
|---|---|
| `GET /` | 内置 Web 控制台（零构建静态页：输入需求 → 实时八阶段时间线 → 候选人证据） |
| `POST /search` | `SearchRequest` → `SearchResponse`（camelCase JSON；`details=true` 附带 `agentTrace` / `agentMetrics`） |
| `GET /search/stream?query=…&topK=10` | SSE：非搜索请求返回单帧 `reply`；搜索请求逐阶段推送，最后 `result` 帧返回完整结果 |
| `POST /search/stream` | 同一 SSE 流的 JSON body 版本（查询不进 URL / 访问日志）；客户端断开即请求后端取消 |
| `GET /sessions/{queryToken}?offset=5&limit=5` | 分页读取本次 Agent 结果池，不重新检索、不调用 LLM |
| `DELETE /sessions/{queryToken}` | 主动删除分页会话；不调用也会按 1 小时 TTL 过期 |
| `GET /healthz` | 健康检查：版本 / 模式 / 后端与 LLM 类型 |

SSE 事件协议：

| `event` | 何时出现 | 载荷 | 是否终止 |
|---|---|---|---|
| `stage` | 搜索请求每完成一个阶段 | `stage` + `detail` | 否 |
| `reply` | 问候 / 帮助 / 明确无关任务 | `type` / `intent` / `message` / `suggestions` / `searchStarted=false` | 是，且为唯一一帧 |
| `result` | 搜索完成（含协作取消终态） | 完整 `SearchResponse` | 是 |
| `error` | Harness 抛出未预期异常 | `message` | 是 |

```bash
curl -s http://127.0.0.1:8000/search -H "Content-Type: application/json" \
  -d '{"query": "找有 3 年以上经验的后端工程师", "topK": 5}'
```

`create_app(tool=..., llm_client=...)` 同样接受自定义后端，与本地调用共用同一套 `SearchTool` 协议。

### 多轮会话与结果分页

`SearchRequest` 额外支持两个可选字段：

- `conversationId`：省略时后端自动生成并在响应返回；后续请求带上它，`understand` 会读取最近 3 轮
  条件摘要。对话记忆默认最多保留 10 轮、TTL 2 小时，并按 `(companyId, hrId)` 精确校验归属。
- `pageSize`：本次返回的首页大小；不传时保持旧行为，也不创建分页 Session。`topK=20, pageSize=5`
  表示 Agent 最多交付 20 人，本次只返回前 5 人，并通过 `queryToken` 获取后续页。

```bash
# 第一轮：自动创建 conversationId，并返回 queryToken
curl -s -X POST http://127.0.0.1:8000/search -H "Content-Type: application/json" \
  -d '{"query": "找 Python 后端工程师，3 年以上", "topK": 20, "pageSize": 5}'

# 第二轮：复制上一轮的 conversationId，当前输入会继承并细化前一轮条件
curl -s -X POST http://127.0.0.1:8000/search -H "Content-Type: application/json" \
  -d '{"query": "最好有大厂背景", "conversationId": "<上一步返回值>", "topK": 20, "pageSize": 5}'

# 分页：复制 queryToken，不重新运行 Agent
curl -s "http://127.0.0.1:8000/sessions/<queryToken>?offset=5&limit=5"
```

Web 控制台已接入同一协议：同一标签页会复用 `conversationId`，显示历史 query 与当前轮次；
候选人列表底部可翻页，点击“新对话”后下一轮不会继承旧上下文。未配置 Redis 时使用进程内降级，
多进程部署请设置 `SEARCH_AGENT_REDIS_URL` 或 `REDIS_URL`。

演示时可以先输入“找有 3 年以上经验的后端工程师”，随后在同一会话输入“最好有大厂背景”，
观察 `understand` 阶段的“历史上下文 1 轮”和来源假设变化；离线脚本化 LLM 也会对
“最好 / 优先 / 不要”等追问做规则合并。再点击“新对话”重复第二轮，确认不再携带历史。
完整的三段式口播稿和验收点见 `docs/CASE_STUDY.md` 的“多轮会话 + 分页演示”。

## Docker 全栈（API + Elasticsearch + LLM 网关）

一条命令起「HTTP 服务 + Elasticsearch + LLM 网关」：

```bash
docker compose up -d --build            # 首次要构建镜像，之后可去掉 --build
curl -s http://127.0.0.1:8000/healthz   # → backend=ElasticsearchSearchTool, mode=offline
curl -s -X POST http://127.0.0.1:8000/search -H "Content-Type: application/json" -d '{"query": "找有 3 年以上经验的后端工程师", "topK": 5}'
docker compose down                     # 停止（加 -v 一并清掉 ES 数据卷）
```

镜像为多阶段构建（build 阶段产出 wheel，runtime 阶段只装 wheel、以非 root 运行、内置
`HEALTHCHECK`）；compose 里 `api` 服务等 ES 健康检查通过后才启动，启动时把 16 条演示语料
灌进 ES（`--recreate-index`，可用 `command:` 覆盖）。想切真实 LLM，取消 compose 里
`SEARCH_AGENT_API_KEY` 的注释即可——容器会自动从 offline 模式换成 OpenAI 兼容端点。

`gateway` 服务使用仓库内已跟踪的 `examples/gateway.docker.json`；本地
`gateway.local.json` 继续用于命令行启动。上游 key 通过 `.env` 注入，不会进入仓库：

```text
SEARCH_AGENT_GATEWAY_API_KEY=local-gateway-key
PRIMARY_LLM_API_KEY=...
FALLBACK_LLM_API_KEY=...
```

本地不启动 Docker 时，可以用脚本后台启动网关和 API：

```powershell
.\scripts\start-dev.cmd          # gateway + API
.\scripts\start-dev.cmd -SkipApi # 只启动 gateway
```

脚本会把日志写到 `logs/`，并复用已经健康的 4000 / 8000 服务。`.cmd` 包装器使用
PowerShell 的 `-ExecutionPolicy Bypass`；如果直接运行 `.ps1`，请使用
`powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1`。

## 架构

```mermaid
flowchart TD
    Q([自然语言查询]) --> U["1 · understand<br/>岗位画像 + 人才画像 + 难度评估"]
    U -->|需澄清| CL["返回澄清问题"]
    U -->|难度路由| P["2 · plan<br/>多条不同召回意图的搜索路径"]
    P --> E["3 · execute<br/>线程池并行调用 SearchTool"]
    E --> J["4 · judge<br/>能力级判断（基于工具硬事实）"]
    J --> V{"5 · evaluate<br/>达标？预算还有？"}
    V -->|继续| R["6 · reflect<br/>共性 / 缺口 / 来源假设"]
    R --> RV["7 · revise<br/>收紧 / 放宽 / 新增路径（签名防重）"]
    RV --> E
    V -->|终止| F["8 · finalize<br/>收敛再搜 + 全局重排"]
    F --> OUT(["结果 + agentTrace + 指标"])
    U -.->|LLM 失败| D1["降级：基线单趟检索"]
    E -.->|全部路径失败| D1
    J -.->|LLM 失败| D2["降级：跳过判断，用工具事实排序"]
```

- 一条 query 的完整走查（逐阶段真实中间产物 + 面试讲法）：见 `docs/CASE_STUDY.md`
- 状态机、降级矩阵、预算与合并策略细节：见 `docs/ARCHITECTURE.md`
- 关键设计取舍（ADR 风格）：见 `docs/DECISIONS.md`
- 变更记录：见 `CHANGELOG.md`；贡献指南：见 `CONTRIBUTING.md`

## 项目结构

```text
search-agent-harness/
├── src/search_agent/
│   ├── harness.py           # 外层循环：阶段调度、降级应用、trace 组装
│   ├── service.py           # SearchRequest → SearchResponse 适配层
│   ├── runner.py            # 可替换执行后端（默认 LoopRunner）
│   ├── graph.py             # 可选：LangGraphRunner（同一批阶段 → StateGraph，行为等价）
│   ├── server.py            # 可选：FastAPI HTTP 服务（SSE 阶段事件流）
│   ├── intent_router.py     # Harness 之前的确定性请求路由（问候 / 帮助 / 无关任务 / 搜索）
│   ├── stages/              # 8 个阶段（每个都是纯函数，可独立单测）
│   ├── core/                # 签名 / 合并 / 预算 / 降级 / 难度路由 / 指标 / trace / 策略记忆 / 消融开关
│   ├── contract/tool.py     # SearchTool 协议 —— 与检索后端的唯一边界
│   ├── tools/memory.py      # 参考实现：内存检索后端（离线演示用）
│   ├── tools/sqlite_fts.py  # 零依赖 SQLite/FTS5 后端（SQL 编译 where 树）
│   ├── tools/text.py        # 共享语义层：分词 / 匹配 / 打分（多后端同源）
│   ├── tools/elasticsearch.py # 真实 ES 8 后端（DSL 编译 + docker compose）
│   ├── hybrid_v2/          # 独立混合检索：IR 编译 + 硬过滤 + BM25/kNN + 重排
│   ├── llm/                 # LLM 客户端（OpenAI 协议）+ 各阶段 prompt
│   ├── gateway/             # 可选 OpenAI-compatible LLM 网关：别名 / 降级 / 用量
│   ├── models/              # 意图 IR / 推理工件 / API 模型（pydantic v2）
│   ├── demo.py              # 离线脚本化 LLM + 合成候选人数据
│   ├── eval.py              # 离线评测：单趟基线 vs 全循环（效果 / 成本对比）
│   ├── corpus.py            # 合成语料生成器：种子化 + 生成器真值（oracle 标注）
│   ├── oracle.py            # 去自证指标：交付列表打分 / 仅全循环发现增量
│   ├── tools/embedding.py   # 确定性哈希向量（换真实模型只改这一个函数）
│   ├── web/                 # 内置 Web 控制台（HTML/CSS/JS，零构建；FastAPI 托管 GET /）
│   └── cli.py               # 命令行入口：demo / corpus / search / eval / serve
├── scripts/                 # Windows 本地开发：start-dev.ps1（gateway + API）
├── tests/                   # pytest suite（离线 + 可选 ES 集成）
├── examples/                # custom backend · ES 端到端 · 混合召回 · gateway*.json
├── docs/                    # 架构 / Hybrid V2 / 决策 / 案例 / LLM 网关 / 评测基线
├── Dockerfile               # 多阶段镜像：wheel 构建 → 非 root 运行 + HEALTHCHECK
└── docker-compose.yml       # ES 8 + API + LLM gateway（docker compose up -d --build）
```

### 混合召回通道（BM25 + 向量 + RRF）

真实系统的召回层通常做「关键词 + 向量」双通道再融合，这里把同一套漏斗也做进了 ES 后端：

```bash
python examples/es_hybrid_recall.py        # BM25 / kNN / RRF 三列对比
```

实测输出（同一个查询）：

```text
[BM25 关键词通道 (operator=and)] 0 条
  （无命中）

[kNN 向量通道 (dense_vector + cosine)] 5 条
  张伟     c001  score=0.7024
  许诺     c016  score=0.6833

[RRF 混合召回 (rank_constant=60)] 5 条
  张伟     c001  rrf=0.0164  bm25=-  knn=1
  许诺     c016  rrf=0.0161  bm25=-  knn=2
```

长查询在 AND 语义的关键词通道下 0 命中、向量通道仍能召回——这正是混合召回存在的理由。
向量由 `tools/embedding.py` 的确定性哈希向量生成（不下载模型、CI 里可复现）；换成真实
embedding 模型只需替换 `embed_text` 并重建索引，索引映射 / kNN 检索 / RRF 融合都不用动。
Agent 主循环默认仍走「引擎侧过滤 + 共享语义层打分」，所以三后端逐位一致的测试不受影响。

## 接入你自己的检索后端

Harness 与检索后端之间只有一个协议边界：

```python
class SearchTool(Protocol):
    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult: ...
    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult: ...
```

实现这两个方法（ES / 向量库 / 自有检索服务皆可），组装
`SearchAgentService(tool=..., llm_client=...)` 即可。参考实现见
`src/search_agent/tools/memory.py`，最小示例见 `examples/custom_backend.py`。
此外仓库内置四个后端：内存（`tools/memory.py`）、SQLite/FTS5（`tools/sqlite_fts.py`，
仅标准库）、Elasticsearch 8 简版（`tools/elasticsearch.py`）与独立的 Hybrid V2
（`hybrid_v2/`，硬过滤 + BM25/kNN + 可解释重排）。前三个在同一演示语料上结果逐位一致
（`tests/test_sqlite_fts.py`、`tests/test_elasticsearch_backend.py`）；Hybrid V2 使用
自己的索引契约，见 `docs/HYBRID_V2.md`。

## Hybrid V2：可独立运行的混合检索引擎

Hybrid V2 是仓库内可独立运行的检索引擎，可以直接连接你自己的 Elasticsearch 8：

```powershell
pip install -e ".[es]"
docker compose up -d elasticsearch
$env:SEARCH_AGENT_HYBRID_V2_ES_URL = "http://127.0.0.1:9200"
$env:SEARCH_AGENT_HYBRID_V2_ES_INDEX = "candidate"
search-agent hybrid-v2 index --recreate
search-agent hybrid-v2 doctor
search-agent search "找 3 年以上、做过高并发系统的后端工程师" `
  --backend hybrid_v2 --offline --top-k 5
python examples/hybrid_v2_end_to_end.py
```

`hybrid-v2 index` 会创建与查询编译器匹配的 ES 8 mapping（索引型 `dense_vector`、CJK 文本分析、
keyword 子字段、`work_stats`、向量字段），并批量导入 demo 语料；`doctor` 检查集群、索引、
字段、向量维度和文档数。配置了 ES 地址后，`hybrid_v2` 会成为默认检索后端；没有配置时仍
默认 `memory`，公开 demo 不需要任何服务。它保留 NL2IR 硬条件编译、工作年限/空窗/司龄规则、
O*NET 本地岗位别名、BM25 + 多字段 kNN 和可解释的 `hardMatches`；不包含图扩展、托管词典或
外部存储适配器。索引字段、认证、embedding 与重排配置见 `docs/HYBRID_V2.md`。

## 接入真实后端（Elasticsearch）

仓库自带可直接运行的 Elasticsearch 8 后端（`tools/elasticsearch.py`），与内存参考后端在同一语料上结果逐位一致：

```bash
docker compose up -d elasticsearch   # 单节点 ES 8.15：关闭安全认证，512m 堆
pip install -e ".[es]"
python examples/es_end_to_end.py     # 建索引 → 跑完整 Agent 循环 → 与内存后端逐位比对
```

`WhereNode` 树会先编译成真实的 ES DSL（`terms` / `range` / `bool.must_not` / `minimum_should_match`），`details=True` 时该 DSL 原样出现在 `es_query` 里；引擎侧过滤后用共享语义层复检与打分。集成测试未检测到 ES 时自动跳过（`SEARCH_AGENT_ES_URL` 可覆盖默认地址）：

```bash
pytest -q -m es
```

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `SEARCH_AGENT_API_KEY` / `OPENAI_API_KEY` | — | 真实模式必需 |
| `SEARCH_AGENT_BASE_URL` / `OPENAI_BASE_URL` | 官方端点 | 任意 OpenAI 兼容端点 |
| `SEARCH_AGENT_MODEL` | `gpt-4o-mini` | 模型名 |
| `SEARCH_AGENT_MAX_ITERATIONS` | `3` | 反思循环最大迭代数 |
| `SEARCH_AGENT_MAX_PATHS_PER_ITERATION` | `4` | 每轮最大搜索路径数 |
| `SEARCH_AGENT_TARGET_QUALIFIED` | `max(top_k*2, 20)` | 合格候选人目标数 |
| `SEARCH_AGENT_TIME_BUDGET_SEC` | `12.0` | 单次请求墙钟预算（秒） |
| `SEARCH_AGENT_LLM_CALL_BUDGET` | `40` | 单次请求 LLM 调用上限 |
| `SEARCH_AGENT_TOKEN_BUDGET` | `0` | 单次请求 token 上限；`0` = 只记账不设限，命中即 `token_budget` 终止 |
| `SEARCH_AGENT_RECALL_POOL_SIZE` | `max(top_k*3, 30)` | 每路径召回池大小（池内全量 judge，最终交付 top_k） |
| `SEARCH_AGENT_LLM_RETRIES` | `2` | 瞬时错误重试次数（超时 / 429 / 5xx，指数退避 + 抖动） |
| `SEARCH_AGENT_LLM_JSON_REPAIRS` | `1` | 非法 JSON 的修复重试轮数（回传模型输出要求重写） |
| `SEARCH_AGENT_LLM_BACKOFF_SEC` | `0.5` | 退避基数（秒，逐次翻倍） |
| `SEARCH_AGENT_LLM_TIMEOUT_SEC` | `60` | 单次 LLM 请求超时（秒） |
| `SEARCH_AGENT_DISABLE_THINKING` | `0` | `1` 时发送 `enable_thinking=false`，适用于 Qwen 等思考型模型 |
| `SEARCH_AGENT_REDIS_URL` / `REDIS_URL` | — | 可选：会话记忆 / 结果分页 / 策略记忆（跨进程共享） |
| `SEARCH_AGENT_ES_URL` | `http://127.0.0.1:9200` | Elasticsearch 地址；同时启用 Hybrid V2 默认选择 |
| `SEARCH_AGENT_HYBRID_V2_ES_URL` / `_ES_INDEX` | — / `candidate` | Hybrid V2 专用地址与索引 |
| `SEARCH_AGENT_HYBRID_V2_VECTOR_MODE` | `flat` | `flat`（默认兼容路径）或 `nested` 向量字段布局 |
| `SEARCH_AGENT_HYBRID_V2_RERANK_PROVIDER` | `local` | 本地确定性重排，或 `openai` 兼容重排 |
| `SEARCH_AGENT_HYBRID_V2_TAG_DICT` | — | 可选的本地标签 name→id JSON 文件 |

## LLM 网关（可选）

仓库新增了一个薄的 OpenAI-compatible 网关，用于统一模型别名、主备降级、Bearer
鉴权、超时控制和 token 使用统计：

```powershell
pip install -e ".[api]"
Copy-Item examples/gateway.example.json gateway.local.json
$env:SEARCH_AGENT_GATEWAY_API_KEY = "local-gateway-key"
$env:PRIMARY_LLM_API_KEY = "sk-..."
$env:FALLBACK_LLM_API_KEY = "..."
search-agent gateway --config gateway.local.json --port 4000
```

网关是独立的长驻服务，不会随 `search-agent search` 自动拉起。希望少手动启动时，用
`docker compose up -d gateway`（带 `restart: unless-stopped`），或在 Windows 本地执行
`.\scripts\start-dev.cmd -SkipApi`。

Agent 侧只需要把地址和模型别名指向网关：

```powershell
$env:SEARCH_AGENT_BASE_URL = "http://127.0.0.1:4000/v1"
$env:SEARCH_AGENT_API_KEY = "local-gateway-key"
$env:SEARCH_AGENT_MODEL = "search-agent-primary"
```

网关只负责 provider 层路由和失败降级；Agent 的 per-request token 预算、取消和
阶段级降级仍然保留。完整配置、接口和限制见 `docs/LLM_GATEWAY.md`。

## Roadmap

- [x] 八阶段循环 + 预算 + 降级矩阵 + 并行执行
- [x] 离线演示（脚本化 LLM + 内存后端），零 API key 可跑
- [x] 373 个离线测试通过（另有 24 个可选 ES 集成测试）；ruff 全绿；GitHub Actions CI（ruff + pytest + ES + wheel 资源校验）
- [x] LangGraphRunner：把同一批阶段编译成 StateGraph，与默认循环行为等价（可选 extra，见 `graph.py`）
- [x] FastAPI 服务（HTTP + SSE 阶段事件流）：`search-agent serve`（`server.py`，可选 extra）
- [x] Web 控制台：零构建单页（八阶段时间线 / 候选人匹配证据 / metrics / trace），`GET /` 直接托管，
      截图 `docs/console.png`；SSE 支持 `POST /search/stream`（查询不进 URL），客户端断开触发阶段边界取消。
- [x] 轻量多轮会话（结构化摘要、最近 3 轮注入、租户隔离）与结果池分页（`/sessions/{queryToken}`，
      翻页不重新运行 Agent）；Web 控制台支持续接会话、新对话和上一页 / 下一页。
- [x] 评测脚本：Agent 全循环 vs 单趟 baseline 的效果 / 成本对比（`search-agent eval`）
- [x] SQLite/FTS5 后端（`tools/sqlite_fts.py`，标准库）：where 树编译 SQL + 跨后端一致性测试。
- [x] Elasticsearch 真实后端 + `docker compose` 端到端示例（ES DSL 编译，`examples/es_end_to_end.py`）。
- [x] 独立 Hybrid V2 引擎：IR 硬条件编译、BM25 + 多字段 kNN、可解释 hardMatches、
      本地/可选 LLM 分片重排；使用用户自己的 ES 与环境变量，无内部依赖（`docs/HYBRID_V2.md`）。
- [x] Hybrid V2 索引生命周期：内置 mapping、JSONL/JSON 批量导入、`doctor` 自检和真实 ES 端到端示例。
- [x] 向量通道：`dense_vector` + kNN，与 BM25 做 RRF 混合召回（`examples/es_hybrid_recall.py`）。
- [x] Docker 全栈：多阶段镜像 + compose（`api` 等待 ES 健康后启动，自动灌入演示语料）。
- [x] Oracle 基准：种子化合成语料 + 生成器真值，去自证评测（`search-agent eval --generated / --oracle`）。
- [x] 100 人真实模型 A/B 复核：Qwen + Hybrid V2 + ES、10 条复杂 query、逐 query trace / 成本 /
      bootstrap 区间与失败分析（`examples/bench100/`、`docs/real_llm_ab_results_100.md`）。
- [x] 宽召回池语义：每路径召回 `max(top_k*3, 30)`（`SEARCH_AGENT_RECALL_POOL_SIZE`），judge 全量池、交付 top_k。
- [x] 策略记忆：按查询族累计「哪一类策略有效」，并作为先验注入 plan / reflect（`core/experience.py`，Redis 可选）。
- [x] 消融实验 + 评测门禁：`eval --ablate` 单开关对照表；`docs/eval_baseline.json` 作为 CI 基线，回归即红。
- [x] 能力感知请求路由：问候 / 帮助 / 明确无关任务在 Harness 之前返回单帧 `reply`（零 LLM、零检索），
      模糊输入默认走搜索；前端展示能力卡片与可填入的推荐需求。
- [ ] 真实 embedding 接入：跨越「项目语言 ↔ 能力词」表述差，修掉 Oracle 基准量出的天花板。
- [ ] 演示 GIF：录制脚本 `docs/demo.tape` 已就绪——装 [vhs](https://github.com/charmbracelet/vhs) 后跑 `vhs docs/demo.tape`（约 40 秒：demo 时间线 + 消融表），产物 `docs/demo.gif`
- [ ] 完整英文 README（现为 README 内折叠版）

## 诚实声明

- 仓库内所有公开演示数据均为虚构：16 人离线 demo（`c001`–`c016`）与 100 人真实模型基准
  （`g001`–`g100`）。其中的公司 / 院校名仅作为行业背景，与真实个人或真实简历库无关。
- 仓库内置内存 / SQLite / Elasticsearch 简版 / Hybrid V2 四条检索路径；前两条零依赖，后两条需要使用者自己的 Elasticsearch 8。
- 未在生产环境部署过；离线 demo 的耗时来自本机纯 CPU 单进程，真实模型 A/B 的延迟来自本机到模型网关
  与 Elasticsearch 的观测，均不代表生产 SLA。
- 简历 / 汇报口径里的「平均每条 Query 新增 13.4 个相关候选、独有候选相关率 79.8%」来自一次
  **非公开评测**，不在本仓库复现；仓库不包含该评测数据或执行环境。该对照是一次**消融实验**：基线 = 同一套
  understand → plan → execute → judge 的单趟链路，唯一变量是关掉 reflect + revise，因此新增收益可归因到
  反思闭环；指标口径为 `A_only = A − B`、新增相关候选 = `|A_only ∩ R|` 的均值、独有候选相关率 =
  `|A_only ∩ R| / |A_only|`（R = 人工标注相关集）。
- 本仓库用**同一套消融设计、同一对指标定义**做了两级公开复核：离线规则替身基准给出 9.0 人 / 7.4%，
  100 人真实模型基准给出 1.7 人/query（合计 17 人）/ 70.8%。两组数字都来自合成语料：前者用于量出实现
  天花板，后者用于验证真实 LLM + Hybrid V2 下的链路行为；它们都不是公司数据复现，也不能与 13.4 / 79.8%
  直接对比。

<details>
<summary>English</summary>

An eight-stage reflective retrieval agent harness for talent search:
`understand → plan → execute → judge → evaluate → reflect → revise → finalize`.

Offline runnable without any API key:

```bash
pip install -e ".[dev]"
search-agent demo     # offline showcase: scripted LLM + synthetic candidates
search-agent demo --repeat 3   # strategy memory: same query x3, then priors
search-agent eval     # single-pass baseline vs the full loop (effect vs cost)
search-agent eval --generated 120 --seed 7   # oracle-labeled benchmark (no self-grading)
search-agent serve --offline   # HTTP API + zero-build web console on :8000
```

The streaming endpoints route greetings, help questions and clearly unrelated
tasks to a single terminal `reply` frame before the Harness starts: zero LLM
calls, zero retrieval calls. Ambiguous inputs deliberately fall through to the
search Agent so an unusual recruiting query is never rejected by keyword rules.

See `docs/ARCHITECTURE.md` for the state machine, budget and degradation
design, and `docs/DECISIONS.md` for the ADR-style trade-offs.

Design rationale: the missing piece is not retrieval — Elasticsearch is already
strong — but the search-policy layer that decides what to search, where, whether
it is enough, and when to change direction. This harness adds a budget-governed
reflective loop on top of a deterministic hybrid retrieval backend, behind a
two-method `SearchTool` protocol. A standalone Hybrid V2 retrieval engine is
included and connects to an operator-provided Elasticsearch 8 cluster.

> Honesty note: no production data or credentials are included; the demo
> ships 16 fictional candidates and runs without any API key.
> An optional reproducible real-LLM benchmark ships 100 synthetic candidates
> and 10 complex queries; its `synthetic_oracle` results are reported separately
> and must not be compared with company metrics.

</details>

## License

[MIT](LICENSE)
