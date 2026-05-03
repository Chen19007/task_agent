"""命令规格定义模块 - 独立于 agent.py 以避免循环依赖。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .platform_utils import get_shell_tool_name


@dataclass
class CommandSpec:
    """命令规格，携带上下文信息。"""

    command: str
    tool: str = field(default_factory=get_shell_tool_name)
    background: bool = False
    timeout: Optional[int] = None
    index: int = 0

    def display(self) -> str:
        """命令显示文本"""
        if self.tool in {"ps_call", "bash_call"} and self.background:
            return f"{self.command} (后台)"
        return self.command
