"""
Webhook 输出处理

将 Agent 输出发送到飞书等平台
"""

import logging
import queue
import re

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

    def _emit(self, callback_name: str, content: str, output_type: str = "content") -> None:
        """统一输出入口：为消息增加回调标识，便于排查路由。"""
        tagged = f"[{callback_name}] {content}"
        formatted = self.platform.format_output(tagged, output_type)
        self._queue.put(("content", formatted))

    def _summarize_multiline_result(self, result: str, head: int = 8, tail: int = 8) -> str:
        """命令结果摘要：短输出全显，长输出显示前后窗口。"""
        text = (result or "").rstrip("\n")
        if not text.strip():
            return "（无输出）"

        lines = text.splitlines()
        total = len(lines)
        if total <= head + tail:
            return "\n".join(lines)

        omitted = total - head - tail
        first = "\n".join(lines[:head])
        last = "\n".join(lines[-tail:])
        return f"{first}\n...（中间省略 {omitted} 行）...\n{last}"

    def on_think(self, content: str) -> None:
        """LLM 推理内容 - 最简提示"""
        self._emit("on_think", "💭 正在思考...", "content")

    def on_content(self, content: str) -> None:
        """普通文本内容"""
        # 含 <return> 的完整响应会在 on_agent_complete 再输出一次，这里跳过避免重复
        if "<return>" in content and "</return>" in content:
            return
        # 工具标签（ps_call/bash_call/builtin/create_agent）由专门流程处理，避免与授权卡片重复
        if re.search(r"<(ps_call|bash_call|builtin|create_agent)\b", content, re.IGNORECASE):
            return
        self._emit("on_content", content, "content")

    def on_ps_call(self, command: str, index: int, depth_prefix: str) -> None:
        """Shell 命令请求 - 完整显示"""
        prefix = depth_prefix or ""
        cmd_text = f"#{index}\n{prefix}{command}"
        self._emit("on_ps_call", cmd_text, "ps_call")

    def on_ps_call_result(self, result: str, status: str) -> None:
        """命令执行结果 - 摘要显示"""
        summary = self._summarize_multiline_result(result, head=8, tail=8)
        self._emit("on_ps_call_result", f"status={status}\n{summary}", "ps_call_result")

    def on_create_agent(
        self, task: str, depth: int, agent_name: str, context_info: dict
    ) -> None:
        """创建子 Agent - 完整显示"""
        agent_info = f" [{agent_name}]" if agent_name else ""
        text = f"depth={depth}{agent_info}\n{task}"
        self._emit("on_create_agent", text, "create_agent")

    def on_agent_complete(self, summary: str, stats: dict) -> None:
        """Agent 完成 - 输出完整摘要"""
        clean_summary = re.sub(r"</?return>", "", summary or "").strip()
        text = clean_summary or "任务完成"
        self._emit("on_agent_complete", text, "agent_complete")

    def on_depth_limit(self) -> None:
        """达到深度限制 - 最简提示"""
        self._emit("on_depth_limit", "⚠️ 达到深度限制，停止继续下钻", "content")

    def on_quota_limit(self, limit_type: str) -> None:
        """配额限制 - 最简提示"""
        self._emit("on_quota_limit", f"⚠️ 达到配额限制（{limit_type}）", "content")

    def on_wait_input(self) -> None:
        """等待用户输入 - 最简提示"""
        self._emit("on_wait_input", "⏸️ 等待你的下一条输入", "content")

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
