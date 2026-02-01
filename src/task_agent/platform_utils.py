"""平台相关工具函数。"""

from __future__ import annotations

import os
import sys


def is_windows() -> bool:
    return os.name == "nt" or sys.platform.startswith("win")


def get_hint_platform_suffix() -> str:
    return "windows" if is_windows() else "linux"


def get_shell_tool_name() -> str:
    return "ps_call" if is_windows() else "bash_call"


def get_shell_result_tag() -> str:
    return "ps_call_result" if is_windows() else "bash_call_result"


def get_shell_label() -> str:
    return "PowerShell" if is_windows() else "Bash"
