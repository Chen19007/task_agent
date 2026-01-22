"""聊天面板组件

显示消息历史和输入框。
"""

import math
from dataclasses import dataclass
from typing import Callable, Optional

# Dear PyGui 将在使用时动态导入
try:
    import dearpygui.dearpygui as dpg
except ImportError:
    dpg = None

from .themes import ThemeColors


@dataclass
class Message:
    """消息数据结构"""
    role: str
    content: str
    timestamp: float


class ChatPanel:
    """聊天面板组件"""

    def __init__(self, parent: int, on_send: Optional[Callable] = None,
                 on_stop: Optional[Callable] = None, on_auto_toggle: Optional[Callable] = None,
                 on_command: Optional[Callable] = None):
        """初始化聊天面板

        Args:
            parent: 父容器 ID
            on_send: 发送回调 (message: str)
            on_stop: 停止回调
            on_auto_toggle: auto模式切换回调 (is_enabled: bool)
        """
        if dpg is None:
            raise ImportError("Dear PyGui is not installed. Run: pip install dearpygui")

        self.parent = parent
        self.on_send = on_send
        self.on_stop = on_stop
        self.on_auto_toggle = on_auto_toggle
        self.on_command = on_command

        self.messages: list[Message] = []

        self.messages_container: Optional[int] = None
        self.input_field: Optional[int] = None
        self.send_button: Optional[int] = None
        self.stop_button: Optional[int] = None
        self.auto_checkbox: Optional[int] = None
        self.auto_container: Optional[int] = None
        self.mode_button: Optional[int] = None

        self.command_mode = False
        self.command_history: list[str] = []
        self._command_history_index = 0
        self._pending_scroll = False
        self._pending_scroll_frames = 0

        self._input_handler: Optional[int] = None
        self._input_line_height = 20
        self._input_min_lines = 3
        self._input_max_lines = 10

        self._create_ui()
        self._register_key_handlers()

    def _estimate_text_height(self, content: str, min_lines: int = 1, max_lines: Optional[int] = None) -> int:
        """根据行数估算文本高度"""
        lines = content.count("\n") + 1 if content else 1
        lines = max(lines, min_lines)
        if max_lines is not None:
            lines = min(lines, max_lines)
        return lines * self._input_line_height + 6

    def _update_input_height(self, content: str):
        """根据内容更新输入框高度"""
        if not self.input_field:
            return
        lines = None
        width = None
        if hasattr(dpg, "get_item_rect_size"):
            width = dpg.get_item_rect_size(self.input_field)[0]
        elif hasattr(dpg, "get_item_width"):
            width = dpg.get_item_width(self.input_field)
        if width and hasattr(dpg, "get_text_size") and content:
            wrap_width = max(int(width) - 20, 1)
            text_height = dpg.get_text_size(content, wrap_width=wrap_width)[1]
            lines = max(1, int(math.ceil(text_height / self._input_line_height)))

        if lines is None:
            lines = content.count("\n") + 1 if content else 1
        lines = max(lines, self._input_min_lines)
        lines = min(lines, self._input_max_lines)
        height = lines * self._input_line_height + 6
        dpg.configure_item(self.input_field, height=height)
        self.update_layout()

    def _get_item_height(self, item_id: int) -> int:
        """获取组件高度（若不可用则返回 0）"""
        if not item_id:
            return 0
        if hasattr(dpg, "get_item_rect_size"):
            return int(dpg.get_item_rect_size(item_id)[1])
        if hasattr(dpg, "get_item_height"):
            return int(dpg.get_item_height(item_id))
        return 0

    def update_layout(self):
        """根据可用空间调整消息区高度"""
        if not self.messages_container or not self.input_container:
            return
        parent_height = self._get_item_height(self.parent)
        input_height = self._get_item_height(self.input_container)
        if not parent_height or not input_height:
            return
        target = max(parent_height - input_height - 4, self._input_line_height * 3)
        dpg.configure_item(self.messages_container, height=target)

    def _create_ui(self):
        """创建 UI"""
        # 主布局：垂直分割，消息在上，输入在下
        with dpg.group(parent=self.parent):
            # 消息显示区域（可滚动，高度由可用空间计算）
            self.messages_container = dpg.add_child_window(
                height=200,
                border=False,
                no_scrollbar=False
            )

            # 输入区域（固定在底部）
            self.input_container = dpg.add_group()
            with dpg.group(parent=self.input_container):
                dpg.add_spacer(height=5)
                dpg.add_separator()

                # auto 模式复选框
                self.auto_container = dpg.add_group()
                with dpg.group(parent=self.auto_container, horizontal=True):
                    self.auto_checkbox = dpg.add_checkbox(
                        label="自动同意安全命令",
                        default_value=False,
                        callback=self._on_auto_toggle
                    )
                    dpg.add_spacer(width=20)
                    dpg.add_text("当前目录安全操作将自动执行", color=ThemeColors.HINT_TEXT)

                dpg.add_spacer(height=5)

                # 输入框
                self.input_field = dpg.add_input_text(
                    hint="输入任务描述... (Ctrl+Enter 发送)",
                    width=-1,
                    multiline=True,
                    on_enter=True,  # 单行回车不发送，需要 Ctrl+Enter
                    callback=self._on_enter,
                    height=self._estimate_text_height("", min_lines=self._input_min_lines)
                )

                handler_add = getattr(dpg, "add_item_edited_handler", None) or getattr(dpg, "add_item_edit_handler", None)
                if handler_add:
                    self._input_handler = dpg.add_item_handler_registry()
                    handler_add(callback=self._on_input_edit, parent=self._input_handler)
                    dpg.bind_item_handler_registry(self.input_field, self._input_handler)

                # 按钮行
                with dpg.group(horizontal=True):
                    self.send_button = dpg.add_button(
                        label="发送",
                        callback=self._on_send,
                        width=80
                    )

                    self.mode_button = dpg.add_button(
                        label="命令模式",
                        callback=self._on_toggle_mode,
                        width=90
                    )

                    self.stop_button = dpg.add_button(
                        label="停止",
                        callback=self._on_stop,
                        width=80
                    )
                    # 默认禁用停止按钮
                    dpg.disable_item(self.stop_button)
        self.update_layout()

    def _on_input_edit(self, sender, app_data, user_data=None):
        """输入框编辑回调，用于自适应高度"""
        content = dpg.get_value(self.input_field)
        self._update_input_height(content)

    def _on_toggle_mode(self, sender, app_data, user_data=None):
        """切换聊天/命令模式"""
        self.command_mode = not self.command_mode
        if self.command_mode:
            dpg.configure_item(self.input_field, multiline=False, height=28)
            dpg.configure_item(self.input_field, hint="输入命令... (Enter 执行)")
            if self.auto_container:
                dpg.hide_item(self.auto_container)
            dpg.configure_item(self.send_button, label="执行")
            dpg.configure_item(self.mode_button, label="聊天模式")
            if self.stop_button:
                dpg.hide_item(self.stop_button)
        else:
            dpg.configure_item(self.input_field, multiline=True, height=self._estimate_text_height("", min_lines=self._input_min_lines))
            dpg.configure_item(self.input_field, hint="输入任务描述... (Ctrl+Enter 发送)")
            if self.auto_container:
                dpg.show_item(self.auto_container)
            dpg.configure_item(self.send_button, label="发送")
            dpg.configure_item(self.mode_button, label="命令模式")
            if self.stop_button:
                dpg.show_item(self.stop_button)

        dpg.set_value(self.input_field, "")
        self._update_input_height("")
        self._focus_input()

    def _register_key_handlers(self):
        """注册命令历史上下键"""
        key_handler = getattr(dpg, "add_key_press_handler", None)
        if not key_handler:
            return
        with dpg.handler_registry():
            dpg.add_key_press_handler(key=dpg.mvKey_Up, callback=self._on_history_up)
            dpg.add_key_press_handler(key=dpg.mvKey_Down, callback=self._on_history_down)

    def _on_history_up(self, sender, app_data, user_data=None):
        if not self.command_mode or not self.input_field or not dpg.is_item_focused(self.input_field):
            return
        if not self.command_history:
            return
        self._command_history_index = max(self._command_history_index - 1, 0)
        dpg.set_value(self.input_field, self.command_history[self._command_history_index])

    def _on_history_down(self, sender, app_data, user_data=None):
        if not self.command_mode or not self.input_field or not dpg.is_item_focused(self.input_field):
            return
        if not self.command_history:
            return
        self._command_history_index = min(self._command_history_index + 1, len(self.command_history))
        if self._command_history_index >= len(self.command_history):
            dpg.set_value(self.input_field, "")
        else:
            dpg.set_value(self.input_field, self.command_history[self._command_history_index])

    def _on_enter(self, sender, app_data, user_data=None):
        """回车键回调 - 只在 Ctrl+Enter 时发送"""
        # Dear PyGui 多行输入框：on_enter=True 时，普通回车换行，Ctrl+Enter 触发回调
        # 检查是否有内容
        message = dpg.get_value(self.input_field)
        if message and message.strip():
            # Ctrl+Enter 时 app_data 是一个特殊值，普通回车是 None
            # Dear PyGui 的多行模式下，on_enter=True 时 Ctrl+Enter 才触发回调
            if self.command_mode:
                self._on_send(sender, app_data, user_data)
            else:
                self._on_send(sender, app_data, user_data)

    def _on_send(self, sender, app_data, user_data=None):
        """发送按钮回调"""
        # 获取输入内容
        message = dpg.get_value(self.input_field)
        if message and message.strip():
            message = message.strip()

            if self.command_mode:
                self.command_history.append(message)
                self._command_history_index = len(self.command_history)
                if self.on_command:
                    self.on_command(message)
                dpg.set_value(self.input_field, "")
                self._focus_input()
                return

            # 检查是否为 /auto 命令
            if message.lower() == "/auto":
                current_state = self.is_auto_enabled()
                new_state = not current_state
                self.set_auto_enabled(new_state)
                if self.on_auto_toggle:
                    self.on_auto_toggle(new_state)
                dpg.set_value(self.input_field, "")
                return

            if self.on_send:
                self.on_send(message)
            # 清空输入框
            dpg.set_value(self.input_field, "")
            self._update_input_height("")
            self._focus_input()

    def _on_stop(self, sender, app_data, user_data=None):
        """停止按钮回调"""
        if self.on_stop:
            self.on_stop()

    def set_running(self, is_running: bool):
        """设置运行状态"""
        if is_running:
            dpg.enable_item(self.stop_button)
            dpg.disable_item(self.send_button)
        else:
            dpg.disable_item(self.stop_button)
            dpg.enable_item(self.send_button)

    def add_message(self, role: str, content: str, timestamp: float):
        """添加消息（直接显示原始内容）

        Args:
            role: 角色 (user, assistant, system)
            content: 消息内容
            timestamp: 时间戳
        """
        self.messages.append(Message(role, content, timestamp))

        if not self.messages_container:
            return

        with dpg.group(parent=self.messages_container):
            # 角色标签
            role_color = ThemeColors.get_role_color(role)
            role_display = {
                "user": "用户",
                "assistant": "助手",
                "system": "系统"
            }.get(role, role)

            dpg.add_text(f"[{role_display}]", color=role_color)

            # 使用 input_text 实现可选择复制的文本显示
            # readonly=True + multiline=True 允许选择和复制（Ctrl+C）
            dpg.add_input_text(
                default_value=content,
                multiline=True,
                readonly=True,
                width=-1,
                height=self._estimate_text_height(content)
            )

            # 添加间隔
            dpg.add_spacer(height=5)

        # 滚动到底部
        self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        """滚动到底部"""
        if self.messages_container:
            self._pending_scroll = True
            self._pending_scroll_frames = 6
            scroll_max = dpg.get_y_scroll_max(self.messages_container)
            dpg.set_y_scroll(self.messages_container, value=scroll_max)

    def flush_scroll(self):
        """在渲染循环中触发滚动"""
        if not self._pending_scroll or not self.messages_container:
            return
        scroll_max = dpg.get_y_scroll_max(self.messages_container)
        if scroll_max <= 0:
            return
        dpg.set_y_scroll(self.messages_container, value=scroll_max)
        self._pending_scroll_frames -= 1
        if self._pending_scroll_frames <= 0:
            self._pending_scroll = False

    def clear_messages(self):
        """清空消息"""
        self.messages.clear()
        if self.messages_container:
            dpg.delete_item(self.messages_container, children_only=True)

    def load_messages(self, messages: list):
        """加载消息列表

        Args:
            messages: 消息列表，每项包含 role, content, timestamp
        """
        self.clear_messages()
        for msg in messages:
            self.add_message(msg["role"], msg["content"], msg["timestamp"])

    def add_collapsible_block(self, label: str, content: str, collapsed: bool = True):
        """添加可折叠块

        Args:
            label: 块标签
            content: 块内容
            collapsed: 是否默认折叠
        """
        if not self.messages_container:
            return

        with dpg.group(parent=self.messages_container):
            with dpg.collapsing_header(label=label, default_open=not collapsed):
                # 使用 input_text 实现可选择复制的文本
                # 设置高度但不使用 multiline 的 autosize（因为不稳定）
                dpg.add_input_text(
                    default_value=content,
                    multiline=True,
                    readonly=True,
                    width=-1,
                    height=self._estimate_text_height(content, min_lines=3)
                )
            dpg.add_spacer(height=5)

        self._scroll_to_bottom()

    def add_text(self, content: str):
        """添加普通文本

        Args:
            content: 文本内容
        """
        if not self.messages_container:
            return

        with dpg.group(parent=self.messages_container):
            # 使用 input_text 实现可选择复制的文本
            dpg.add_input_text(
                default_value=content,
                multiline=True,
                readonly=True,
                width=-1,
                height=self._estimate_text_height(content)
            )

        self._scroll_to_bottom()

    def _on_auto_toggle(self, sender, app_data, user_data=None):
        """auto 复选框回调"""
        is_enabled = bool(app_data)
        if self.on_auto_toggle:
            self.on_auto_toggle(is_enabled)

    def set_auto_enabled(self, enabled: bool):
        """设置 auto 模式状态

        Args:
            enabled: 是否启用 auto 模式
        """
        if self.auto_checkbox:
            dpg.set_value(self.auto_checkbox, enabled)

    def is_auto_enabled(self) -> bool:
        """获取 auto 模式状态

        Returns:
            是否启用 auto 模式
        """
        if self.auto_checkbox:
            return bool(dpg.get_value(self.auto_checkbox))
        return False

    def focus_input(self):
        """对外暴露的输入框聚焦方法"""
        self._focus_input()

    def _focus_input(self):
        """确保输入框获得焦点"""
        if self.input_field:
            dpg.focus_item(self.input_field)
