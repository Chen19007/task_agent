"""聊天面板组件

显示消息历史和输入框。
"""

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
                 on_stop: Optional[Callable] = None, on_auto_toggle: Optional[Callable] = None):
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

        self.messages: list[Message] = []

        self.messages_container: Optional[int] = None
        self.input_field: Optional[int] = None
        self.send_button: Optional[int] = None
        self.stop_button: Optional[int] = None
        self.auto_checkbox: Optional[int] = None

        self._create_ui()

    def _create_ui(self):
        """创建 UI"""
        # 主布局：垂直分割，消息在上，输入在下
        with dpg.group(parent=self.parent):
            # 消息显示区域（可滚动，占据剩余空间）
            self.messages_container = dpg.add_child_window(
                height=-1,  # 占据剩余空间
                border=False,
                no_scrollbar=False  # 允许独立滚动
            )

            # 输入区域（独立，固定高度，使用 group 而非 child_window）
            with dpg.group():
                # 限制输入区域总高度
                dpg.add_spacer(height=5)
                dpg.add_separator()

                # auto 模式复选框
                with dpg.group(horizontal=True):
                    self.auto_checkbox = dpg.add_checkbox(
                        label="自动同意安全命令",
                        default_value=False,
                        callback=self._on_auto_toggle
                    )
                    dpg.add_spacer(width=20)
                    dpg.add_text("当前目录安全操作将自动执行", color=ThemeColors.HINT_TEXT)

                dpg.add_spacer(height=5)

                # 输入框和按钮
                with dpg.group(horizontal=True):
                    # 输入框设置为多行，Ctrl+Enter 发送
                    # 高度计算：每行约20px，设置3行高度 = 60px
                    self.input_field = dpg.add_input_text(
                        hint="输入任务描述... (Ctrl+Enter 发送)",
                        width=-1,
                        multiline=True,
                        on_enter=True,  # 单行回车不发送，需要 Ctrl+Enter
                        callback=self._on_enter,
                        height=60  # 3行文本的高度 (3 * 20px)
                    )

                    self.send_button = dpg.add_button(
                        label="发送",
                        callback=self._on_send,
                        width=80
                    )

                    self.stop_button = dpg.add_button(
                        label="停止",
                        callback=self._on_stop,
                        width=80
                    )
                    # 默认禁用停止按钮
                    dpg.disable_item(self.stop_button)

    def _on_enter(self, sender, app_data, user_data=None):
        """回车键回调 - 只在 Ctrl+Enter 时发送"""
        # Dear PyGui 多行输入框：on_enter=True 时，普通回车换行，Ctrl+Enter 触发回调
        # 检查是否有内容
        message = dpg.get_value(self.input_field)
        if message and message.strip():
            # Ctrl+Enter 时 app_data 是一个特殊值，普通回车是 None
            # Dear PyGui 的多行模式下，on_enter=True 时 Ctrl+Enter 才触发回调
            self._on_send(sender, app_data, user_data)

    def _on_send(self, sender, app_data, user_data=None):
        """发送按钮回调"""
        # 获取输入内容
        message = dpg.get_value(self.input_field)
        if message and message.strip():
            message = message.strip()

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
                width=-1
            )

            # 添加间隔
            dpg.add_spacer(height=5)

        # 滚动到底部
        self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        """滚动到底部"""
        if self.messages_container:
            scroll_max = dpg.get_y_scroll_max(self.messages_container)
            dpg.set_y_scroll(self.messages_container, value=scroll_max)

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
                # 计算内容行数，设置合适的初始高度
                lines = content.count('\n') + 1
                # 每行约20像素高，最小3行，最大20行
                height = min(max(lines * 20, 60), 400)

                # 使用 input_text 实现可选择复制的文本
                # 设置高度但不使用 multiline 的 autosize（因为不稳定）
                dpg.add_input_text(
                    default_value=content,
                    multiline=True,
                    readonly=True,
                    width=-1,
                    height=height
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
                width=-1
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
