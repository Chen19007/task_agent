"""审批执行上下文构建。"""

from __future__ import annotations

from .agent import Executor
from .command_runtime import ExecutionContext


def build_execution_context(executor: Executor, workspace_dir: str) -> ExecutionContext:
    """从执行器构建命令执行上下文。"""
    return ExecutionContext(
        config=executor.config,
        workspace_dir=workspace_dir,
        context_messages=(executor.current_agent.history if executor.current_agent else None),
    )

