# Bench V2 100：原生 V2 字段简历基准

本目录提供 100 份可复现的合成候选人简历，覆盖 Hybrid V2 实际检索会使用的主要字段：

- `overview`：姓名、工作年限、年龄、城市、自我评价
- `skills`：技能标签与熟练度
- `workExperiences`：公司、岗位、职责描述、起止时间
- `projectExperiences`：项目名称、项目描述、起止时间
- `educations`：学校、专业、学历、起止时间
- `intention` 相关字段：期望岗位、期望城市、期望薪资、求职状态、工作类型
- `certificates`、`languages`、`tags`

其中 `work_stats`、最高学历和向量字段不手写，而是在导入 Elasticsearch 时由现有索引器从工作经历、教育经历和文本内容派生。

## 文件

- `candidates.jsonl`：100 份 V2 原生字段简历。
- `oracle.json`：生成器蓝图标签，包括真实能力、隐式能力、关键词可见能力和工作年限。
- `../real_ab_queries_100.json`：10 条复杂查询及对应相关候选人 ID。
- `../build_real_ab_bench.py`：确定性生成器（`seed=2026`、`size=100`）。

## 重新生成

```powershell
.\.venv\Scripts\python.exe examples\build_real_ab_bench.py
```

生成过程只由固定随机种子驱动，输出可复现。标签来自生成蓝图，不是对简历文本做关键词回扫，也不是人工标注。

## 导入 Elasticsearch

```powershell
search-agent hybrid-v2 index `
  --data examples\bench_v2_100\candidates.jsonl `
  --es-url http://127.0.0.1:9200 `
  --es-index search-agent-hybrid-v2-bench100 `
  --recreate
```

实验指向同一个索引：

```powershell
$env:SEARCH_AGENT_HYBRID_V2_ES_INDEX = "search-agent-hybrid-v2-bench100"
$env:SEARCH_AGENT_ES_INDEX = "search-agent-hybrid-v2-bench100"
```

## 运行真实 LLM A/B

```powershell
$env:SEARCH_AGENT_BASE_URL = "http://127.0.0.1:4000/v1"
$env:SEARCH_AGENT_API_KEY = "local-gateway-key"
$env:SEARCH_AGENT_MODEL = "search-agent-primary"
$env:SEARCH_AGENT_ES_URL = "http://127.0.0.1:9200"
$env:SEARCH_AGENT_TIME_BUDGET_SEC = "180"
$env:SEARCH_AGENT_MAX_ITERATIONS = "2"
$env:SEARCH_AGENT_MAX_PATHS_PER_ITERATION = "3"
$env:SEARCH_AGENT_TARGET_QUALIFIED = "99"

.\.venv\Scripts\python.exe examples\real_ab_experiment.py `
  --benchmark examples\real_ab_queries_100.json `
  --top-k 5 --workers 4 `
  --json-out docs\real_llm_ab_results_100.json `
  --markdown-out docs\real_llm_ab_results_100.md
```

## 标签边界

`relevant` 来自生成器的潜在能力集合和最低年限规则，是**合成 oracle**，不是人工评审，也不是生产招聘基准。它适合验证 Agent 循环、结构化过滤、BM25 和向量召回之间的相对差异，不应把绝对指标与真实招聘数据直接比较。
