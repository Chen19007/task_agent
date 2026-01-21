"""异步执行器

在线程中执行 Agent，通过队列更新 GUI。
"""

import os
import queue
import threading
from typing import Callable, Optional

from ..agent import StepResult
from ..safety import is_safe_command
from .adapter import ExecutorAdapter


class AsyncExecutor:
    """异步执行器

    在后台线程中执行任务，通过队列将结果传递给 GUI 主线程。
    复用 CLI 的命令执行逻辑。
    """

    def __init__(self, adapter: ExecutorAdapter):
        """初始化异步执行器

        Args:
            adapter: Executor 适配器
        """
        self.adapter = adapter
        self.output_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._is_running = False
        self._generator = None  # 保存当前的生成器
        self._pending_commands: list[tuple[int, str]] = []  # (index, command)
        self._waiting_for_confirmation = False

    def execute_task_async(self, task: str):
        """异步执行任务

        Args:
            task: 任务描述
        """
        if self._is_running:
            return

        self._stop_event.clear()
        self._is_running = True
        self._pending_commands = []
        self._waiting_for_confirmation = False

        # 创建并保存生成器
        self._generator = self.adapter.execute_task(task)

        def _run():
            try:
                # 遍历生成器
                for outputs, result in self._generator:
                    if self._stop_event.is_set():
                        self.output_queue.put(("stopped", None))
                        break

                    # 将输出和结果放入队列
                    self.output_queue.put(("output", (outputs, result)))

                    # 检查是否有待确认的命令
                    if result and result.pending_commands:
                        # 检查 auto_approve 模式
                        if self.adapter.executor.auto_approve:
                            current_dir = os.getcwd()
                            # 过滤出安全命令自动执行
                            safe_commands = []
                            unsafe_commands = []
                            for cmd in result.pending_commands:
                                if is_safe_command(cmd, current_dir):
                                    safe_commands.append(cmd)
                                else:
                                    unsafe_commands.append(cmd)

                            # 自动执行安全命令
                            for cmd in safe_commands:
                                self._auto_execute_command(cmd)

                            # 如果没有不安全的命令，继续执行
                            if not unsafe_commands:
                                continue
                            # 否则只处理不安全的命令
                            self._pending_commands = list(enumerate(unsafe_commands, 1))
                            self._waiting_for_confirmation = True
                            self.output_queue.put(("pending_commands", self._pending_commands))
                            break
                        else:
                            # 非 auto 模式，正常流程
                            self._pending_commands = list(enumerate(result.pending_commands, 1))
                            self._waiting_for_confirmation = True
                            self.output_queue.put(("pending_commands", self._pending_commands))
                            break  # 等待 GUI 确认

                    # 检查是否需要等待用户输入
                    if result and result.action.value == "wait":
                        self.output_queue.put(("waiting", None))
                        break

                else:
                    # 正常结束
                    self.output_queue.put(("complete", None))

            except Exception as e:
                self.output_queue.put(("error", str(e)))
            finally:
                self._is_running = False

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def confirm_and_execute_command(self, command_index: int, action: str, user_input: str = ""):
        """确认并执行命令（由 GUI 对话框调用）

        Args:
            command_index: 命令索引（从 1 开始）
            action: executed/skip/rejected
            user_input: 用户输入（当 action=rejected 时）
        """
        if not self._pending_commands:
            return

        # 找到对应的命令
        command = None
        for idx, cmd in self._pending_commands:
            if idx == command_index:
                command = cmd
                break

        if not command:
            return

        # 在新线程中执行命令
        def execute():
            try:
                from ..cli import _execute_command
                cmd_result = _execute_command(command, self.adapter.executor.config.timeout)

                # 构建结果消息
                if action == "executed":
                    if cmd_result.returncode == 0:
                        if cmd_result.stdout:
                            message = f"命令执行成功，输出：\n{cmd_result.stdout}"
                        else:
                            message = "命令执行成功（无输出）"
                    else:
                        message = f"命令执行失败（退出码: {cmd_result.returncode}）：\n{cmd_result.stderr}"
                    result_msg = f'<ps_call_result id="executed">\n{message}\n</ps_call_result>'
                elif action == "skip":
                    message = "命令已跳过"
                    result_msg = f'<ps_call_result id="skip">\n{message}\n</ps_call_result>'
                else:  # rejected
                    message = f"用户建议：{user_input}"
                    result_msg = f'<ps_call_result id="rejected">\n{message}\n</ps_call_result>'

                # 发送结果给 Agent
                if self.adapter.executor.current_agent:
                    self.adapter.executor.current_agent._add_message("user", result_msg)

                # 继续执行（继续遍历同一个生成器）
                self._continue_execution()

            except Exception as e:
                error_msg = f"命令执行异常：{str(e)}"
                if self.adapter.executor.current_agent:
                    self.adapter.executor.current_agent._add_message("user", f'<ps_call_result id="executed">\n{error_msg}\n</ps_call_result>')
                self._continue_execution()

        thread = threading.Thread(target=execute, daemon=True)
        thread.start()

    def _continue_execution(self):
        """继续执行（命令确认后）"""
        self._waiting_for_confirmation = False
        self._pending_commands = []

        # 继续遍历同一个生成器
        def _run():
            try:
                # 继续遍历 self._generator
                for outputs, result in self._generator:
                    if self._stop_event.is_set():
                        self.output_queue.put(("stopped", None))
                        break

                    self.output_queue.put(("output", (outputs, result)))

                    if result and result.pending_commands:
                        # 检查 auto_approve 模式
                        if self.adapter.executor.auto_approve:
                            current_dir = os.getcwd()
                            # 过滤出安全命令自动执行
                            safe_commands = []
                            unsafe_commands = []
                            for cmd in result.pending_commands:
                                if is_safe_command(cmd, current_dir):
                                    safe_commands.append(cmd)
                                else:
                                    unsafe_commands.append(cmd)

                            # 自动执行安全命令
                            for cmd in safe_commands:
                                self._auto_execute_command(cmd)

                            # 如果没有不安全的命令，继续执行
                            if not unsafe_commands:
                                continue
                            # 否则只处理不安全的命令
                            self._pending_commands = list(enumerate(unsafe_commands, 1))
                            self._waiting_for_confirmation = True
                            self.output_queue.put(("pending_commands", self._pending_commands))
                            break
                        else:
                            self._pending_commands = list(enumerate(result.pending_commands, 1))
                            self._waiting_for_confirmation = True
                            self.output_queue.put(("pending_commands", self._pending_commands))
                            break

                    if result and result.action.value == "wait":
                        self.output_queue.put(("waiting", None))
                        break

                else:
                    self.output_queue.put(("complete", None))

            except Exception as e:
                self.output_queue.put(("error", str(e)))

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def resume_async(self, user_input: str):
        """异步恢复执行（用户输入后）

        Args:
            user_input: 用户输入内容
        """
        if self._is_running:
            return

        self._stop_event.clear()
        self._is_running = True

        # 创建并保存生成器
        self._generator = self.adapter.resume(user_input)

        def _run():
            try:
                for outputs, result in self._generator:
                    if self._stop_event.is_set():
                        self.output_queue.put(("stopped", None))
                        break

                    self.output_queue.put(("output", (outputs, result)))

                    if result and result.pending_commands:
                        # 检查 auto_approve 模式
                        if self.adapter.executor.auto_approve:
                            current_dir = os.getcwd()
                            # 过滤出安全命令自动执行
                            safe_commands = []
                            unsafe_commands = []
                            for cmd in result.pending_commands:
                                if is_safe_command(cmd, current_dir):
                                    safe_commands.append(cmd)
                                else:
                                    unsafe_commands.append(cmd)

                            # 自动执行安全命令
                            for cmd in safe_commands:
                                self._auto_execute_command(cmd)

                            # 如果没有不安全的命令，继续执行
                            if not unsafe_commands:
                                continue
                            # 否则只处理不安全的命令
                            self._pending_commands = list(enumerate(unsafe_commands, 1))
                            self._waiting_for_confirmation = True
                            self.output_queue.put(("pending_commands", self._pending_commands))
                            break
                        else:
                            self._pending_commands = list(enumerate(result.pending_commands, 1))
                            self._waiting_for_confirmation = True
                            self.output_queue.put(("pending_commands", self._pending_commands))
                            break

                    if result and result.action.value == "wait":
                        self.output_queue.put(("waiting", None))
                        break

                else:
                    self.output_queue.put(("complete", None))

            except Exception as e:
                self.output_queue.put(("error", str(e)))
            finally:
                self._is_running = False

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def _auto_execute_command(self, command: str):
        """自动执行安全命令

        Args:
            command: 待执行的命令
        """
        try:
            from ..cli import _execute_command
            cmd_result = _execute_command(command, self.adapter.executor.config.timeout)

            # 构建结果消息
            if cmd_result.returncode == 0:
                if cmd_result.stdout:
                    message = f"命令执行成功（自动执行），输出：\n{cmd_result.stdout}"
                else:
                    message = "命令执行成功（自动执行，无输出）"
            else:
                message = f"命令执行失败（退出码: {cmd_result.returncode}）：\n{cmd_result.stderr}"

            result_msg = f'<ps_call_result id="executed">\n{message}\n</ps_call_result>'

            # 发送结果给 Agent
            if self.adapter.executor.current_agent:
                self.adapter.executor.current_agent._add_message("user", result_msg)

        except Exception as e:
            error_msg = f"命令执行异常：{str(e)}"
            if self.adapter.executor.current_agent:
                self.adapter.executor.current_agent._add_message("user", f'<ps_call_result id="executed">\n{error_msg}\n</ps_call_result>')

    def stop(self):
        """停止当前执行"""
        if self._is_running:
            self._stop_event.set()

    def is_running(self) -> bool:
        """检查是否正在运行"""
        return self._is_running

    def get_queue(self) -> queue.Queue:
        """获取输出队列"""
        return self.output_queue

    def is_waiting_for_confirmation(self) -> bool:
        """检查是否在等待命令确认"""
        return self._waiting_for_confirmation

    def process_queue(self, output_callback: Callable, complete_callback: Callable,
                     error_callback: Callable, waiting_callback: Callable,
                     pending_commands_callback: Callable = None):
        """处理队列中的消息（在主线程中调用）

        Args:
            output_callback: 输出回调 (outputs, result)
            complete_callback: 完成回调
            error_callback: 错误回调 (error_message)
            waiting_callback: 等待用户输入回调
            pending_commands_callback: 待确认命令回调 (commands list)
        """
        try:
            while True:
                msg_type, data = self.output_queue.get_nowait()

                if msg_type == "output":
                    outputs, result = data
                    output_callback(outputs, result)
                elif msg_type == "complete":
                    complete_callback()
                elif msg_type == "error":
                    error_callback(data)
                elif msg_type == "waiting":
                    waiting_callback()
                elif msg_type == "stopped":
                    complete_callback()
                elif msg_type == "pending_commands" and pending_commands_callback:
                    pending_commands_callback(data)

        except queue.Empty:
            pass
