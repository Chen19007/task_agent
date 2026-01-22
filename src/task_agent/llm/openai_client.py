"""OpenAI API 客户端"""

import json

import requests

from .base import LLMClient, ChatResponse, ChatMessage


class OpenAIClient(LLMClient):
    """OpenAI API 客户端"""

    def chat(self, messages: list[ChatMessage], max_tokens: int) -> ChatResponse:
        """聊天

        Args:
            messages: 消息历史
            max_tokens: 最大输出 token 数

        Returns:
            ChatResponse: 聊天响应
        """
        url = f"{self.config.openai_base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.config.openai_api_key}",
            "Content-Type": "application/json",
        }

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
            "max_tokens": max_tokens,  # 使用 max_tokens 兼容更多 API
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=self.config.timeout)
            if response.status_code != 200:
                # 打印详细错误信息
                error_detail = response.text if response.text else str(response.status_code)
                raise RuntimeError(f"OpenAI API 请求失败: {response.status_code} {response.reason}\n响应内容: {error_detail}")
            data = response.json()

            choices = data.get("choices", [])
            if not choices:
                return ChatResponse(content="")

            message = choices[0].get("message", {})
            content = message.get("content", "")
            reasoning = message.get("reasoning_content", "")

            return ChatResponse(content=content, reasoning=reasoning)

        except requests.HTTPError as e:
            raise RuntimeError(f"OpenAI API 请求失败: {e}")
        except Exception as e:
            raise RuntimeError(f"OpenAI API 调用失败: {e}")

    def check_connection(self) -> bool:
        """检查 OpenAI 服务是否可用

        Returns:
            bool: 服务可用返回 True
        """
        try:
            url = f"{self.config.openai_base_url}/models"
            headers = {"Authorization": f"Bearer {self.config.openai_api_key}"}
            response = requests.get(url, headers=headers, timeout=5)
            return response.ok
        except Exception:
            return False
