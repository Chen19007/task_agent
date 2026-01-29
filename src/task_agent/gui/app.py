"""Task-Agent GUI 主应用

使用 Dear PyGui 实现的图形界面主应用。
"""

import sys
import time
import threading
from typing import Optional

try:
    import dearpygui.dearpygui as dpg
except ImportError:
    print("错误: Dear PyGui 未安装")
    print("请运行: pip install dearpygui")
    sys.exit(1)

from ..agent import Action, StepResult
from ..config import Config
from ..llm import create_client
from .adapter import ExecutorAdapter
from .async_executor import AsyncExecutor
from .chat_panel import ChatPanel
from .gui_output import GUIOutput
from .session_list import SessionList
from .themes import ThemeColors


class TaskAgentGUI:
    """Task-Agent GUI 主应用"""

    def __init__(self, config: Config):
        """初始化 GUI 应用

        Args:
            config: 配置对象
        """
        self.config = config

        # 检查 LLM 连接（不阻止启动）
        self._llm_connected = self._check_llm_connection()
        if not self._llm_connected:
            print(f"警告: 无法连接到 LLM 服务 ({self.config.api_type})")
            print(f"Ollama 地址: {self.config.ollama_host}")
            print(f"模型: {self.config.model}")
            print("GUI 将继续启动，但任务执行可能失败")

        # UI 组件（初始为 None，在 _create_main_window 中创建）
        self.session_list: SessionList = None
        self.chat_panel: ChatPanel = None
        self.status_text: int = None
        self._pending_status: Optional[str] = None

        # 初始化 Dear PyGui
        dpg.create_context()

        # 加载中文字体
        self._load_fonts()

        # 创建主窗口（包括 chat_panel）
        self._create_main_window()

        # 现在创建 GUIOutput 和适配器（chat_panel 已创建）
        self.gui_output = GUIOutput(self.chat_panel)
        self.adapter = ExecutorAdapter(self.config, output_handler=self.gui_output)
        self.async_executor = AsyncExecutor(self.adapter)

        # 状态
        self._waiting_for_user_input = False

        # 加载会话列表
        self._refresh_session_list()

        # 加载当前会话历史
        self._load_current_session()

        # 异步刷新模型列表
        threading.Thread(target=self._refresh_model_list, daemon=True).start()

    def _render_session_messages(self, messages: list[dict]):
        """按 GUI 展示格式渲染历史消息"""
        self.chat_panel.clear_messages()
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            think = msg.get("think", "")
            timestamp = msg.get("timestamp", 0.0)
            if role == "assistant":
                if think and think.strip():
                    self.chat_panel.add_collapsible_block("[思考]", think, collapsed=True)
                if content and content.strip():
                    self.gui_output.render_history_content(content)
            else:
                self.chat_panel.add_message(role, content, timestamp)
        self.gui_output.flush()

    def _check_llm_connection(self) -> bool:
        """检查 LLM 服务连接"""
        try:
            client = create_client(self.config)
            return client.check_connection()
        except Exception:
            return False

    def _load_fonts(self):
        """加载中文字体"""
        import os

        # 尝试使用系统中文字体
        font_paths = [
            # Windows 系统字体
            "C:/Windows/Fonts/msyh.ttc",  # 微软雅黑
            "C:/Windows/Fonts/simhei.ttf",  # 黑体
            "C:/Windows/Fonts/simsun.ttc",  # 宋体
            # Linux 常见字体
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            # macOS 字体
            "/System/Library/Fonts/PingFang.ttc",
        ]

        font_file = None
        for path in font_paths:
            if os.path.exists(path):
                font_file = path
                break

        if font_file:
            # Dear PyGui 2.x 字体加载方式
            with dpg.font_registry():
                # 加载中文字体并添加中文字符范围
                with dpg.font(font_file, 18) as font_id:
                    # 添加默认拉丁字符范围
                    dpg.add_font_range_hint(dpg.mvFontRangeHint_Default)
                    # 添加中文字符范围（简体中文常用）
                    dpg.add_font_range_hint(dpg.mvFontRangeHint_Chinese_Simplified_Common)
                    # 添加完整中文字符范围
                    dpg.add_font_range_hint(dpg.mvFontRangeHint_Chinese_Full)
                self._font_id = font_id
            print(f"已加载中文字体: {font_file}")
        else:
            self._font_id = None
            print("警告: 未找到中文字体，中文可能显示为方块")

    def _create_main_window(self):
        """创建主窗口"""
        # 设置主题
        with dpg.theme() as global_theme:
            with dpg.theme_component(dpg.mvAll):
                # 颜色
                dpg.add_theme_color(
                    dpg.mvThemeCol_WindowBg,
                    ThemeColors.WINDOW_BG
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_ChildBg,
                    ThemeColors.PANEL_BG
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button,
                    ThemeColors.INPUT_BG
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_ButtonHovered,
                    ThemeColors.BUTTON_HOVER
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_ButtonActive,
                    ThemeColors.BUTTON_ACTIVE
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_Text,
                    ThemeColors.USER_TEXT
                )
                # 减少默认 padding 和 spacing，避免额外空间
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0, category=dpg.mvThemeCat_Core)
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 0, 0, category=dpg.mvThemeCat_Core)
                dpg.add_theme_style(dpg.mvStyleVar_CellPadding, 0, 0, category=dpg.mvThemeCat_Core)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 0, 0, category=dpg.mvThemeCat_Core)

        dpg.bind_theme(global_theme)

        # 创建主窗口（不设置固定尺寸，让它适应视口）
        with dpg.window(label="Task Agent", tag="main_window",
                        no_scrollbar=True, no_scroll_with_mouse=True):
            # 状态栏（包含模型选择下拉框）
            with dpg.group(horizontal=True):
                dpg.add_text("模型: ")
                self.model_combo = dpg.add_combo(
                    items=[self.config.model],  # 初始只有当前模型
                    default_value=self.config.model,
                    callback=self._on_model_change,
                    width=200
                )
                dpg.add_text(" | ")
                self.status_text = dpg.add_input_text(
                    default_value="就绪",
                    readonly=True,
                    width=-1
                )
            dpg.add_separator()

            content_container = dpg.add_child_window(
                border=False,
                no_scrollbar=True,
                no_scroll_with_mouse=True,
                width=-1,
                height=-1
            )

            # 主布局：左右分割（独立滚动）
            with dpg.group(parent=content_container, horizontal=True):
                # 左侧：会话列表 (25%)
                left_panel = dpg.add_child_window(
                    border=False,
                    no_scrollbar=True,
                    no_scroll_with_mouse=True,
                    width=250,
                    height=-1
                )
                # 右侧：聊天面板 (75%)
                right_panel = dpg.add_child_window(
                    border=False,
                    no_scrollbar=True,
                    no_scroll_with_mouse=True,
                    width=-1,
                    height=-1
                )

                # 获取 sessions 目录路径
                import os
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
                sessions_dir = os.path.join(project_root, "sessions")

                self.session_list = SessionList(
                    parent=left_panel,
                    sessions_dir=sessions_dir,
                    on_session_select=self._on_session_select
                )
                self.session_list.set_on_new_session(self._on_new_session)

        self.chat_panel = ChatPanel(
            parent=right_panel,
            on_send=self._on_send,
            on_stop=self._on_stop,
            on_auto_toggle=self._on_auto_toggle,
            on_command=self._on_command
        )
        self.chat_panel.focus_input()

        # 绑定全局字体（在创建窗口之后，setup 之前）
        if hasattr(self, '_font_id') and self._font_id is not None:
            dpg.bind_font(self._font_id)
            print(f"已绑定字体 ID: {self._font_id}")

        # 设置主窗口大小
        dpg.set_primary_window("main_window", True)

    def _refresh_session_list(self):
        """刷新会话列表"""
        self.session_list.load_sessions_from_disk()

    def _load_current_session(self):
        """加载当前会话的历史消息"""
        session_id = self.adapter.get_current_session_id()
        if session_id:
            messages = self.session_list.load_session_messages(session_id)
            if messages:
                self._render_session_messages(messages)

            # 恢复 auto 状态
            auto_enabled = self.adapter.executor.auto_approve
            self.chat_panel.set_auto_enabled(auto_enabled)

    def _on_send(self, message: str):
        """发送消息回调"""
        # 添加用户消息到界面
        self.chat_panel.add_message("user", message, time.time())

        # 更新状态
        self._update_status("执行中...")
        self.chat_panel.set_running(True)

        # 异步执行
        if self._waiting_for_user_input:
            self.async_executor.resume_async(message)
            self._waiting_for_user_input = False
        else:
            self.async_executor.execute_task_async(message)

    def _on_stop(self):
        """停止按钮回调"""
        self.async_executor.stop()
        self._update_status("已停止")
        self.chat_panel.set_running(False)

    def _on_command(self, command: str):
        """命令模式执行"""
        self._update_status("正在执行命令...")

        def run():
            try:
                from ..cli import _execute_command
                result = _execute_command(command, self.config.timeout)
                if result.returncode == 0:
                    output = result.stdout.strip() if result.stdout else "命令执行成功（无输出）"
                else:
                    output = result.stderr.strip() if result.stderr else f"命令执行失败（退出码: {result.returncode}）"
                text = f"$ {command}\n{output}"
                self.gui_output.enqueue_plain_text(text)
                self._set_pending_status("命令执行完成")
            except Exception as exc:
                self.gui_output.enqueue_plain_text(f"$ {command}\n执行异常：{exc}")
                self._set_pending_status("命令执行异常")

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

    def _on_auto_toggle(self, is_enabled: bool):
        """auto 模式切换回调

        Args:
            is_enabled: 是否启用 auto 模式
        """
        # 更新 executor 状态
        self.adapter.executor.auto_approve = is_enabled
        self.chat_panel.set_auto_enabled(is_enabled)

        # 更新状态栏
        status = "启用" if is_enabled else "禁用"
        self._update_status(f"自动同意已{status}")

        # 添加提示消息
        self.chat_panel.add_text(f"[提示] 自动同意已{status}\n")

    def _on_model_change(self, sender, app_data, user_data):
        """模型切换回调
        
        Args:
            sender: DPG sender
            app_data: 选中的模型名称
            user_data: 用户数据
        """
        new_model = app_data
        if new_model and new_model != self.config.model:
            old_model = self.config.model
            self.config.model = new_model
            # 同步更新 adapter 中的 config
            self.adapter.config.model = new_model
            self._update_status(f"已切换到 {new_model}")
            self.chat_panel.add_text(f"[提示] 模型已从 {old_model} 切换到 {new_model}\n")

    def _refresh_model_list(self):
        """刷新可用模型列表"""
        try:
            client = create_client(self.config)
            models = client.list_models()
            if models and hasattr(self, 'model_combo'):
                # 确保当前模型在列表中
                if self.config.model not in models:
                    models.insert(0, self.config.model)
                dpg.configure_item(self.model_combo, items=models)
        except Exception as e:
            print(f"刷新模型列表失败: {e}")

    def _on_session_select(self, session_id: int):
        """会话选择回调"""
        # 更新当前会话ID
        self.session_list._current_session_id = session_id

        if self.adapter.load_session(session_id):
            # 先在聊天区域显示切换提示
            self.chat_panel.add_text(f"\n>>> 已切换到会话 #{session_id} <<<\n")

            messages = self.session_list.load_session_messages(session_id)
            if messages:
                self._render_session_messages(messages)
            else:
                self.chat_panel.clear_messages()
                self.chat_panel.add_text("(空会话，无历史消息)\n")

            # 恢复 auto 状态
            auto_enabled = self.adapter.executor.auto_approve
            self.chat_panel.set_auto_enabled(auto_enabled)

            self._update_status(f"已加载会话 #{session_id}")
            self._refresh_session_list()
        else:
            self._update_status("加载会话失败")
            self.chat_panel.add_text(f"\n[错误] 加载会话 #{session_id} 失败\n")

    def _on_new_session(self):
        """新建会话回调"""
        new_id = self.adapter.create_new_session()
        self.chat_panel.clear_messages()

        # 重置 auto 状态
        self.chat_panel.set_auto_enabled(False)
        self.adapter.executor.auto_approve = False

        self._update_status(f"已创建会话 #{new_id}")
        self._refresh_session_list()

    def _update_status(self, text: str):
        """更新状态栏"""
        if self.status_text:
            dpg.set_value(self.status_text, f"模型: {self.config.model} | {text}")

    def _set_pending_status(self, text: str):
        """在线程安全地请求状态更新"""
        self._pending_status = text

    def _process_output(self, outputs, result):
        """处理执行输出

        注意：GUI 通过 OutputHandler 回调接收结构化输出，
        outputs 列表主要用于 CLI 命令确认流程，GUI 可以忽略大部分内容。
        """
        if self.gui_output:
            self.gui_output.flush()

        # 检查动作
        if result and result.action == Action.COMPLETE:
            self._update_status("任务完成")
            self.chat_panel.set_running(False)

    def _process_queue(self):
        """处理异步队列（每帧调用）"""
        self.async_executor.process_queue(
            output_callback=self._process_output,
            complete_callback=lambda: (
                self._update_status("任务完成"),
                self.chat_panel.set_running(False)
            ),
            error_callback=lambda e: (
                self._update_status(f"错误: {e}"),
                self.chat_panel.set_running(False)
            ),
            waiting_callback=lambda: (
                self._update_status("等待输入..."),
                self.chat_panel.set_running(False),
                setattr(self, '_waiting_for_user_input', True)
            ),
            pending_commands_callback=self._on_pending_commands
        )

    def _on_pending_commands(self, commands):
        """处理待确认的命令（由 AsyncExecutor 调用）

        Args:
            commands: [(index, command), ...] 列表
        """
        if not commands:
            return

        # 显示第一个命令的确认对话框
        for index, command in commands:
            display_command = command.display() if hasattr(command, "display") else command
            self._show_command_confirmation_dialog(index, display_command)
            break  # 只显示第一个

    def _show_command_confirmation_dialog(self, index: int, command: str):
        """显示命令确认对话框

        Args:
            index: 命令索引
            command: 待执行的命令
        """
        # 检查是否已经有对话框打开
        if dpg.does_item_exist("command_confirmation_dialog"):
            return

        with dpg.window(label="命令确认", modal=True, id="command_confirmation_dialog",
                       pos=[400, 300], width=600, height=300):
            dpg.add_spacer(height=10)
            dpg.add_text(f"待执行命令 #{index}：", color=ThemeColors.COMMAND_TEXT)
            dpg.add_separator()

            # 命令文本（可折叠）
            with dpg.collapsing_header(label="命令详情", default_open=True):
                dpg.add_text(command, wrap=1000, color=ThemeColors.COMMAND_TEXT)

            dpg.add_spacer(height=10)
            dpg.add_separator()

            # 按钮组
            with dpg.group(horizontal=True):
                dpg.add_button(label="执行", width=100, callback=lambda: self._on_command_confirm_executed(index, command))
                dpg.add_spacer(width=10)
                dpg.add_button(
                    label="执行并开启自动",
                    width=140,
                    callback=lambda: self._on_command_confirm_execute_with_auto(index, command)
                )
                dpg.add_spacer(width=10)
                dpg.add_button(label="取消", width=100, callback=lambda: self._on_command_confirm_rejected(index))

            dpg.add_spacer(height=10)
            dpg.add_text("提示：点击 '执行' 将在后台运行命令", color=ThemeColors.HINT_TEXT)

    def _on_command_confirm_executed(self, index: int, command: str):
        """用户确认执行命令

        Args:
            index: 命令索引
            command: 待执行的命令
        """
        # 关闭对话框
        if dpg.does_item_exist("command_confirmation_dialog"):
            dpg.delete_item("command_confirmation_dialog")

        # 调用 AsyncExecutor 的方法（会复用 CLI 的 _execute_command）
        self.async_executor.confirm_and_execute_command(index, "executed")

        # 更新状态
        self._update_status("正在执行命令...")

    def _on_command_confirm_execute_with_auto(self, index: int, command: str):
        """用户确认执行命令并开启自动授权"""
        self._on_auto_toggle(True)
        self._on_command_confirm_executed(index, command)

    def _on_command_confirm_rejected(self, index: int):
        """用户取消命令（发送建议）"""
        if dpg.does_item_exist("command_confirmation_dialog"):
            dpg.delete_item("command_confirmation_dialog")

        # TODO: 可以添加输入框让用户输入建议
        self.async_executor.confirm_and_execute_command(index, "rejected", "")
        self.chat_panel.add_text("[取消] 命令已取消\n")

        # 更新状态
        self._update_status("继续执行...")

    def run(self):
        """运行主循环"""
        # 创建视口
        dpg.create_viewport(
            title="Task Agent GUI",
            width=1200,
            height=800,
            clear_color=[30, 30, 30, 255]
        )

        # 设置主窗口
        dpg.setup_dearpygui()

        # 设置主窗口为视口的主窗口（填满视口）
        dpg.set_primary_window("main_window", True)

        # 显示视口
        dpg.show_viewport()

        # 主循环
        while dpg.is_dearpygui_running():
            # 处理队列
            self._process_queue()
            if self.gui_output:
                self.gui_output.flush()
            if self._pending_status:
                self._update_status(self._pending_status)
                self._pending_status = None
            if self.chat_panel:
                self.chat_panel.update_layout()
                self.chat_panel.flush_scroll()

            # 渲染
            dpg.render_dearpygui_frame()

        # 清理
        dpg.destroy_context()


def main():
    """主函数入口"""
    # 复用 CLI 的参数解析逻辑
    from ..cli import parse_args, check_llm_connection

    args = parse_args()

    # 根据 API 类型设置不同的 max_output_tokens 默认值
    # 本地小模型（Ollama）默认 4096，大模型（OpenAI）默认 8192 * 4
    if args.api_type == "openai":
        default_max_tokens = 8192 * 4
    else:
        default_max_tokens = 4096

    # 根据 API 类型设置不同的 num_ctx 默认值
    if args.api_type == "openai":
        default_num_ctx = 1024 * 200
    else:
        default_num_ctx = 4096

    # 用户指定则用用户的，否则使用 API 类型对应的默认值
    max_tokens = args.max_tokens if args.max_tokens is not None else default_max_tokens
    num_ctx = args.num_ctx if args.num_ctx is not None else default_num_ctx

    # 创建配置
    config = Config(
        api_type=args.api_type,
        ollama_host=args.host,
        openai_base_url=args.base_url,
        openai_api_key=args.api_key,
        model=args.model,
        timeout=args.timeout,
        max_output_tokens=max_tokens,
        num_ctx=num_ctx,
    )

    # 检查 LLM 连接
    if not check_llm_connection(config):
        service_name = "OpenAI" if config.api_type == "openai" else "Ollama"
        print(f"错误: 无法连接到 {service_name}")
        print(f"GUI 将继续启动，但任务执行可能失败")

    try:
        print(f"正在初始化 GUI...")
        print(f"模型: {config.model}")
        app = TaskAgentGUI(config)
        print("GUI 已创建，正在启动...")
        app.run()
        print("GUI 已关闭")
    except Exception as e:
        import traceback
        print(f"错误: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
