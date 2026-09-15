"""Abstract LLMClient interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMClient(ABC):
    """Base interface for all LLM backends."""

    @abstractmethod
    def complete(self, system_prompt: str, user_message: str) -> str:
        """Return a completion string given a system prompt and user message."""
        ...
