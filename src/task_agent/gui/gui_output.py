"""GUI 输出实现 - 使用可折叠块

将 Agent 层的结构化输出转换为 Dear PyGui 可折叠块显示。
"""

import time
from ..output_handler import OutputHandler
from .chat_panel import ChatPanel


class GUIOutput(OutputHandler):
    """GUI 输出实现 - 使用可折叠块"""

    def __init__(self, chat_panel: ChatPanel):
        """初始化 GUI 输出

        Args:
            chat_panel: ChatPanel 实例
        """
        self.chat_panel = chat_panel

    def on_think(self, content: str) -> None:
        """显示思考过程（可折叠）"""
        self.chat_panel.add_collapsible_block("[思考]", content, collapsed=True)

    def on_content(self, content: str) -> None:
        """显示普通内容"""
        self.chat_panel.add_text(content)

    def on_ps_call(self, command: str, index: int, depth_prefix: str) -> None:
        """显示命令请求（可折叠）"""
        # 根据深度前缀生成标签
        if depth_prefix == "":
            label = f"[命令 #{index}]"
        else:
            depth_marker = "+" * (len(depth_prefix) - 1)
            label = f"{depth_marker} [命令 #{index}]"
        self.chat_panel.add_collapsible_block(label, command, collapsed=False)

    def on_ps_call_result(self, result: str, status: str) -> None:
        """显示命令结果（可折叠）"""
        if status == "executed":
            icon = "[OK]"
        elif status == "skip":
            icon = "[SKIP]"
        else:  # rejected
            icon = "[CANCEL]"
        self.chat_panel.add_collapsible_block(f"{icon} 结果", result, collapsed=True)

    def on_create_agent(self, task: str, depth: int, agent_name: str,
                       context_info: dict) -> None:
        """显示子 Agent 创建"""
        agent_info = f" [{agent_name}]" if agent_name else ""
        text = (f"\n{'+'*60}\n"
                f"深度: {depth}/{context_info.get('max_depth', 4)}{agent_info} | 任务: {task}\n"
                f"{'+'*60}\n")
        self.chat_panel.add_text(text)

    def on_agent_complete(self, summary: str, stats: dict) -> None:
        """显示完成信息"""
        text = (f"\n{'='*50}\n"
                f"[任务完成]\n"
                f"{summary}\n"
                f"执行命令: {stats['commands']} | 创建子Agent: {stats['sub_agents']}\n")
        self.chat_panel.add_text(text)

    def on_depth_limit(self) -> None:
        """深度限制"""
        text = f"\n!! [深度限制]\n已达到最大深度，由当前Agent执行\n{'═'*50}\n"
        self.chat_panel.add_text(text)

    def on_quota_limit(self, limit_type: str) -> None:
        """配额限制"""
        if limit_type == "local":
            text = f"\n!! [本地配额限制]\n当前Agent已用完子Agent配额\n{'═'*50}\n"
        else:
            text = f"\n!! [全局配额限制]\n整个任务已用完所有子Agent配额\n{'═'*50}\n"
        self.chat_panel.add_text(text)

    def on_wait_input(self) -> None:
        """等待输入"""
        self.chat_panel.add_text("[等待用户输入]")
