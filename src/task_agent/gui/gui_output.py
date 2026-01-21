"""GUI 输出实现 - 使用可折叠块

将 Agent 层的结构化输出转换为 Dear PyGui 可折叠块显示。
"""

import queue
import time
from ..output_handler import OutputHandler
from .chat_panel import ChatPanel
from .message_parser import MessageParser


class GUIOutput(OutputHandler):
    """GUI 输出实现 - 使用可折叠块"""

    def __init__(self, chat_panel: ChatPanel):
        """初始化 GUI 输出

        Args:
            chat_panel: ChatPanel 实例
        """
        self.chat_panel = chat_panel
        self._parser = MessageParser()
        self._queue: queue.Queue = queue.Queue()

    def on_think(self, content: str) -> None:
        """显示思考过程（可折叠）"""
        self._queue.put(("think", content))

    def on_content(self, content: str) -> None:
        """显示普通内容"""
        self._queue.put(("content", content))

    def on_ps_call(self, command: str, index: int, depth_prefix: str) -> None:
        """显示命令请求（可折叠）"""
        self._queue.put(("ps_call", (command, index, depth_prefix)))

    def on_ps_call_result(self, result: str, status: str) -> None:
        """显示命令结果（可折叠）"""
        self._queue.put(("ps_call_result", (result, status)))

    def on_create_agent(self, task: str, depth: int, agent_name: str,
                       context_info: dict) -> None:
        """显示子 Agent 创建"""
        self._queue.put(("create_agent", (task, depth, agent_name, context_info)))

    def on_agent_complete(self, summary: str, stats: dict) -> None:
        """显示完成信息"""
        self._queue.put(("agent_complete", (summary, stats)))

    def on_depth_limit(self) -> None:
        """深度限制"""
        self._queue.put(("depth_limit", None))

    def on_quota_limit(self, limit_type: str) -> None:
        """配额限制"""
        self._queue.put(("quota_limit", limit_type))

    def on_wait_input(self) -> None:
        """等待输入"""
        self._queue.put(("wait_input", None))

    def flush(self):
        """在主线程渲染排队的输出"""
        while True:
            try:
                event_type, payload = self._queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "think":
                self.chat_panel.add_collapsible_block("[思考]", payload, collapsed=True)
            elif event_type == "content":
                blocks = self._parser.parse(payload)
                rendered = 0
                for block in blocks:
                    if block.block_type == "text":
                        if block.content.strip():
                            self.chat_panel.add_text(block.content)
                            rendered += 1
                        continue
                    if block.block_type in {"ps_call", "create_agent"}:
                        continue
                    if block.block_type == "return":
                        self.chat_panel.add_collapsible_block("[返回]", block.content, collapsed=True)
                        rendered += 1
                        continue
                    if block.block_type == "ps_call_result":
                        self.chat_panel.add_collapsible_block("[命令结果]", block.content, collapsed=True)
                        rendered += 1
                        continue
                    self.chat_panel.add_text(block.content)
                    rendered += 1
                if rendered == 0 and payload.strip():
                    self.chat_panel.add_text(payload)
            elif event_type == "ps_call":
                command, index, depth_prefix = payload
                if depth_prefix == "":
                    label = f"[命令 #{index}]"
                else:
                    depth_marker = "+" * (len(depth_prefix) - 1)
                    label = f"{depth_marker} [命令 #{index}]"
                self.chat_panel.add_collapsible_block(label, command, collapsed=False)
            elif event_type == "ps_call_result":
                result, status = payload
                if status == "executed":
                    icon = "[OK]"
                elif status == "skip":
                    icon = "[SKIP]"
                else:
                    icon = "[CANCEL]"
                self.chat_panel.add_collapsible_block(f"{icon} 结果", result, collapsed=True)
            elif event_type == "create_agent":
                task, depth, agent_name, context_info = payload
                agent_info = f" [{agent_name}]" if agent_name else ""
                text = (f"\n{'+'*60}\n"
                        f"深度: {depth}/{context_info.get('max_depth', 4)}{agent_info} | 任务: {task}\n"
                        f"{'+'*60}\n")
                self.chat_panel.add_text(text)
            elif event_type == "agent_complete":
                summary, stats = payload
                text = (f"\n{'='*50}\n"
                        f"[任务完成]\n"
                        f"{summary}\n"
                        f"执行命令: {stats['commands']} | 创建子Agent: {stats['sub_agents']}\n")
                self.chat_panel.add_text(text)
            elif event_type == "depth_limit":
                text = f"\n!! [深度限制]\n已达到最大深度，由当前Agent执行\n{'═'*50}\n"
                self.chat_panel.add_text(text)
            elif event_type == "quota_limit":
                limit_type = payload
                if limit_type == "local":
                    text = f"\n!! [本地配额限制]\n当前Agent已用完子Agent配额\n{'═'*50}\n"
                else:
                    text = f"\n!! [全局配额限制]\n整个任务已用完所有子Agent配额\n{'═'*50}\n"
                self.chat_panel.add_text(text)
            elif event_type == "wait_input":
                self.chat_panel.add_text("[等待用户输入]")

    def render_history_content(self, content: str):
        """用于历史消息渲染，展示所有 tool tags"""
        blocks = self._parser.parse(content)
        rendered = 0
        for block in blocks:
            if block.block_type == "text":
                if block.content.strip():
                    self.chat_panel.add_text(block.content)
                    rendered += 1
                continue
            if block.block_type == "return":
                self.chat_panel.add_collapsible_block("[返回]", block.content, collapsed=True)
                rendered += 1
                continue
            if block.block_type == "ps_call":
                self.chat_panel.add_collapsible_block("[命令]", block.content, collapsed=False)
                rendered += 1
                continue
            if block.block_type == "ps_call_result":
                self.chat_panel.add_collapsible_block("[命令结果]", block.content, collapsed=True)
                rendered += 1
                continue
            if block.block_type == "create_agent":
                self.chat_panel.add_collapsible_block("[创建Agent]", block.content, collapsed=True)
                rendered += 1
                continue
            self.chat_panel.add_text(block.content)
            rendered += 1
        if rendered == 0 and content.strip():
            self.chat_panel.add_text(content)
