"""Gradio 执行器 - 处理命令确认和异步执行

在 Gradio 环境中实现类似 Dear PyGui 的命令确认对话框。
"""

import queue
import threading
import time
from typing import Optional, List, Tuple, Generator
from task_agent.gui.adapter import ExecutorAdapter
from task_agent.agent import CommandSpec
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
        self._pending_commands: List[Tuple[int, CommandSpec]] = []
        self._waiting_for_confirmation = False

    def _normalize_command_spec(self, command: object) -> CommandSpec:
        if isinstance(command, CommandSpec):
            return command
        return CommandSpec(command=str(command))

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
                                command_spec = self._normalize_command_spec(command)
                                # 检查命令是否安全
                                from task_agent.safety import is_safe_command
                                import os
                                current_dir = os.getcwd()
                                if is_safe_command(command_spec.command, current_dir):
                                    self._execute_command_sync(command_spec, "executed")
                                else:
                                    # 不安全的命令需要用户确认
                                    self._pending_commands = list(enumerate([self._normalize_command_spec(cmd) for cmd in result.pending_commands], 1))
                                    self._waiting_for_confirmation = True
                                    self._state_queue.put(("pending_commands", self._pending_commands))
                                    break
                            else:
                                # 所有命令都已自动执行，继续循环
                                continue
                        else:
                            # 需要用户确认，显示确认对话框
                            self._pending_commands = list(enumerate([self._normalize_command_spec(cmd) for cmd in result.pending_commands], 1))
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

    def _execute_command_sync(self, command_spec: CommandSpec, action: str):
        """\u540c\u6b65\u6267\u884c\u547d\u4ee4\uff08\u7528\u4e8e\u81ea\u52a8\u540c\u610f\u6a21\u5f0f\uff09"""
        try:
            from task_agent.cli import _execute_command

            command_timeout = command_spec.timeout if command_spec.timeout is not None else self.adapter.executor.config.timeout
            cmd_result = _execute_command(
                command_spec.command,
                command_timeout,
                background=command_spec.background,
            )

            if action == "executed":
                if cmd_result.returncode == 0:
                    if cmd_result.stdout:
                        message = "\u547d\u4ee4\u6267\u884c\u6210\u529f\uff0c\u8f93\u51fa\uff1a\n" + cmd_result.stdout
                    else:
                        message = "\u547d\u4ee4\u6267\u884c\u6210\u529f\uff08\u65e0\u8f93\u51fa\uff09"
                else:
                    message = f"\u547d\u4ee4\u6267\u884c\u5931\u8d25\uff08\u9000\u51fa\u7801: {cmd_result.returncode}\uff09\uff1a\n{cmd_result.stderr}"
                result_msg = f'<ps_call_result id="executed">\n{message}\n</ps_call_result>'
            else:  # rejected
                message = "\u7528\u6237\u53d6\u6d88\u4e86\u547d\u4ee4\u6267\u884c"
                result_msg = f'<ps_call_result id="rejected">\n{message}\n</ps_call_result>'

            if self.adapter.executor.current_agent:
                self.adapter.executor.current_agent._add_message("user", result_msg)
        except Exception as e:
            error_msg = f"\u547d\u4ee4\u6267\u884c\u5f02\u5e38\uff1a{e}"
            if self.adapter.executor.current_agent:
                self.adapter.executor.current_agent._add_message("user", f'<ps_call_result id="executed">\n{error_msg}\n</ps_call_result>')

    def confirm_command(self, command_index: int, action: str, user_input: str = ""):
        """\u786e\u8ba4\u5e76\u6267\u884c\u547d\u4ee4"""
        if not self._pending_commands:
            return

        command_spec = None
        for idx, cmd in self._pending_commands:
            if idx == command_index:
                command_spec = cmd
                break

        if not command_spec:
            return

        def execute():
            try:
                from task_agent.cli import _execute_command

                command_timeout = command_spec.timeout if command_spec.timeout is not None else self.adapter.executor.config.timeout
                cmd_result = _execute_command(
                    command_spec.command,
                    command_timeout,
                    background=command_spec.background,
                )

                if action == "executed":
                    if cmd_result.returncode == 0:
                        if cmd_result.stdout:
                            message = "\u547d\u4ee4\u6267\u884c\u6210\u529f\uff0c\u8f93\u51fa\uff1a\n" + cmd_result.stdout
                        else:
                            message = "\u547d\u4ee4\u6267\u884c\u6210\u529f\uff08\u65e0\u8f93\u51fa\uff09"
                    else:
                        message = f"\u547d\u4ee4\u6267\u884c\u5931\u8d25\uff08\u9000\u51fa\u7801: {cmd_result.returncode}\uff09\uff1a\n{cmd_result.stderr}"
                    result_msg = f'<ps_call_result id="executed">\n{message}\n</ps_call_result>'
                else:  # rejected
                    if user_input:
                        message = f"\u7528\u6237\u5efa\u8bae\uff1a{user_input}"
                    else:
                        message = "\u7528\u6237\u53d6\u6d88\u4e86\u547d\u4ee4\u6267\u884c"
                    result_msg = f'<ps_call_result id="rejected">\n{message}\n</ps_call_result>'

                if self.adapter.executor.current_agent:
                    self.adapter.executor.current_agent._add_message("user", result_msg)

                self._continue_execution()
            except Exception as e:
                error_msg = f"\u547d\u4ee4\u6267\u884c\u5f02\u5e38\uff1a{e}"
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
                        self._pending_commands = list(enumerate([self._normalize_command_spec(cmd) for cmd in result.pending_commands], 1))
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
