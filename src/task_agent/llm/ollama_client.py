"""Ollama API 客户端"""

import json

import requests

from .base import LLMClient, ChatResponse, ChatMessage


class OllamaClient(LLMClient):
    """Ollama API 客户端"""

    def chat(self, messages: list[ChatMessage], max_tokens: int) -> ChatResponse:
        """聊天

        Args:
            messages: 消息历史
            max_tokens: 最大输出 token 数

        Returns:
            ChatResponse: 聊天响应
        """
        url = f"{self.config.ollama_host}/api/chat"

        payload_messages = []
        for msg in messages:
            if msg.content:
                payload_messages.append({"role": msg.role, "content": msg.content})
            if msg.think:
                payload_messages.append({
                    "role": msg.role,
                    "content": f"<think>\n{msg.think}\n</think>"
                })

        payload = {
            "model": self.config.model,
            "messages": payload_messages,
            "stream": False,  # 非流式
            "options": {
                "num_predict": max_tokens,
                "num_ctx": self.config.num_ctx,
            },
        }

        try:
            response = requests.post(url, json=payload, timeout=self.config.timeout)
            response.raise_for_status()
            data = response.json()

            message = data.get("message", {})
            content = message.get("content", "")
            reasoning = message.get("thinking", "")

            return ChatResponse(content=content, reasoning=reasoning)

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

    def list_models(self) -> list[str]:
        """获取可用的模型列表

        Returns:
            list[str]: 可用模型名称列表
        """
        try:
            url = f"{self.config.ollama_host}/api/tags"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            models = data.get("models", [])
            return [m.get("name", "") for m in models if m.get("name")]
        except Exception as e:
            print(f"获取模型列表失败: {e}")
            return []
