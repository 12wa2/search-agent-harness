# Bench100: synthetic resume search benchmark

This directory contains the reproducible 100-candidate corpus used by the
optional real-LLM A/B experiment. It is safe to publish because every resume is
generated; no company data or personal data is included.

## Files

- `candidates.jsonl`: 100 synthetic candidate resumes.
- `oracle.json`: generator ground truth (abilities, implicit-expression labels,
  visible abilities, years) for the same documents.
- `../real_ab_queries_100.json`: 10 complex-query definitions with relevant
  document ids and query metadata.
- `../build_real_ab_bench.py`: deterministic generator (`seed=2026`, `size=100`).

## Rebuild

```powershell
.\.venv\Scripts\python.exe examples\build_real_ab_bench.py
```

## Index into Elasticsearch

```powershell
search-agent hybrid-v2 index `
  --data examples\bench100\candidates.jsonl `
  --es-url http://127.0.0.1:9200 `
  --es-index search-agent-hybrid-v2-bench100 `
  --recreate
```

Point the experiment at the same index:

```powershell
$env:SEARCH_AGENT_HYBRID_V2_ES_INDEX = "search-agent-hybrid-v2-bench100"
$env:SEARCH_AGENT_ES_INDEX = "search-agent-hybrid-v2-bench100"
```

## Run the real-LLM A/B experiment

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

Each worker owns isolated LLM and tool clients; output remains ordered by the
benchmark query list. The published run is stored in
`docs/real_llm_ab_results_100.{json,md}`.

## Label boundary

`relevant` is derived from the generator's latent ability set and minimum-years
rule. It is a **synthetic oracle**, not human annotation and not a production
recruiting benchmark. Use it to test the agent loop and its relative trade-offs;
do not compare its absolute numbers with company metrics.
