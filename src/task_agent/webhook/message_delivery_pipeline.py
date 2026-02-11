"""Webhook 消息发送管线。"""

from __future__ import annotations

import logging
import time
from typing import Callable

logger = logging.getLogger(__name__)


class MessageDeliveryPipeline:
    """统一处理分片、顺序发送与重试。"""

    def __init__(self, max_chars: int = 3000, max_attempts: int = 2, retry_delay: float = 0.3):
        self.max_chars = max_chars
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay

    def _split_text(self, content: str) -> list[str]:
        text = content or ""
        if len(text) <= self.max_chars:
            return [text]
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        for line in text.splitlines(keepends=True):
            if current_len + len(line) <= self.max_chars:
                current.append(line)
                current_len += len(line)
                continue
            if current:
                chunks.append("".join(current).rstrip("\n"))
            if len(line) <= self.max_chars:
                current = [line]
                current_len = len(line)
            else:
                # 超长单行硬切分
                for idx in range(0, len(line), self.max_chars):
                    chunks.append(line[idx : idx + self.max_chars])
                current = []
                current_len = 0
        if current:
            chunks.append("".join(current).rstrip("\n"))
        return chunks or [text]

    def send_text(
        self,
        send_func: Callable[[str], str],
        content: str,
        error_callback: Callable[[Exception], None] | None = None,
    ) -> list[str]:
        message_ids: list[str] = []
        for chunk in self._split_text(content):
            last_exc: Exception | None = None
            sent = False
            for attempt in range(self.max_attempts):
                try:
                    message_id = send_func(chunk)
                    if message_id:
                        message_ids.append(message_id)
                        sent = True
                        break
                    raise RuntimeError("empty_message_id")
                except Exception as exc:  # noqa: PERF203
                    last_exc = exc
                    if attempt + 1 < self.max_attempts:
                        time.sleep(self.retry_delay)
            if not sent and last_exc is not None:
                logger.error(f"消息发送失败: {last_exc}")
                if error_callback is not None:
                    error_callback(last_exc)
        return message_ids

