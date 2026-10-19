# syntax=docker/dockerfile:1

# ---- build stage: resolve every dependency into wheels ----------------------
FROM python:3.12-slim AS build

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip \
 && pip wheel --no-cache-dir --wheel-dir /wheels ".[api,es]"

# ---- runtime stage: wheels only, non-root, healthchecked -------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SEARCH_AGENT_BACKEND=hybrid_v2 \
    SEARCH_AGENT_ES_INDEX=search-agent-demo \
    SEARCH_AGENT_HYBRID_V2_ES_INDEX=search-agent-demo \
    SEARCH_AGENT_PORT=8000

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels "search-agent-harness[api,es]" \
 && rm -rf /wheels \
 && useradd --create-home --uid 10001 agent

USER agent
WORKDIR /home/agent
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
  CMD curl -fsS "http://127.0.0.1:${SEARCH_AGENT_PORT}/healthz" || exit 1

# Seed the 16-candidate Hybrid V2 index, then start the Web API in its quick-demo
# preset. The online preset and gateway are wired by compose; override this
# command or drop --recreate for a persistent production index.
CMD ["sh", "-c", "search-agent hybrid-v2 index --recreate && exec search-agent serve --host 0.0.0.0 --port $SEARCH_AGENT_PORT --backend $SEARCH_AGENT_BACKEND --offline"]
