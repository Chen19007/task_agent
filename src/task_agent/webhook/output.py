"""
Webhook 输出处理

将 Agent 输出发送到飞书等平台
"""

import logging
import queue
import re
from typing import Optional

from ..output_handler import OutputHandler
from .platforms.base import Platform

logger = logging.getLogger(__name__)


class WebhookOutput(OutputHandler):
    """
    Webhook 输出处理器

    将 Agent 输出事件缓存到队列，然后批量发送到平台
    """

    def __init__(self, platform: Platform, chat_id: str):
        """
        初始化 Webhook 输出处理器

        Args:
            platform: 平台实例
            chat_id: 会话 ID
        """
        self.platform = platform
        self.chat_id = chat_id
        self._queue: queue.Queue = queue.Queue()
        self._buffer: list[str] = []  # 缓存待发送的消息
        self._buffer_size = 10  # 每多少条消息发送一次

    def on_think(self, content: str) -> None:
        """LLM 推理内容 - 隐藏"""
        pass  # 飞书不显示思考过程

    def on_content(self, content: str) -> None:
        """普通文本内容"""
        # 含 <return> 的完整响应会在 on_agent_complete 再输出一次，这里跳过避免重复
        if "<return>" in content and "</return>" in content:
            return
        # 工具标签（ps_call/bash_call/builtin/create_agent）由专门流程处理，避免与授权卡片重复
        if re.search(r"<(ps_call|bash_call|builtin|create_agent)\b", content, re.IGNORECASE):
            return
        formatted = self.platform.format_output(content, "content")
        self._queue.put(("content", formatted))

    def on_ps_call(self, command: str, index: int, depth_prefix: str) -> None:
        """Shell 命令请求 - 隐藏"""
        pass  # 飞书不显示命令请求

    def on_ps_call_result(self, result: str, status: str) -> None:
        """命令执行结果 - 隐藏"""
        pass  # 飞书不显示命令结果

    def on_create_agent(
        self, task: str, depth: int, agent_name: str, context_info: dict
    ) -> None:
        """创建子 Agent - 简化版"""
        agent_info = f" [{agent_name}]" if agent_name else ""
        # 限制任务描述长度，避免太长
        task_short = task[:50] + "..." if len(task) > 50 else task
        text = f"子Agent{agent_info}: {task_short}"
        formatted = self.platform.format_output(text, "create_agent")
        self._queue.put(("content", formatted))

    def on_agent_complete(self, summary: str, stats: dict) -> None:
        """Agent 完成"""
        clean_summary = re.sub(r"</?return>", "", summary or "").strip()
        text = clean_summary or "任务完成"
        formatted = self.platform.format_output(text, "agent_complete")
        self._queue.put(("content", formatted))

    def on_depth_limit(self) -> None:
        """达到深度限制 - 隐藏"""
        pass  # 飞书不显示警告

    def on_quota_limit(self, limit_type: str) -> None:
        """配额限制 - 隐藏"""
        pass  # 飞书不显示警告

    def on_wait_input(self) -> None:
        """等待用户输入 - 隐藏"""
        pass  # 飞书不显示等待提示

    def flush(self) -> list[str]:
        """
        获取排队的输出

        Returns:
            输出内容列表
        """
        contents = []
        while True:
            try:
                event_type, content = self._queue.get_nowait()
                contents.append(content)
            except queue.Empty:
                break
        return contents

    def clear(self) -> None:
        """清空队列"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
