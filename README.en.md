<div align="center">

# Search Agent Harness

**Turn talent search from a one-shot query into a budget-aware search-policy loop.**

Deterministic direct search · Adaptive agentic search for harder requests · Evidence-grounded judgment · End-to-end observability

[简体中文](README.md) · [English](README.en.md)

[![CI](https://github.com/12wa2/search-agent-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/12wa2/search-agent-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![Pydantic](https://img.shields.io/badge/pydantic-v2-e92063)
![Tests](https://img.shields.io/badge/tests-484-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-v0.1.0-blue)

<img src="docs/console.png" alt="Search Agent conversational talent-search UI" width="100%">

</div>

> A reference implementation for talent search: structured requests go directly to retrieval IR; clear requests use a lightweight agent; complex requests enter multi-path planning and reflective revision. It runs offline without API keys and can connect to real LLM endpoints, Hybrid V2, and Elasticsearch 8.

## Why this project

Elasticsearch already knows how to retrieve and rank. The harder problem is the search policy around it:

- How should a natural-language hiring brief become executable retrieval constraints?
- When should the system broaden a path, try another source, or stop?
- Is the model judging evidence from the candidate record, or only keyword similarity?
- Which conversation context should carry over, and which candidates did the recruiter explicitly exclude?
- What does reflection buy in quality, and what does it cost in tokens and latency?

Search Agent Harness adds a controlled policy loop on top of deterministic retrieval. LLMs handle understanding, planning, judgment, and reflection. The engineering layer owns state transitions, budgets, signature deduplication, stopping conditions, cancellation, and degradation. Any retrieval system can plug in through the two-method `SearchTool` protocol.

## Execution model

```text
Natural-language request
        |
        v
IntentRouter --- greeting / identity / help / out-of-scope ---> reply
        | SEARCH
        v
Difficulty routing
        |-- structured --> Hybrid V2 parser -> CandidateSearchIR -> SearchTool
        |                   direct_search -> execute -> finalize
        |
        |-- clear ------> understand -> execute -> judge -> evaluate -> finalize
        |
        '-- complex ----> understand -> plan -> execute -> judge -> evaluate
                           -> reflect -> revise -> finalize

Every run: budget checks · signature deduplication · stage degradation · cancellation · trace
```

| Route | Actual stages | Typical request |
|---|---|---|
| Direct search | `direct_search → execute → finalize` | Structured skills, title, tenure, or city |
| Lightweight agent | `understand → execute → judge → evaluate → finalize` | Clear request that still needs judgment and coverage checks |
| Reflective agent | `understand → plan → execute → judge → evaluate → reflect → revise → finalize` | Open-ended preferences, transferable experience, abstract abilities |

The direct route skips the Agent understanding, planning, judging, and reflection stages, so Agent-orchestration LLM calls are `0`. The Hybrid V2 parser still typically makes one model call and falls back to local rules on failure.

## Quick start

Requires Python 3.12+.

```bash
git clone https://github.com/12wa2/search-agent-harness.git
cd search-agent-harness
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -e ".[api]"
search-agent demo
search-agent serve --offline
```

Open <http://127.0.0.1:8000>. The offline preset uses a scripted LLM and 16 fictional candidates; it sends no external model requests.

For a full online setup:

```powershell
pip install -e ".[api,openai,es,redis]"
docker compose up -d elasticsearch
Copy-Item .env.example .env

# Configure the model endpoint, key, model name, and Elasticsearch settings.
search-agent hybrid-v2 index --recreate
search-agent hybrid-v2 doctor
search-agent serve --backend hybrid_v2
```

A complete Docker stack is also available:

```bash
cp .env.example .env
docker compose up -d --build
```

## What is in the repository

- Adaptive 3 / 5 / 8-stage execution with explicit budgets and degradation.
- Evidence-grounded judging and candidate cards that show matched abilities, gaps, hard constraints, and source paths.
- L1 recent context, L2 conversation archive, and L3 explicit candidate exclusion. Redis is optional.
- Four interchangeable retrieval backends: in-memory, SQLite/FTS5, Elasticsearch, and Hybrid V2.
- A zero-build Web console with an agent workspace and an execution-observability view.
- A streaming SSE API, pageable result pools, runtime preset switching, and cooperative cancellation.
- A thin OpenAI-compatible gateway with model aliases, primary/fallback routing, and usage accounting.

## Evaluation snapshot

All public numbers below come from reproducible synthetic benchmarks. They are engineering measurements, not production claims.

| Benchmark | Single pass | Full agent loop | Cost |
|---|---:|---:|---:|
| Offline ablation · pool recall | 0.70 | 0.76 | 2.4x LLM calls, 2.2x tokens |
| Offline ablation · delivered top-10 recall | 0.32 | **0.41** | same as above |
| Real LLM A/B · pool recall | 0.735 | **0.937** | 3.0x calls, 3.0x tokens |
| Real LLM A/B · recall@5 | 0.392 | **0.473** | 2.8x p50 latency |

The reflective loop clearly improves exploration. In the 10-query real-model run, the bootstrap interval for top-5 recall crosses zero, so the sample does not establish a reliable average delivered-recall gain. Costs are roughly 3x, two runs hit budget limits, and not every query improved.

Detailed tables, query-level results, confidence intervals, failure analysis, and reproduction commands: **[docs/EVALUATION.md](docs/EVALUATION.md)**.

## Bring your own retrieval backend

```python
class SearchTool(Protocol):
    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult: ...
    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult: ...
```

Implement those two methods and pass the tool to `SearchAgentService`. See [`examples/custom_backend.py`](examples/custom_backend.py) for a minimal example.

## HTTP API

```bash
curl -s http://127.0.0.1:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query":"Find backend engineers with 3+ years of high-concurrency systems experience","topK":5}'
```

Key endpoints: `GET /` · `POST /search` · `POST /search/stream` · `GET /conversations` · `GET /conversations/{id}` · `PATCH /conversations/{id}/candidates/{docId}` · `GET /sessions/{queryToken}` · `POST /runtime/mode` · `GET /healthz`.

## Documentation

| Document | What it covers |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | State machine, budgets, degradation, parallel execution, merging, and conversation tiers |
| [Evaluation](docs/EVALUATION.md) | Offline cost/oracle/ablation results, real LLM A/B, confidence intervals, limitations |
| [Hybrid V2](docs/HYBRID_V2.md) | NL2IR parser, IR compilation, Elasticsearch mapping, BM25/kNN, embeddings, reranking |
| [LLM Gateway](docs/LLM_GATEWAY.md) | Model aliases, fallback routing, authentication, timeouts, and usage accounting |
| [Case Study](docs/CASE_STUDY.md) | Stage-by-stage walkthrough of a complete query |
| [Decisions](docs/DECISIONS.md) | ADR-style design trade-offs |
| [Contributing](CONTRIBUTING.md) | Development setup, tests, and extension points |
| [Changelog](CHANGELOG.md) | Implemented capabilities and behavior changes |

## Project status

- The suite contains 484 tests. A local run without Elasticsearch reports `460 passed, 24 skipped`; a dedicated CI job runs the skipped integration cases. CI also runs Ruff, wheel asset verification, an offline demo smoke test, and an evaluation gate.
- The project has not been deployed to production and includes no real resumes, internal hosts, or credentials.
- All public metrics use synthetic data. Do not treat them as production SLAs or recruiting accuracy claims.
- Model quality and latency depend on the provider you connect.

## License

[MIT](LICENSE)
