"""Local-only slow demo target for admission-control load tests.

This command intentionally wraps the in-memory demo tool with a fixed delay so
the API's concurrency gate can be exercised without calling a real LLM or
Elasticsearch. It is a test utility, not a production server entry point.

Example::

    python scripts/load_test_target.py --port 8012 --delay-sec 0.5
"""

from __future__ import annotations

import argparse
import time

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    SearchToolResult,
)
from search_agent.core.concurrency import BoundedRunLimiter
from search_agent.demo import DEMO_DATA
from search_agent.server import create_app
from search_agent.tools.memory import InMemorySearchTool


class DelayedSearchTool:
    """Delegate to the demo tool after a fixed delay."""

    def __init__(self, delay_sec: float) -> None:
        self._inner = InMemorySearchTool(str(DEMO_DATA))
        self._delay_sec = delay_sec

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult:
        time.sleep(self._delay_sec)
        return self._inner.search_with_intent(req)

    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult:
        time.sleep(self._delay_sec)
        return self._inner.rerank_pool(req)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument("--delay-sec", type=float, default=0.5)
    parser.add_argument("--max-concurrent-runs", type=int, default=4)
    parser.add_argument("--max-queued-runs", type=int, default=16)
    parser.add_argument("--queue-timeout-sec", type=float, default=1.0)
    args = parser.parse_args()
    if args.delay_sec < 0:
        parser.error("--delay-sec must be non-negative")

    limiter = BoundedRunLimiter(
        max_concurrent_runs=args.max_concurrent_runs,
        max_queued_runs=args.max_queued_runs,
        queue_timeout_sec=args.queue_timeout_sec,
    )
    app = create_app(
        tool=DelayedSearchTool(args.delay_sec),
        offline=True,
        run_limiter=limiter,
    )

    import uvicorn

    print(
        f"slow target http://{args.host}:{args.port} "
        f"delay={args.delay_sec}s limit={args.max_concurrent_runs} "
        f"queue={args.max_queued_runs} timeout={args.queue_timeout_sec}s",
        flush=True,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
