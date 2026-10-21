<div align="center">

# Search Agent Harness

**Turn talent search from a one-shot query into a budget-aware search-policy loop.**

Deterministic direct search · Adaptive reflection for harder requests · Evidence-grounded judgment · End-to-end observability

[简体中文](README.md) · [English](README.en.md)

[![CI](https://github.com/12wa2/search-agent-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/12wa2/search-agent-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![Pydantic](https://img.shields.io/badge/pydantic-v2-e92063)
![Tests](https://img.shields.io/badge/tests-484-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

<img src="docs/console.png" alt="Search Agent conversational talent-search UI" width="100%">

</div>

> A reference agent for talent search: structured requests use deterministic retrieval, clear requests use a lightweight agent, and complex requests enter multi-path planning, judgment, and reflective revision. It runs without API keys and also supports real LLMs, Hybrid V2, and Elasticsearch 8.

[Quick start](#quick-start) · [Execution model](#execution-model) · [Evaluation](#evaluation-snapshot) · [Core design](#core-design) · [Documentation](#documentation)

---

## Why this project

Elasticsearch already knows how to retrieve and rank. The harder problem is the search policy around it:

- How should a natural-language hiring brief become executable retrieval constraints?
- When a path is not enough, should the system broaden it, change sources, or stop?
- Is the model judging evidence from the candidate record, or only keyword similarity?
- Which conversation context should carry over, and which candidates were explicitly excluded?
- What does reflection buy in quality, and what does it cost in tokens and latency?

Search Agent Harness adds a controlled policy loop on top of deterministic retrieval. LLMs handle understanding, planning, judgment, and reflection. The engineering layer owns state transitions, budgets, signature deduplication, stopping conditions, cancellation, and degradation. Any retrieval system can plug in through the two-method `SearchTool` protocol.

## Highlights

- **Adaptive execution**: structured requests use a 3-stage direct path; clear requests use a 5-stage lightweight agent; complex requests may use the 8-stage reflective loop.
- **Evidence first**: the judge consumes candidate facts returned by the search tool; cards expose matched abilities, gaps, hard constraints, and source paths.
- **Governed loop**: explicit state machine, iteration/path/LLM/wall-clock budgets, signature deduplication, and stage-level degradation.
- **Observable and conversational**: SSE stage events, actual executed stages, raw trace, L1 recent context, L2 conversation archive, and L3 candidate exclusion.
- **Pluggable and reproducible**: in-memory, SQLite/FTS5, Elasticsearch, and Hybrid V2 share one contract; 484 tests and a zero-key demo run locally.

## Execution model

```text
Natural-language request
        |
        v
IntentRouter --- greeting / identity / help / explicit out-of-scope ---> reply
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

The direct route skips the Agent understanding, planning, judging, and reflection stages, so **Agent LLM calls are 0**. The Hybrid V2 parser still typically makes one model call and explicitly falls back to local rules on failure. See [Intent Routing](docs/INTENT_ROUTING.md) for the complete policy.

## Quick start

### Zero-key demo

Requires Python 3.12+.

```bash
git clone https://github.com/12wa2/search-agent-harness.git
cd search-agent-harness
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\activate
pip install -e ".[api]"
search-agent serve --offline
```

macOS / Linux:

```bash
source .venv/bin/activate
pip install -e ".[api]"
search-agent serve --offline
```

Open <http://127.0.0.1:8000>. The quick demo uses a scripted LLM and 16 fictional candidates. It sends no external model requests and does not require Elasticsearch.

Windows can also use the launcher:

```powershell
.\scripts\start-dev.cmd
```

The launcher loads `.env` and prepares the local gateway when a model key is available. The page still starts in quick-demo mode and can switch to the full online preset from the top bar.

### Full online mode

Full online mode needs an OpenAI-compatible model endpoint, Elasticsearch 8, and optionally Redis. Without Redis, conversations and result pagination fall back to process memory.

```powershell
pip install -e ".[api,openai,es,redis]"
docker compose up -d elasticsearch
Copy-Item .env.example .env
# Fill in the model endpoint, key, model name, and Elasticsearch settings.

search-agent hybrid-v2 index --recreate
search-agent hybrid-v2 doctor
.\scripts\start-dev.cmd -StartOnline
```

See **[Deployment](docs/DEPLOYMENT.md)** for the full Docker stack, Redis setup, and troubleshooting. See **[Configuration](docs/CONFIGURATION.md)** for every environment variable.

## Interface

The default view is a conversational agent workspace: recent conversations on the left, requirement parsing and live execution evidence in the center, and the delivered shortlist on the right. The top-bar switch opens the execution-observability view with actual stages, retrieval paths, metrics, and raw trace. Both views share the same SSE run and switching does not rerun the search.

[Delivered candidates](docs/console-run.png) · [Execution observability](docs/console-observe.png)

## Evaluation snapshot

All public numbers below come from reproducible synthetic benchmarks. They are engineering measurements, not production SLAs, recruiting accuracy claims, or company data.

| Benchmark | Single pass | Full agent loop | Cost |
|---|---:|---:|---:|
| Offline ablation · pool recall | 0.70 | 0.76 | LLM calls 12 → 29; tokens 49.7k → 109.5k |
| Offline ablation · delivered top-10 recall | 0.32 | **0.41** | Same as above |
| Real LLM A/B · pool recall | 0.735 | **0.937** | 20 → 59 calls; 148,428 → 446,704 tokens |
| Real LLM A/B · recall@5 | 0.392 | **0.473** | p50 latency 50.7s → 139.9s |
| Real LLM A/B · precision@5 | 0.740 | **0.840** | Same as above |

Key findings:

- The loop clearly widens exploration. The `0.32 → 0.41` delivered top-10 result comes from a forced-iteration ablation; the default target is more conservative.
- In the real-model A/B, the pool-recall interval excludes zero; the recall@5 bootstrap interval crosses zero, so 10 queries do not establish a stable average delivered-recall gain.
- Cost is roughly 3x and not every query improves. The next priority is judge/rerank precision, not adding more paths.

Full tables, confidence intervals, per-query failure analysis, and reproduction commands are in **[Evaluation](docs/EVALUATION.md)**.

## Core design

- **Adaptive harness**: stages are pure functions behind an explicit loop; an optional LangGraph runner compiles the same stages without changing behavior.
- **Budgets and degradation**: the state machine governs iterations, paths, LLM calls, and wall-clock time; stage failures either degrade or terminate with an explainable reason.
- **Conversation tiers**: L1 injects recent structured context, L2 persists the complete conversation, and L3 records viewed or explicitly excluded candidates.
- **Retrieval boundary**: implement the two methods below to connect your own search system without changing Agent stages.

```python
class SearchTool(Protocol):
    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult: ...
    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult: ...
```

See [`examples/custom_backend.py`](examples/custom_backend.py) for a minimal implementation, plus [Architecture](docs/ARCHITECTURE.md) and [Hybrid V2](docs/HYBRID_V2.md) for details.

## Documentation

| Document | What it covers |
|---|---|
| [Intent Routing](docs/INTENT_ROUTING.md) | Five intents, 3/5/8 route selection, and observable fields |
| [Architecture](docs/ARCHITECTURE.md) | State machine, budgets, degradation, merging, and finalize |
| [HTTP API](docs/API.md) | Search, SSE, conversations, candidate exclusion, and pagination |
| [Deployment](docs/DEPLOYMENT.md) | Quick demo, full local online mode, Docker Compose, and troubleshooting |
| [Configuration](docs/CONFIGURATION.md) | Model, gateway, Redis, budgets, ES, and Hybrid V2 variables |
| [Evaluation](docs/EVALUATION.md) | Offline ablation, real LLM A/B, intervals, and limitations |
| [Hybrid V2](docs/HYBRID_V2.md) | NL2IR, ES mapping, BM25/kNN, embeddings, and reranking |
| [LLM Gateway](docs/LLM_GATEWAY.md) | Model aliases, fallback routing, authentication, and usage |
| [Case Study](docs/CASE_STUDY.md) | Stage-by-stage walkthrough of a complete query |
| [Decisions](docs/DECISIONS.md) | ADR-style trade-offs and alternatives |
| [Contributing](CONTRIBUTING.md) | Development setup, tests, and extension points |
| [Changelog](CHANGELOG.md) | Implemented capabilities and behavior changes |

## Quality and scope

- The suite contains 484 tests. A local run without Elasticsearch reports `460 passed, 24 skipped`; CI runs the skipped integration cases in a dedicated job.
- CI covers Ruff, pytest, Web asset validation, an offline demo smoke test, an evaluation gate, and Elasticsearch integration tests.
- The repository is not deployed to production and contains no real resumes, company data, internal hosts, or credentials. Demo data is synthetic.
- Model quality, cost, and latency vary by provider; rerun the evaluation before connecting real data.

## Roadmap

- [ ] Connect real embeddings and quantify semantic-recall gains
- [ ] Improve judge/rerank evidence granularity and fix "found in exploration, lost in ranking"
- [ ] Allocate convergence/re-search budgets by query family to reduce the roughly 3x cost
- [ ] Record a README demo GIF; `docs/demo.tape` is ready

## License

[MIT](LICENSE)
