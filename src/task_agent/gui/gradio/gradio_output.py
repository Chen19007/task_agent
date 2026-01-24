"""Gradio 输出处理器

实现 OutputHandler 接口，将 Agent 事件转换为 Gradio 可渲染的 HTML。
"""

import html
from typing import List
from task_agent.output_handler import OutputHandler
from task_agent.gui.message_parser import MessageParser


class GradioOutput(OutputHandler):
    """Gradio 输出处理器

    将 Agent 输出事件转换为 Gradio 可用的 HTML/markdown 格式。
    """

    def __init__(self):
        """初始化 Gradio 输出处理器"""
        self._parser = MessageParser()
        self._events: List[tuple] = []  # (event_type, payload)

    # OutputHandler 接口实现

    def on_think(self, content: str) -> None:
        """LLM 推理内容（思考过程）"""
        self._events.append(("think", content))

    def on_content(self, content: str) -> None:
        """普通文本内容"""
        # 检查 content 是否完全由 tool tags 组成（没有其他文本）
        # 如果只有 tool tags，由专门的事件处理
        # 如果有文本内容，则解析并渲染（忽略 tool tags，只渲染文本）
        if not content or not content.strip():
            return

        # 使用 parser 解析，只保留 text 类型的 block
        blocks = self._parser.parse(content)
        has_text = False
        for block in blocks:
            if block.block_type == "text" and block.content.strip():
                has_text = True
                break

        if has_text:
            # 有纯文本内容，添加到事件队列
            self._events.append(("content", content))
        # 否则只有 tool tags，由专门的事件处理，这里忽略

    def on_ps_call(self, command: str, index: int, depth_prefix: str) -> None:
        """PowerShell 命令请求"""
        self._events.append(("ps_call", (command, index, depth_prefix)))

    def on_ps_call_result(self, result: str, status: str) -> None:
        """命令执行结果"""
        self._events.append(("ps_call_result", (result, status)))

    def on_create_agent(self, task: str, depth: int, agent_name: str,
                       context_info: dict) -> None:
        """创建子 Agent"""
        self._events.append(("create_agent", (task, depth, agent_name, context_info)))

    def on_agent_complete(self, summary: str, stats: dict) -> None:
        """Agent 完成"""
        self._events.append(("agent_complete", (summary, stats)))

    def on_depth_limit(self) -> None:
        """达到深度限制"""
        self._events.append(("depth_limit", None))

    def on_quota_limit(self, limit_type: str) -> None:
        """配额限制"""
        self._events.append(("quota_limit", limit_type))

    def on_wait_input(self) -> None:
        """等待用户输入"""
        self._events.append(("wait_input", None))

    # Gradio 特定方法

    def get_rendered_content(self) -> str:
        """获取渲染后的 HTML 内容

        Returns:
            HTML 字符串，包含所有事件的渲染结果
        """
        rendered_parts = []
        for event_type, payload in self._events:
            rendered = self._render_event(event_type, payload)
            if rendered:
                rendered_parts.append(rendered)
        self._events.clear()
        return "\n\n".join(rendered_parts)

    def _render_event(self, event_type: str, payload) -> str:
        """渲染单个事件

        Args:
            event_type: 事件类型
            payload: 事件数据

        Returns:
            渲染后的 HTML 字符串
        """
        if event_type == "think":
            return self._render_collapsible("[思考]", payload)

        elif event_type == "content":
            return self._render_content(payload)

        elif event_type == "ps_call":
            command, index, depth_prefix = payload
            if depth_prefix == "":
                label = f"[命令 #{index}]"
            else:
                depth_marker = "+" * (len(depth_prefix) - 1)
                label = f"{depth_marker} [命令 #{index}]"
            return self._render_collapsible(label, command, open=True)

        elif event_type == "ps_call_result":
            result, status = payload
            icon_map = {
                "executed": "[OK]",
                "rejected": "[CANCEL]"
            }
            icon = icon_map.get(status, "[结果]")
            return self._render_collapsible(f"{icon} 结果", result)

        elif event_type == "create_agent":
            task, depth, agent_name, context_info = payload
            agent_info = f" [{agent_name}]" if agent_name else ""
            return (
                f"\n{'+'*60}\n"
                f"深度: {depth}/{context_info.get('max_depth', 4)}{agent_info} | 任务: {task}\n"
                f"{'+'*60}\n"
            )

        elif event_type == "agent_complete":
            summary, stats = payload
            return (
                f"\n{'='*50}\n"
                f"[任务完成]\n"
                f"{summary}\n"
                f"执行命令: {stats['commands']} | 创建子Agent: {stats['sub_agents']}\n"
            )

        elif event_type == "depth_limit":
            return f"\n!! [深度限制]\n已达到最大深度，由当前Agent执行\n{'═'*50}\n"

        elif event_type == "quota_limit":
            if payload == "local":
                return f"\n!! [本地配额限制]\n当前Agent已用完子Agent配额\n{'═'*50}\n"
            else:
                return f"\n!! [全局配额限制]\n整个任务已用完所有子Agent配额\n{'═'*50}\n"

        elif event_type == "wait_input":
            return "[等待用户输入]"

        return ""

    def _render_content(self, content: str) -> str:
        """渲染内容（只渲染纯文本，忽略 tool tags）

        Args:
            content: 原始内容

        Returns:
            渲染后的 HTML 字符串
        """
        blocks = self._parser.parse(content)
        rendered_parts = []

        for block in blocks:
            # 只渲染文本，tool tags 由专门的事件处理
            if block.block_type == "text":
                rendered_parts.append(f"```\n{block.content}\n```")
            # 其他类型（ps_call, create_agent 等）由专门的事件处理，这里忽略

        return "\n\n".join(rendered_parts) if rendered_parts else ""

    def _render_collapsible(self, label: str, content: str, open: bool = False) -> str:
        """渲染可折叠块

        使用 HTML <details> 和 <pre> 标签创建可折叠的代码块。

        Args:
            label: 折叠块的标签
            content: 块内容
            open: 是否默认展开

        Returns:
            HTML 字符串
        """
        escaped = html.escape(content)
        open_attr = " open" if open else ""
        return f'<details{open_attr}><summary>{label}</summary><pre>{escaped}</pre></details>'

    def clear(self):
        """清空事件队列"""
        self._events.clear()
