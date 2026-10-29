"""Backward-compatible module import; the packaged client lives in mini_agent_sdk."""

from mini_agent_sdk.client import MiniAgentClient, MiniAgentError

__all__ = ["MiniAgentClient", "MiniAgentError"]
