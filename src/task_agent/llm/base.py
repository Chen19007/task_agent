"""LLM client base types."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..config import Config


@dataclass
class ChatResponse:
    """Chat response."""
    content: str
    reasoning: str = ""


@dataclass
class ChatMessage:
    """Chat message with optional think block."""
    role: str
    content: str
    think: str = ""


class LLMClient(ABC):
    """LLM client interface."""

    def __init__(self, config: Config):
        self.config = config

    @abstractmethod
    def chat(self, messages: list[ChatMessage], max_tokens: int) -> ChatResponse:
        """Chat with LLM."""
        raise NotImplementedError

    @abstractmethod
    def check_connection(self) -> bool:
        """Check whether the service is reachable."""
        raise NotImplementedError


def create_client(config: Config) -> LLMClient:
    """Create an LLM client based on config."""
    api_type = config.api_type.lower()

    if api_type == "ollama":
        from .ollama_client import OllamaClient
        return OllamaClient(config)
    if api_type == "openai":
        from .openai_client import OpenAIClient
        return OpenAIClient(config)
    raise ValueError(f"Unsupported API type: {config.api_type}")
