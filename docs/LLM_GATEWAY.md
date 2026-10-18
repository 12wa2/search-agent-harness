# LLM Gateway

`search-agent gateway` is a thin OpenAI-compatible gateway for the agent. It is
not a replacement for a full model platform; it is the small control plane
that makes one model alias behave predictably under provider failures.

## What it owns

- model aliases such as `search-agent-primary`;
- primary-to-fallback routing;
- per-target timeout and one attempt per target;
- incoming bearer-token authentication;
- request IDs and non-secret audit metadata;
- aggregate token/request accounting;
- pass-through of Chat Completions bodies, including `response_format`.

The agent still owns its per-request budget, cancellation, JSON repair and
stage-level degradation policy. Retrieval failures remain the responsibility
of the `SearchTool`.

## Quick start

Install the optional API dependencies:

```powershell
pip install -e ".[api]"
Copy-Item examples/gateway.example.json gateway.local.json
```

Create the ignored root `.env` file once. `scripts/start-dev.cmd` loads it
automatically; values already present in the environment take precedence:

```powershell
Copy-Item .env.example .env
# Fill PRIMARY_LLM_API_KEY and FALLBACK_LLM_API_KEY in .env

search-agent gateway --config gateway.local.json --host 127.0.0.1 --port 4000
```

The manual `search-agent gateway` command above still reads its keys from the
current process environment. For the one-time local setup, prefer the helper in
[One-command start](#one-command-start), which imports `.env` before launching
the gateway. Manual launches can export the same variables into the current
PowerShell session.

## One-command start

The gateway is a long-running HTTP service; it is not started automatically by
an agent search command. The repository provides two convenience paths:

Docker Compose starts Elasticsearch, the API and the gateway together:

```bash
docker compose up -d --build
```

The `gateway` service uses the tracked `examples/gateway.docker.json`; the
ignored `gateway.local.json` remains the default for the CLI. Upstream keys are
injected from `.env` and are never baked into the image. The service uses
`restart: unless-stopped`.

On Windows without Docker, use the local helper (logs go to `logs/`):

```powershell
.\scripts\start-dev.cmd              # gateway plus API; API starts in quick demo
.\scripts\start-dev.cmd -StartOnline # API starts in full online mode
.\scripts\start-dev.cmd -SkipApi     # gateway only
```

The helper imports the repository-root `.env` on every launch. When at least
one upstream key is present it starts or reuses the gateway, while the Web API
still starts in the quick-demo preset by default. The in-page “完整在线” button
switches the complete preset for subsequent searches: online LLM, Hybrid V2 and
the configured Elasticsearch candidate index. “快速演示” switches back to the
scripted LLM and the 16-candidate in-memory corpus. Without an upstream key the
gateway is skipped and the quick demo still runs.

The `.cmd` wrapper invokes PowerShell with `-ExecutionPolicy Bypass`; to run the
`.ps1` directly, use `powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1`.

Point the agent at the gateway:

```powershell
$env:SEARCH_AGENT_BASE_URL = "http://127.0.0.1:4000/v1"
$env:SEARCH_AGENT_API_KEY = "local-gateway-key"
$env:SEARCH_AGENT_MODEL = "search-agent-primary"
search-agent search "复杂招聘需求" --backend hybrid_v2
```

The gateway exposes:

| Endpoint | Purpose |
|---|---|
| `POST /v1/chat/completions` | OpenAI-compatible non-streaming completion |
| `GET /v1/models` | Configured model aliases |
| `GET /v1/usage` | Aggregate request/token usage (requires bearer token) |
| `GET /healthz` | Liveness and non-secret route summary |

## Configuration

The config file contains no literal secrets by default:

```json
{
  "gateway_api_key_env": "SEARCH_AGENT_GATEWAY_API_KEY",
  "default_model_alias": "search-agent-primary",
  "model_aliases": {
    "search-agent-primary": [
      {
        "name": "primary-openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "PRIMARY_LLM_API_KEY",
        "timeout_sec": 60
      },
      {
        "name": "fallback-qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "api_key_env": "FALLBACK_LLM_API_KEY",
        "timeout_sec": 60
      }
    ]
  }
}
```

Each alias has an ordered target list. The first successful target is returned.
The upstream `model` field is replaced with the target's model; all other
request fields are passed through.

Provider-specific request fields can be pinned per target with `extra_body`.
This keeps a Qwen-only option from leaking to a fallback provider:

```json
{
  "name": "qwen",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "model": "qwen-plus",
  "api_key_env": "PRIMARY_LLM_API_KEY",
  "extra_body": {"enable_thinking": false}
}
```

`extra_body` cannot override `model`, `messages` or `stream`. For direct
OpenAI-compatible calls without the gateway, set
`SEARCH_AGENT_DISABLE_THINKING=1` instead.

## Fallback policy

The gateway tries the next target for:

- timeouts and connection errors;
- `401`, `403`, `404`;
- `408`, `409`, `425`, `429`;
- `500`, `502`, `503`, `504`.

It does **not** retry `400` or `422`, because those usually mean the request
itself is invalid and retrying would multiply cost. Each target is attempted
once per gateway request; the agent's own retry policy can add another layer.

For evaluation runs, pin one model and disable fallback or record fallback
events separately. A silent model switch changes the experiment.

## Token accounting

The gateway reads `usage` from the upstream response and records:

- prompt, completion and total tokens;
- request count, failures, fallbacks and missing-usage count;
- breakdown by alias, resolved model and optional `X-Tenant-Id`.

This ledger is process-local. Production deployments should persist it in
Redis or a database and add per-tenant hard quotas. The agent's `BudgetState`
remains the per-request budget authority.

## Request metadata

Clients may send:

```text
X-Request-Id: trace-id
X-Tenant-Id: company-or-team
```

The gateway returns `X-Request-Id`, `X-Gateway-Alias`, `X-Gateway-Upstream-Model`,
`X-Gateway-Fallback` and `X-Gateway-Attempts`. Prompt and candidate content are
not logged by default.

## Limitations

- Chat Completions only; streaming is intentionally rejected.
- `response_format: json_schema` is passed through, but compatibility depends
  on the upstream provider.
- The usage ledger is not persistent yet.
- Automatic fallback is for provider failures, not semantic-quality failures;
  the agent's judge and stage degradation still need to run.
