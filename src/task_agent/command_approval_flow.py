"""统一命令审批与执行流程。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .agent import CommandSpec, Executor
from .approval_execution_context import build_execution_context
from .command_runtime import (
    can_auto_execute_command,
    execute_command_spec,
    format_shell_result,
    normalize_command_spec,
)


@dataclass(slots=True)
class ApprovalExecutionItem:
    command_spec: CommandSpec
    status: str
    message: str


class CommandApprovalFlow:
    """跨场景复用的审批执行器。"""

    def __init__(self, execute_command: Callable[..., object]):
        self._execute_command = execute_command

    @staticmethod
    def normalize_commands(pending_commands: Iterable[object]) -> list[CommandSpec]:
        return [normalize_command_spec(item) for item in pending_commands]

    def split_auto_executable(
        self,
        commands: Iterable[CommandSpec],
        auto_approve: bool,
        workspace_dir: str,
    ) -> tuple[list[CommandSpec], list[CommandSpec]]:
        auto_list: list[CommandSpec] = []
        manual_list: list[CommandSpec] = []
        for command_spec in commands:
            if can_auto_execute_command(command_spec, auto_approve, workspace_dir):
                auto_list.append(command_spec)
            else:
                manual_list.append(command_spec)
        return auto_list, manual_list

    def execute_commands(
        self,
        executor: Executor,
        commands: Iterable[CommandSpec],
        workspace_dir: str,
        output_result: Callable[[str, str], None] | None = None,
    ) -> list[ApprovalExecutionItem]:
        context = build_execution_context(executor, workspace_dir)
        results: list[ApprovalExecutionItem] = []
        for command_spec in commands:
            exec_result = execute_command_spec(
                command_spec=command_spec,
                context=context,
                execute_command=self._execute_command,
            )
            message = exec_result.human_message()
            status = "executed" if exec_result.returncode == 0 else "rejected"
            if output_result is not None:
                output_result(message, status)
            if executor.current_agent:
                executor.current_agent._add_message("user", format_shell_result("executed", message))
            results.append(ApprovalExecutionItem(command_spec=command_spec, status=status, message=message))
        return results

    def auto_execute_if_all_safe(
        self,
        executor: Executor,
        pending_commands: Iterable[object],
        workspace_dir: str,
        output_result: Callable[[str, str], None] | None = None,
    ) -> bool:
        if not executor.auto_approve:
            return False
        normalized = self.normalize_commands(pending_commands)
        auto_list, manual_list = self.split_auto_executable(normalized, True, workspace_dir)
        if manual_list:
            return False
        self.execute_commands(executor, auto_list, workspace_dir, output_result)
        return True

    def reject_commands(
        self,
        executor: Executor,
        reason: str,
        output_result: Callable[[str, str], None] | None = None,
    ) -> None:
        reject_text = reason.strip() if reason.strip() else "用户取消了命令执行"
        if output_result is not None:
            output_result(reject_text, "rejected")
        if executor.current_agent:
            executor.current_agent._add_message("user", format_shell_result("rejected", reject_text))

