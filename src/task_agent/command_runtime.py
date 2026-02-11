"""统一命令执行协调器：授权归一，执行分流。"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Callable, Optional

from .agent import CommandSpec
from .platform_utils import get_shell_result_tag
from .safety import is_safe_command


@dataclass
class ExecutionContext:
    """命令执行上下文。"""

    config: object
    workspace_dir: str = ""
    context_messages: Optional[list] = None


@dataclass
class CommandExecutionResult:
    """统一命令执行结果。"""

    command_spec: CommandSpec
    executed_command: str
    returncode: int
    stdout: str
    stderr: str

    def human_message(self) -> str:
        if self.returncode == 0:
            if self.stdout:
                return f"命令执行成功，输出：\n{self.stdout}"
            return "命令执行成功（无输出）"
        return f"命令执行失败（退出码: {self.returncode}）：\n{self.stderr}"


def normalize_command_spec(command: object) -> CommandSpec:
    if isinstance(command, CommandSpec):
        return command
    return CommandSpec(command=str(command))


def is_builtin_command(command_spec: CommandSpec) -> bool:
    tool = str(getattr(command_spec, "tool", "")).strip().lower()
    command = (command_spec.command or "").strip().lower()
    return tool == "builtin" or command.startswith("builtin.")


def is_shell_tool(command_spec: CommandSpec) -> bool:
    tool = str(getattr(command_spec, "tool", "")).strip().lower()
    return tool in {"ps_call", "bash_call"}


def _wrap_powershell_workspace(command: str, workspace_dir: str) -> str:
    escaped = workspace_dir.replace("'", "''")
    return f"Set-Location -LiteralPath '{escaped}'; {command}"


def _wrap_bash_workspace(command: str, workspace_dir: str) -> str:
    escaped = shlex.quote(workspace_dir)
    return f"cd {escaped}; {command}"


def prepare_command_for_execution(command_spec: CommandSpec, workspace_dir: str = "") -> str:
    """
    仅对 shell 类命令注入工作目录；builtin 保持原样。
    """
    command = command_spec.command
    if not workspace_dir:
        return command
    if is_builtin_command(command_spec):
        return command

    tool = str(getattr(command_spec, "tool", "")).strip().lower()
    if tool == "ps_call":
        return _wrap_powershell_workspace(command, workspace_dir)
    if tool == "bash_call":
        return _wrap_bash_workspace(command, workspace_dir)
    # 未知类型保持原样，避免隐式改变语义
    return command


def can_auto_execute_command(
    command_spec: CommandSpec,
    auto_approve: bool,
    workspace_dir: str = "",
) -> bool:
    if not auto_approve:
        return False
    if is_builtin_command(command_spec):
        return True
    return is_safe_command(command_spec.command, workspace_dir or ".")


def format_shell_result(status: str, message: str) -> str:
    tag = get_shell_result_tag()
    return f'<{tag} id="{status}">\n{message}\n</{tag}>'


def execute_command_spec(
    command_spec: CommandSpec,
    context: ExecutionContext,
    execute_command: Callable[..., object],
) -> CommandExecutionResult:
    """
    统一执行单条 CommandSpec。
    execute_command 由调用方注入（通常是 cli._execute_command）。
    """
    command_timeout = (
        command_spec.timeout
        if getattr(command_spec, "timeout", None) is not None
        else context.config.timeout
    )
    final_command = prepare_command_for_execution(command_spec, context.workspace_dir)
    cmd_result = execute_command(
        final_command,
        command_timeout,
        config=context.config,
        context_messages=context.context_messages or [],
        background=bool(getattr(command_spec, "background", False)),
    )
    return CommandExecutionResult(
        command_spec=command_spec,
        executed_command=final_command,
        returncode=int(getattr(cmd_result, "returncode", 1)),
        stdout=str(getattr(cmd_result, "stdout", "")),
        stderr=str(getattr(cmd_result, "stderr", "")),
    )
