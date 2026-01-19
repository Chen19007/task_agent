"""LLM 客户端抽象接口"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generator, Optional

from ..config import Config


@dataclass
class StreamChunk:
    """流式输出块"""
    content: str           # 增量内容（必填）
    reasoning: str = ""    # 推理过程（可选，用于调试）


class LLMClient(ABC):
    """LLM 客户端抽象接口"""

    def __init__(self, config: Config):
        """初始化客户端

        Args:
            config: 配置对象
        """
        self.config = config

    @abstractmethod
    def chat(self, messages: list, max_tokens: int) -> Generator[StreamChunk, None, None]:
        """流式聊天

        Args:
            messages: 消息历史 [{"role": "user", "content": "..."}]
            max_tokens: 最大输出 token 数

        Yields:
            StreamChunk: 流式输出块
        """
        pass

    @abstractmethod
    def check_connection(self) -> bool:
        """检查服务是否可用

        Returns:
            bool: 服务可用返回 True
        """
        pass


def create_client(config: Config) -> LLMClient:
    """根据配置创建 LLM 客户端

    Args:
        config: 配置对象

    Returns:
        LLMClient: 客户端实例
    """
    api_type = config.api_type.lower()

    if api_type == "ollama":
        from .ollama_client import OllamaClient
        return OllamaClient(config)
    elif api_type == "openai":
        from .openai_client import OpenAIClient
        return OpenAIClient(config)
    else:
        raise ValueError(f"不支持的 API 类型: {config.api_type}")
