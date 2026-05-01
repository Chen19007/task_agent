"""OpenAI API 客户端"""

import json
import time

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

        retryable_codes = {429, 502, 503}
        for attempt in range(3):
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=self.config.timeout)
                if response.status_code == 200:
                    data = response.json()
                    choices = data.get("choices", [])
                    if not choices:
                        return ChatResponse(content="")
                    message = choices[0].get("message", {})
                    content = message.get("content", "")
                    reasoning = message.get("reasoning_content", "")
                    return ChatResponse(content=content, reasoning=reasoning)

                # 可重试的状态码：429/502/503/5xx
                if response.status_code in retryable_codes or (500 <= response.status_code < 600):
                    if attempt < 2:
                        delay = 2 ** attempt
                        time.sleep(delay)
                        continue
                    error_detail = response.text[:500] if response.text else str(response.status_code)
                    raise RuntimeError(
                        f"OpenAI API 请求失败 (已重试3次): {response.status_code}\n响应内容: {error_detail}"
                    )

                # 不可重试的错误：400/401/404 等
                error_detail = response.text[:500] if response.text else str(response.status_code)
                raise RuntimeError(f"OpenAI API 请求失败: {response.status_code}\n响应内容: {error_detail}")

            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt < 2:
                    delay = 2 ** attempt
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"OpenAI API 连接失败 (已重试3次): {e}")
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
    def list_models(self) -> list[str]:
        """获取可用的模型列表

        Returns:
            list[str]: 可用模型名称列表
        """
        try:
            url = f"{self.config.openai_base_url}/models"
            headers = {"Authorization": f"Bearer {self.config.openai_api_key}"}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            models = data.get("data", [])
            # 过滤出支持的模型（可以根据需要调整过滤条件）
            model_list = [m.get("id", "") for m in models if m.get("id")]
            return model_list
        except Exception as e:
            print(f"获取模型列表失败: {e}")
            return []
