"""Search tool implementations: offline demo backend + real backends."""

from search_agent.tools.memory import InMemorySearchTool, load_candidates

__all__ = ["InMemorySearchTool", "load_candidates"]
