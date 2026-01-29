"""Task-Agent Gradio GUI

使用 Gradio 实现的 Web 界面，提供现代化的聊天体验。
"""

import gradio as gr
import threading
import os
import sys
from typing import Generator, Tuple, List

# 确保项目根目录在 sys.path 中
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from task_agent.agent import Action
from task_agent.config import Config
from task_agent.llm import create_client
from task_agent.gui.adapter import ExecutorAdapter
from task_agent.gui.gradio.gradio_output import GradioOutput
from task_agent.gui.gradio.gradio_executor import GradioExecutor


class GradioApp:
    """Gradio 应用主类"""

    def __init__(self, config: Config):
        """初始化 Gradio 应用

        Args:
            config: 配置对象
        """
        self.config = config
        self.gradio_output = GradioOutput()
        self.adapter = ExecutorAdapter(config, output_handler=self.gradio_output)
        self.executor = GradioExecutor(self.adapter, self.gradio_output)
        self.current_session_id = None

        # 自动创建初始会话（确保会话可以被保存）
        self._ensure_session()

        # 先用默认模型初始化（避免启动时阻塞）
        self.models = [config.model]
        self._models_loaded = False

        # 后台异步加载完整模型列表
        threading.Thread(target=self._load_models_async, daemon=True).start()

    def _ensure_session(self):
        """确保有活动会话（如果没有则创建）"""
        if self.adapter.session_manager.current_session_id is None:
            # 创建新会话
            new_id = self.adapter.create_new_session()
            self.current_session_id = new_id

    def _load_models_async(self):
        """异步加载模型列表（后台执行，避免阻塞启动）"""
        try:
            client = create_client(self.config)
            models = client.list_models()
            print(f"[DEBUG] list_models 返回: {models}")
            if not models:
                self.models = [self.config.model]
            elif self.config.model not in models:
                models.insert(0, self.config.model)
                self.models = models
            else:
                self.models = models
            print(f"[DEBUG] 最终模型列表: {self.models}")
            print(f"[DEBUG] config.model = {self.config.model}")
            self._models_loaded = True
        except Exception as e:
            print(f"获取模型列表失败: {e}")
            self.models = [self.config.model]
            self._models_loaded = False

    def chat(self, message: str, history: list, model: str, auto_approve: bool) -> Generator[dict, None, None]:
        """聊天处理函数（支持命令确认）

        Args:
            message: 用户消息
            history: 历史对话
            model: 选择的模型
            auto_approve: 是否自动同意

        Yields:
            dict: 包含更新状态的字典
            - {"type": "content", "content": str}
            - {"type": "pending_commands", "commands": list}
            - {"type": "complete"}
            - {"type": "error", "message": str}
        """
        # 更新配置
        self.config.model = model
        self.adapter.config.model = model

        # 开始执行
        self.executor.execute_task(message, auto_approve)

        # 持续获取状态
        while True:
            state = self.executor.get_state()
            if not state:
                import time
                time.sleep(0.1)
                continue

            state_type, data = state

            if state_type == "output":
                outputs, result = data
                # 获取渲染后的内容
                content = self.gradio_output.get_rendered_content()
                yield {"type": "content", "content": content}

            elif state_type == "pending_commands":
                commands = data
                yield {"type": "pending_commands", "commands": commands}
                break  # 暂停，等待用户确认

            elif state_type == "waiting":
                yield {"type": "waiting", "message": "⏸️ 等待您的输入..."}
                break

            elif state_type == "complete":
                yield {"type": "complete"}
                break

            elif state_type == "error":
                yield {"type": "error", "message": data}
                break

            elif state_type == "stopped":
                yield {"type": "complete"}
                break

    def confirm_command(self, command_index: int, action: str,
                        user_input: str = "") -> Generator[dict, None, None]:
        """处理命令确认后的继续执行

        Args:
            command_index: 命令索引
            action: 动作 (executed/rejected)
            user_input: 用户建议（当 action=rejected 时）

        Yields:
            dict: 与 chat() 相同的格式
        """
        self.executor.confirm_command(command_index, action, user_input)

        # 继续获取状态（逻辑同 chat）
        while True:
            state = self.executor.get_state()
            if not state:
                import time
                time.sleep(0.1)
                continue

            state_type, data = state

            if state_type == "output":
                content = self.gradio_output.get_rendered_content()
                yield {"type": "content", "content": content}

            elif state_type == "pending_commands":
                yield {"type": "pending_commands", "commands": data}
                break

            elif state_type == "complete":
                yield {"type": "complete"}
                break

            elif state_type == "error":
                yield {"type": "error", "message": data}
                break

    def load_session(self, session_id: int) -> Tuple[list, str]:
        """加载会话并返回格式化的历史消息

        Args:
            session_id: 会话 ID

        Returns:
            (格式化的历史消息列表, 状态消息)
        """
        success = self.adapter.load_session(session_id)
        if success:
            self.current_session_id = session_id
            messages = self.adapter.get_current_agent_history()
            formatted = self._format_history_for_gradio(messages)
            return formatted, f"已加载会话 #{session_id}"
        return [], f"加载会话 #{session_id} 失败"

    def _format_history_for_gradio(self, messages: list) -> list:
        """将历史消息格式化为 Gradio Chatbot 格式

        Args:
            messages: 原始消息列表

        Returns:
            格式化后的消息列表
        """
        formatted = []
        for msg in messages:
            if msg["role"] == "assistant":
                content = msg.get("content", "")
                think = msg.get("think", "")

                # 组合渲染
                rendered = ""
                if think:
                    rendered += self.gradio_output._render_collapsible("[思考]", think)

                if content:
                    # 使用 MessageParser 解析
                    self.gradio_output.on_content(content)
                    content_rendered = self.gradio_output.get_rendered_content()
                    if content_rendered:
                        rendered += "\n\n" + content_rendered if rendered else content_rendered

                formatted.append({"role": "assistant", "content": rendered})
            else:
                formatted.append(msg)
        return formatted

    def stop_execution(self):
        """停止当前执行"""
        self.executor.stop()

    def create_interface(self) -> gr.Blocks:
        """创建 Gradio 界面"""
        # 自定义 CSS 样式
        custom_css = """
        details {
            margin: 10px 0;
            border: 1px solid #dee2e6;
            border-radius: 4px;
            padding: 5px;
        }
        summary {
            font-weight: bold;
            cursor: pointer;
            padding: 5px;
            background-color: #f8f9fa;
            border-radius: 3px;
        }
        details[open] summary {
            border-bottom: 1px solid #dee2e6;
            margin-bottom: 5px;
        }
        pre {
            background-color: #f8f9fa;
            padding: 10px;
            border-radius: 4px;
            overflow-x: auto;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        .pending-commands {
            background-color: #fff3cd;
            border: 1px solid #ffc107;
            padding: 15px;
            border-radius: 5px;
            margin: 10px 0;
        }
        """

        with gr.Blocks(title="Task Agent", css=custom_css) as demo:
            # 标题栏 - 单行显示
            gr.Markdown("# 🤖 Task Agent  |  极简任务执行 Agent - 支持多级子 Agent 和会话管理")

            # 状态变量
            pending_commands = gr.State([])
            current_message = gr.State("")
            accumulated_content = gr.State("")

            # 工具栏
            with gr.Row():
                model_dropdown = gr.Dropdown(
                    choices=self.models,
                    value=self.config.model,
                    label="模型",
                    interactive=True,
                    scale=2
                )
                auto_checkbox = gr.Checkbox(
                    label="自动同意安全命令",
                    value=False,
                    scale=1
                )
                stop_btn = gr.Button("停止", variant="stop", scale=1)

            # 对话区域
            chatbot = gr.Chatbot(
                label="对话",
                height=400,
                type="messages"
            )

            # 输入区域
            with gr.Row():
                msg = gr.Textbox(
                    label="输入任务描述",
                    placeholder="输入任务... (Shift+Enter 换行)",
                    lines=2,
                    scale=4
                )
                submit_btn = gr.Button("发送", variant="primary", scale=1)

            # 命令确认对话框（默认隐藏）
            with gr.Column(visible=False) as confirm_dialog:
                gr.Markdown("### ⚠️ 待确认命令")
                current_command = gr.Textbox(
                    label="命令内容",
                    lines=3,
                    interactive=False,
                    value=""
                )
                with gr.Row():
                    execute_btn = gr.Button("执行", variant="primary")
                    cancel_btn = gr.Button("取消")
                    reject_btn = gr.Button("拒绝并输入建议")
                reject_input = gr.Textbox(
                    label="您的建议",
                    visible=False,
                    placeholder="告诉 AI 如何改进..."
                )

            # 会话管理（独立区域）
            with gr.Row():
                gr.Markdown("### 会话管理")
            with gr.Row():
                session_list = self._get_session_list()
                session_dropdown = gr.Dropdown(
                    choices=session_list,
                    value=session_list[0] if session_list else None,
                    label="选择会话",
                    interactive=True,
                    allow_custom_value=True,
                    scale=3
                )
                new_session_btn = gr.Button("新建会话", variant="primary", scale=1)

            # 事件绑定
            def submit_message(message, history, model, auto, pending_cmds):
                """提交消息"""
                if pending_cmds:
                    yield history, message, pending_cmds, gr.update(visible=False), "", gr.update(visible=False)
                    return

                if not message.strip():
                    yield history, message, pending_cmds, gr.update(visible=False), "", gr.update(visible=False)
                    return

                history = history or []
                history.append({"role": "user", "content": message})
                history.append({"role": "assistant", "content": ""})

                accumulated = ""

                for update in self.chat(message, history, model, auto):
                    if update["type"] == "content":
                        # 追加新内容，不是替换
                        if update["content"]:
                            if accumulated:
                                accumulated += "\n\n" + update["content"]
                            else:
                                accumulated = update["content"]
                        history[-1]["content"] = accumulated
                        # 清空输入框，隐藏确认对话框，重置命令列表
                        yield history, "", pending_cmds, gr.update(visible=False), "", gr.update(visible=False)

                    elif update["type"] == "pending_commands":
                        # 显示第一个待确认命令
                        commands = update["commands"]
                        if commands:
                            current_cmd = commands[0] if isinstance(commands[0], str) else commands[0][1]
                            if hasattr(current_cmd, "display"):
                                current_cmd = current_cmd.display()
                            # 清空输入框，显示确认对话框
                            yield history, "", commands, gr.update(visible=True), current_cmd, gr.update(visible=False)
                        else:
                            yield history, "", pending_cmds, gr.update(visible=False), "", gr.update(visible=False)
                        return

                    elif update["type"] == "waiting":
                        accumulated += f"\n\n{update['message']}"
                        history[-1]["content"] = accumulated
                        # 清空输入框，隐藏确认对话框
                        yield history, "", [], gr.update(visible=False), "", gr.update(visible=False)
                        return

                    elif update["type"] == "complete":
                        accumulated += "\n\n✅ 任务完成"
                        history[-1]["content"] = accumulated
                        # 清空输入框，隐藏确认对话框
                        yield history, "", [], gr.update(visible=False), "", gr.update(visible=False)
                        return

                    elif update["type"] == "error":
                        accumulated += f"\n\n❌ 错误: {update['message']}"
                        history[-1]["content"] = accumulated
                        # 清空输入框，隐藏确认对话框
                        yield history, "", [], gr.update(visible=False), "", gr.update(visible=False)
                        return

                # 最终返回，清空输入框
                yield history, "", [], gr.update(visible=False), "", gr.update(visible=False)

            # 命令确认处理（非流式，直接返回最终结果）
            def handle_command_confirmation(action, commands, user_suggestion, history, msg):
                """处理命令确认（逐个确认）- 非流式版本"""
                if not commands:
                    return history, msg, [], gr.update(visible=False), "", gr.update(visible=False)

                # 确定动作类型
                if user_suggestion and user_suggestion.strip():
                    cmd_action = "rejected"
                    user_input = user_suggestion
                else:
                    cmd_action = action  # "executed" 或 "rejected"
                    user_input = ""

                accumulated = history[-1]["content"] if history else ""

                # 收集所有更新
                for update in self.confirm_command(1, cmd_action, user_input):
                    if update["type"] == "content":
                        # 追加新内容
                        if update["content"]:
                            if accumulated:
                                accumulated += "\n\n" + update["content"]
                            else:
                                accumulated = update["content"]
                        history[-1]["content"] = accumulated

                    elif update["type"] == "pending_commands":
                        # 显示下一个待确认命令
                        next_cmds = update["commands"]
                        if next_cmds:
                            next_cmd = next_cmds[0] if isinstance(next_cmds[0], str) else next_cmds[0][1]
                            if hasattr(next_cmd, "display"):
                                next_cmd = next_cmd.display()
                            return history, msg, next_cmds, gr.update(visible=True), next_cmd, gr.update(visible=False)
                        else:
                            return history, msg, [], gr.update(visible=False), "", gr.update(visible=False)

                    elif update["type"] == "complete":
                        accumulated += "\n\n✅ 任务完成"
                        history[-1]["content"] = accumulated
                        return history, msg, [], gr.update(visible=False), "", gr.update(visible=False)

                    elif update["type"] == "error":
                        accumulated += f"\n\n❌ 错误: {update['message']}"
                        history[-1]["content"] = accumulated
                        return history, msg, [], gr.update(visible=False), "", gr.update(visible=False)

                # 默认返回
                return history, msg, [], gr.update(visible=False), "", gr.update(visible=False)

            # 会话切换
            def on_session_change(session_id_str_or_list):
                """会话切换事件"""
                # Gradio 5.x Dropdown 返回 list，兼容 string
                if isinstance(session_id_str_or_list, list):
                    if not session_id_str_or_list:
                        return [], None, ""
                    session_id_str = session_id_str_or_list[0]
                else:
                    if not session_id_str_or_list:
                        return [], None, ""
                    session_id_str = session_id_str_or_list

                # 解析会话 ID
                session_id = int(session_id_str.split("#")[1].strip())
                formatted_history, status_msg = self.load_session(session_id)
                return formatted_history, session_id_str, status_msg

            # 新建会话
            def create_new_session_handler():
                """新建会话"""
                new_id = self.adapter.create_new_session()
                session_list = self._get_session_list()
                new_session_str = f"会话 #{new_id}"
                # 返回 (chatbot, session_dropdown, status_msg)
                # 使用 gr.update 同时更新 choices 和 value
                return [], gr.update(choices=session_list, value=new_session_str), f"已创建会话 #{new_id}"

            # 停止执行
            def stop_handler():
                """停止执行"""
                self.stop_execution()
                return "已停止执行"

            # 绑定事件
            submit_btn.click(
                fn=submit_message,
                inputs=[msg, chatbot, model_dropdown, auto_checkbox, pending_commands],
                outputs=[chatbot, msg, pending_commands, confirm_dialog, current_command, reject_input]
            )

            msg.submit(
                fn=submit_message,
                inputs=[msg, chatbot, model_dropdown, auto_checkbox, pending_commands],
                outputs=[chatbot, msg, pending_commands, confirm_dialog, current_command, reject_input]
            )

            # 执行命令按钮
            execute_btn.click(
                fn=lambda cmds, sug, hist, m: handle_command_confirmation("executed", cmds, sug, hist, m),
                inputs=[pending_commands, reject_input, chatbot, msg],
                outputs=[chatbot, msg, pending_commands, confirm_dialog, current_command, reject_input]
            )

            # 取消命令按钮
            cancel_btn.click(
                fn=lambda cmds, sug, hist, m: handle_command_confirmation("rejected", cmds, sug, hist, m),
                inputs=[pending_commands, reject_input, chatbot, msg],
                outputs=[chatbot, msg, pending_commands, confirm_dialog, current_command, reject_input]
            )

            # 拒绝按钮 - 显示建议输入框
            reject_btn.click(
                fn=lambda: gr.update(visible=True),
                outputs=[reject_input]
            )

            # 建议输入框 - 提交时处理
            reject_input.submit(
                fn=lambda cmds, sug, hist, m: handle_command_confirmation("rejected", cmds, sug, hist, m),
                inputs=[pending_commands, reject_input, chatbot, msg],
                outputs=[chatbot, msg, pending_commands, confirm_dialog, current_command, reject_input]
            )

            # 会话切换
            session_dropdown.change(
                fn=on_session_change,
                inputs=[session_dropdown],
                outputs=[chatbot, session_dropdown, gr.Textbox(visible=False)]
            )

            # 新建会话
            new_session_btn.click(
                fn=create_new_session_handler,
                outputs=[chatbot, session_dropdown, gr.Textbox(visible=False)]
            )

            # 停止按钮
            stop_btn.click(
                fn=stop_handler,
                outputs=[gr.Textbox(visible=False)]
            )

        return demo

    def get_custom_css(self) -> str:
        """获取自定义 CSS 样式"""
        return """
        details {
            margin: 10px 0;
            border: 1px solid #dee2e6;
            border-radius: 4px;
            padding: 5px;
        }
        summary {
            font-weight: bold;
            cursor: pointer;
            padding: 5px;
            background-color: #f8f9fa;
            border-radius: 3px;
        }
        details[open] summary {
            border-bottom: 1px solid #dee2e6;
            margin-bottom: 5px;
        }
        pre {
            background-color: #f8f9fa;
            padding: 10px;
            border-radius: 4px;
            overflow-x: auto;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        .pending-commands {
            background-color: #fff3cd;
            border: 1px solid #ffc107;
            padding: 15px;
            border-radius: 5px;
            margin: 10px 0;
        }
        """

    def _get_session_list(self) -> list[str]:
        """获取会话列表"""
        sessions = self.adapter.list_sessions()
        return [f"会话 #{s['session_id']}" for s in sessions]


def main():
    """主函数入口"""
    from task_agent.cli import parse_args

    args = parse_args()

    # 创建配置
    if args.api_type == "openai":
        default_max_tokens = 8192 * 4
        default_num_ctx = 1024 * 200
    else:
        default_max_tokens = 4096
        default_num_ctx = 4096

    max_tokens = args.max_tokens if args.max_tokens is not None else default_max_tokens
    num_ctx = args.num_ctx if args.num_ctx is not None else default_num_ctx

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

    # 创建应用
    print("Main: Creating GradioApp instance...")
    app = GradioApp(config)
    print("Main: Creating interface...")
    demo = app.create_interface()

    # 启动
    print(f"正在启动 Gradio GUI...")
    print(f"模型: {config.model}")
    demo.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False
    )


if __name__ == "__main__":
    main()
