"""OpenAI API 客户端"""

import json
import sys
from typing import Generator

import requests

from .base import LLMClient, StreamChunk


class OpenAIClient(LLMClient):
    """OpenAI API 客户端"""

    def chat(self, messages: list, max_tokens: int) -> Generator[StreamChunk, None, None]:
        """流式聊天

        Args:
            messages: 消息历史
            max_tokens: 最大输出 token 数

        Yields:
            StreamChunk: 流式输出块
        """
        url = f"{self.config.openai_base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.config.openai_api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "max_completion_tokens": max_tokens,
        }

        try:
            with requests.post(url, json=payload, headers=headers, timeout=self.config.timeout, stream=True) as response:
                response.raise_for_status()

                # OpenAI 使用 SSE 格式 (data: {...}\n\n)
                for line in response.iter_lines():
                    if line:
                        line = line.decode('utf-8')

                        # 跳过空行和 [DONE] 标记
                        if not line or line.startswith(":"):
                            continue

                        # 移除 "data: " 前缀
                        if line.startswith("data: "):
                            line = line[6:]

                        # 检查结束标记
                        if line.strip() == "[DONE]":
                            break

                        try:
                            data = json.loads(line)
                            choices = data.get("choices", [])

                            # 跳过 choices 为空的情况（通常是最后的 usage 数据）
                            if not choices:
                                continue

                            delta = choices[0].get("delta", {})

                            # 处理 content 字段
                            content = delta.get("content", "")

                            # 处理推理内容（某些模型返回 reasoning_content）
                            reasoning = delta.get("reasoning_content", "")

                            if content or reasoning:
                                yield StreamChunk(content=content, reasoning=reasoning)

                        except (json.JSONDecodeError, KeyError, IndexError):
                            # 跳过无法解析的行
                            continue

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
