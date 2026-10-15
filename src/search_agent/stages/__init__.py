"""Agent stages, each a pure-ish function: state in -> state out.

LLM-dependent stages (understand/plan/judge/reflect) take an injectable
``LlmClient``; execute takes the ``SearchTool``. This keeps the LangGraph
compileable and unit-testable with mocks.
"""
