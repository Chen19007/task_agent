"""Gradio 执行器 - 处理命令确认和异步执行

在 Gradio 环境中实现类似 Dear PyGui 的命令确认对话框。
"""

import queue
import threading
import time
from typing import Optional, List, Tuple, Generator
from task_agent.gui.adapter import ExecutorAdapter
from task_agent.output_handler import OutputHandler


class GradioExecutor:
    """Gradio 执行器

    支持命令确认对话框，在后台线程中执行任务。
    """

    def __init__(self, adapter: ExecutorAdapter, output_handler: OutputHandler):
        """初始化 Gradio 执行器

        Args:
            adapter: ExecutorAdapter 实例
            output_handler: OutputHandler 实例
        """
        self.adapter = adapter
        self.output_handler = output_handler
        self._state_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._is_running = False
        self._generator = None
        self._pending_commands: List[Tuple[int, str]] = []
        self._waiting_for_confirmation = False

    def execute_task(self, task: str, auto_approve: bool):
        """开始执行任务

        Args:
            task: 任务描述
            auto_approve: 是否自动同意安全命令
        """
        if self._is_running:
            return

        self._stop_event.clear()
        self._is_running = True
        self._pending_commands = []
        self._waiting_for_confirmation = False
        self.adapter.executor.auto_approve = auto_approve
        self._generator = self.adapter.execute_task(task)

        def _run():
            try:
                for outputs, result in self._generator:
                    if self._stop_event.is_set():
                        self._state_queue.put(("stopped", None))
                        break

                    self._state_queue.put(("output", (outputs, result)))

                    # 检查待确认命令
                    if result and result.pending_commands:
                        # 检查是否启用自动同意模式
                        if self.adapter.executor.auto_approve:
                            # 自动执行所有安全命令
                            for idx, command in enumerate(result.pending_commands, 1):
                                # 检查命令是否安全
                                from task_agent.safety import is_safe_command
                                import os
                                current_dir = os.getcwd()
                                if is_safe_command(command, current_dir):
                                    self._execute_command_sync(command, "executed")
                                else:
                                    # 不安全的命令需要用户确认
                                    self._pending_commands = list(enumerate(result.pending_commands, 1))
                                    self._waiting_for_confirmation = True
                                    self._state_queue.put(("pending_commands", self._pending_commands))
                                    break
                            else:
                                # 所有命令都已自动执行，继续循环
                                continue
                        else:
                            # 需要用户确认，显示确认对话框
                            self._pending_commands = list(enumerate(result.pending_commands, 1))
                            self._waiting_for_confirmation = True
                            self._state_queue.put(("pending_commands", self._pending_commands))
                            break

                    # 检查是否需要等待用户输入
                    if result and result.action.value == "wait":
                        self._state_queue.put(("waiting", None))
                        break
                else:
                    self._state_queue.put(("complete", None))
            except Exception as e:
                self._state_queue.put(("error", str(e)))
            finally:
                self._is_running = False

        threading.Thread(target=_run, daemon=True).start()

    def _execute_command_sync(self, command: str, action: str):
        """同步执行命令（用于自动同意模式）

        Args:
            command: 要执行的命令
            action: 动作类型 (executed/rejected)
        """
        try:
            from task_agent.cli import _execute_command

            cmd_result = _execute_command(command, self.adapter.executor.config.timeout)

            if action == "executed":
                if cmd_result.returncode == 0:
                    message = f"命令执行成功，输出：\n{cmd_result.stdout}" if cmd_result.stdout else "命令执行成功（无输出）"
                else:
                    message = f"命令执行失败（退出码: {cmd_result.returncode}）：\n{cmd_result.stderr}"
                result_msg = f'<ps_call_result id="executed">\n{message}\n</ps_call_result>'
            else:  # rejected
                message = "用户取消了命令执行"
                result_msg = f'<ps_call_result id="rejected">\n{message}\n</ps_call_result>'

            if self.adapter.executor.current_agent:
                self.adapter.executor.current_agent._add_message("user", result_msg)
        except Exception as e:
            error_msg = f"命令执行异常：{str(e)}"
            if self.adapter.executor.current_agent:
                self.adapter.executor.current_agent._add_message("user", f'<ps_call_result id="executed">\n{error_msg}\n</ps_call_result>')

    def confirm_command(self, command_index: int, action: str, user_input: str = ""):
        """确认并执行命令

        Args:
            command_index: 命令索引
            action: 动作类型 (executed/rejected)
            user_input: 用户输入（当 action=rejected 时）
        """
        if not self._pending_commands:
            return

        command = None
        for idx, cmd in self._pending_commands:
            if idx == command_index:
                command = cmd
                break

        if not command:
            return

        def execute():
            try:
                from task_agent.cli import _execute_command

                cmd_result = _execute_command(command, self.adapter.executor.config.timeout)

                if action == "executed":
                    if cmd_result.returncode == 0:
                        message = f"命令执行成功，输出：\n{cmd_result.stdout}" if cmd_result.stdout else "命令执行成功（无输出）"
                    else:
                        message = f"命令执行失败（退出码: {cmd_result.returncode}）：\n{cmd_result.stderr}"
                    result_msg = f'<ps_call_result id="executed">\n{message}\n</ps_call_result>'
                else:  # rejected
                    if user_input:
                        message = f"用户建议：{user_input}"
                    else:
                        message = "用户取消了命令执行"
                    result_msg = f'<ps_call_result id="rejected">\n{message}\n</ps_call_result>'

                if self.adapter.executor.current_agent:
                    self.adapter.executor.current_agent._add_message("user", result_msg)

                self._continue_execution()
            except Exception as e:
                error_msg = f"命令执行异常：{str(e)}"
                if self.adapter.executor.current_agent:
                    self.adapter.executor.current_agent._add_message("user", f'<ps_call_result id="executed">\n{error_msg}\n</ps_call_result>')
                self._continue_execution()

        threading.Thread(target=execute, daemon=True).start()

    def _continue_execution(self):
        """继续执行"""
        self._waiting_for_confirmation = False
        self._pending_commands = []

        def _run():
            try:
                for outputs, result in self._generator:
                    if self._stop_event.is_set():
                        self._state_queue.put(("stopped", None))
                        break

                    self._state_queue.put(("output", (outputs, result)))

                    if result and result.pending_commands:
                        self._pending_commands = list(enumerate(result.pending_commands, 1))
                        self._waiting_for_confirmation = True
                        self._state_queue.put(("pending_commands", self._pending_commands))
                        break

                    if result and result.action.value == "wait":
                        self._state_queue.put(("waiting", None))
                        break
                else:
                    self._state_queue.put(("complete", None))
            except Exception as e:
                self._state_queue.put(("error", str(e)))

        threading.Thread(target=_run, daemon=True).start()

    def stop(self):
        """停止当前执行"""
        if self._is_running:
            self._stop_event.set()

    def get_state(self) -> Optional[tuple]:
        """获取状态（非阻塞）

        Returns:
            (state_type, data) 元组，如果没有状态则返回 None
        """
        try:
            return self._state_queue.get_nowait()
        except queue.Empty:
            return None

    def is_waiting_for_confirmation(self) -> bool:
        """是否在等待确认"""
        return self._waiting_for_confirmation

    def is_running(self) -> bool:
        """是否正在运行"""
        return self._is_running
