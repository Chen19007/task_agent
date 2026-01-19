"""Ollama API 客户端"""

import json
import sys
from typing import Generator

import requests

from .base import LLMClient, StreamChunk


class OllamaClient(LLMClient):
    """Ollama API 客户端"""

    def chat(self, messages: list, max_tokens: int) -> Generator[StreamChunk, None, None]:
        """流式聊天

        Args:
            messages: 消息历史
            max_tokens: 最大输出 token 数

        Yields:
            StreamChunk: 流式输出块
        """
        url = f"{self.config.ollama_host}/api/chat"

        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "options": {"num_predict": max_tokens},
        }

        try:
            with requests.post(url, json=payload, timeout=self.config.timeout, stream=True) as response:
                for line in response.iter_lines():
                    if line:
                        data = json.loads(line.decode('utf-8'))
                        message = data.get("message", {})

                        # Ollama/Qwen3 格式
                        content = message.get("content", "")
                        reasoning = message.get("thinking", "")

                        if reasoning:
                            # 推理过程用于调试
                            print(f"\n--- LLM REASONING ---\n{reasoning}\n--- END ---\n", file=sys.stderr)

                        if content:
                            yield StreamChunk(content=content, reasoning=reasoning)

        except Exception as e:
            raise RuntimeError(f"Ollama API 调用失败: {e}")

    def check_connection(self) -> bool:
        """检查 Ollama 服务是否可用

        Returns:
            bool: 服务可用返回 True
        """
        try:
            url = f"{self.config.ollama_host}/api/tags"
            response = requests.get(url, timeout=5)
            return response.ok
        except Exception:
            return False
