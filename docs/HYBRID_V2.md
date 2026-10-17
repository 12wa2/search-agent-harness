# Hybrid V2: standalone Elasticsearch retrieval engine

Hybrid V2 is the production-style retrieval backend bundled with this
repository. It is selected automatically when an Elasticsearch endpoint is
configured, or explicitly with `--backend hybrid_v2`.

The engine owns four steps:

1. normalize the agent's IR (work-history facts and position aliases);
2. compile hard conditions to Elasticsearch filters;
3. recall with BM25 plus multi-field kNN;
4. rerank locally, or with bounded OpenAI-compatible shards.

All credentials, field names, catalogs, and service endpoints are supplied by
the operator through environment variables. The repository contains no
credentials, host-specific defaults, or real candidate records.

## Quick start

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

`hybrid-v2 index` creates the bundled mapping and loads `src/search_agent/data/demo_candidates.jsonl`
by default. Use `--data <jsonl-or-json>`, `--company-id`, `--vector-mode`, and
`--embedding-dims` for your own corpus. Keep the embedding provider and dimensions
identical between indexing and query time. The bundled path is validated
against Elasticsearch 8.15.0.

`SEARCH_AGENT_HYBRID_V2_ES_URL` takes precedence over `SEARCH_AGENT_ES_URL`.
When neither variable is set, the default remains the zero-dependency
`memory` backend, so the public demo still runs without infrastructure.

Redis is not required by the retrieval engine. To share conversation or
strategy memory across processes, point `SEARCH_AGENT_REDIS_URL` (or
`REDIS_URL`) at your own Redis instance; without it the harness keeps that
state in-process.

## Index contract

`search-agent hybrid-v2 init-index` generates the Elasticsearch 8 mapping below,
including indexed `dense_vector` fields, CJK text analysis, lower-cased keyword sub-fields, work
statistics, and the selected vector layout. `search-agent hybrid-v2 index`
performs the same setup before bulk loading. The query builder expects these
fields; use `search-agent hybrid-v2 doctor` after any manual mapping change.

| Area | Expected fields | Notes |
|---|---|---|
| identity | `_id`, `candidate_profile_id` | `_id` is returned as `docId` |
| tenant | `company_id`, `entity_join` | configurable; set a field name to empty to disable that filter |
| visibility | `deleted`, `profile_pool_id`, `collaborator_id` | soft-delete and optional permission filters |
| profile | `overview.name`, `overview.work_years`, `overview.age`, `overview.city.name` | candidate summary fields |
| skills | `skills.name` | nested-friendly |
| work | `work_experiences.company`, `work_experiences.job`, `work_experiences.description`, start/end times | hard text filters and BM25 |
| projects | `project_experiences.project_name`, `project_experiences.description` | BM25 and project vectors |
| education | `educations.college.name`, `educations.major.name`, `educations.degree.name`, `educations.degree.code` | degree thresholds use codes |
| intention | `intention.positions`, `intention.industries`, `intention.cities`, `intention.expected_salary`, `intention.work_state` | nested name/code objects are accepted |
| work stats | `work_stats.*` | tenure, gaps, segment counts, role tenure |
| tags | `talent_tags.tag_id`, `tags` | tag dictionary is optional and local |
| sorting | `candidate_authority_time`, `candidate_profile_id` | used when a query has no semantic channel |

A hit may also store the profile as a JSON object or JSON string under
`_source.payload`; the converter merges it into the document before mapping
the public `CandidateItem`.

## Vector modes

Set `SEARCH_AGENT_HYBRID_V2_VECTOR_MODE` to match the index mapping:

- `flat` (default): vectors live at `work_vector`, `project_vector`, and
  `education_vector` on the document. This is the broad-compatibility path used
  by the bundled end-to-end example.
- `nested`: vectors live at `work_segments.vector`,
  `project_segments.vector`, and `education_segments.vector`. Use this only when
  the target ES 8 deployment and query path have been verified with nested kNN;
  `doctor` validates mapping shape and dimensions but cannot prove cluster-level
  nested-kNN support.

`self_evaluation_vector` and `skills_vector` are document-level fields in both
modes. All dense vectors must have the same dimension as the configured
embedding provider.

The default `hash` embedder is deterministic and offline. It is intended for
demos and tests, not for comparing against an index built with a hosted model.
For a real index, set the provider and dimensions that produced the vectors:

```powershell
$env:SEARCH_AGENT_EMBEDDING_PROVIDER = "openai"
$env:SEARCH_AGENT_EMBEDDING_MODEL = "text-embedding-3-small"
$env:SEARCH_AGENT_EMBEDDING_DIMS = "1536"
$env:SEARCH_AGENT_EMBEDDING_API_KEY = "..."
$env:SEARCH_AGENT_EMBEDDING_BASE_URL = "https://api.openai.com/v1"
```

Any OpenAI-compatible embedding endpoint can be used. The API key is read
from the environment and is never written into a trace or response.

## Reranking and safety

The default reranker is local and deterministic:

```text
0.45 * normalized ES score + 0.35 * lexical coverage + 0.20 * hard-match ratio
```

`SEARCH_AGENT_HYBRID_V2_RERANK_PROVIDER=openai` enables sharded LLM reranking.
Shards have a fixed size, a wall-clock timeout, a worker limit, and a
local-score fallback on timeout, invalid JSON, missing candidates, or provider
errors. The model is told to treat candidate profile fields as untrusted data,
not instructions. Full profiles are not written to logs; trace output only
contains counts, timings, and status.

The LLM reranker is optional. It does not change the hard-filter semantics of
the Elasticsearch query.

## Configuration groups

See `.env.example` for the complete list. The most useful knobs are:

| Variable | Default | Purpose |
|---|---|---|
| `SEARCH_AGENT_HYBRID_V2_ES_URL` | unset | enables Hybrid V2 |
| `SEARCH_AGENT_HYBRID_V2_ES_INDEX` | `candidate` | candidate index |
| `SEARCH_AGENT_ES_USERNAME` / `SEARCH_AGENT_ES_PASSWORD` | unset | basic auth |
| `SEARCH_AGENT_ES_API_KEY` | unset | API-key auth |
| `SEARCH_AGENT_ES_VERIFY_CERTS` | `true` | TLS verification |
| `SEARCH_AGENT_HYBRID_V2_TENANT_FIELD` | `company_id` | tenant filter field |
| `SEARCH_AGENT_HYBRID_V2_VECTOR_MODE` | `flat` | vector mapping shape |
| `SEARCH_AGENT_HYBRID_V2_RERANK_PROVIDER` | `local` | local or OpenAI-compatible rerank |
| `SEARCH_AGENT_HYBRID_V2_RERANK_SCAN_CAP` | `100` | maximum pool passed to rerank |
| `SEARCH_AGENT_HYBRID_V2_RERANK_TARGET_N` | `20` | minimum scan target before delivery |
| `SEARCH_AGENT_HYBRID_V2_TAG_DICT` | unset | local JSON name-to-id tag map |

## Scope and limitations

- The engine intentionally excludes graph expansion and vendor-managed catalog
  or storage adapters.
- The public tests use deterministic fake ES responses and the hash embedder;
  they do not claim benchmark performance against a real recruiting corpus.
- A production deployment should put Elasticsearch behind network controls,
  use per-tenant credentials or filters, and add index-level monitoring. The
  engine exposes `health()` for a non-secret cluster check.
- `hybrid-v2 init-index` and `hybrid-v2 index` are operator commands. They can
  create or replace indexes and must not be exposed as public HTTP endpoints.
