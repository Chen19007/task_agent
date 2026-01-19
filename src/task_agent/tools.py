"""工具模块 - 命令执行"""

import subprocess
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class CommandResult:
    """命令执行结果"""

    command: str
    stdout: str
    stderr: str
    returncode: int
    duration: float  # 执行时间（秒）


def execute_command(
    command: str, timeout: int = 300, cwd: Optional[str] = None
) -> CommandResult:
    """执行 bash 命令

    Args:
        command: 要执行的命令
        timeout: 超时时间（秒）
        cwd: 工作目录

    Returns:
        CommandResult: 执行结果
    """
    start_time = time.time()

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )

        duration = time.time() - start_time

        return CommandResult(
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
            duration=duration,
        )

    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        return CommandResult(
            command=command,
            stdout="",
            stderr=f"命令超时（>{timeout}秒）",
            returncode=-1,
            duration=duration,
        )

    except Exception as e:
        duration = time.time() - start_time
        return CommandResult(
            command=command,
            stdout="",
            stderr=f"执行错误：{str(e)}",
            returncode=-1,
            duration=duration,
        )


def estimate_tokens(text: str) -> int:
    """估算 token 数量（约 4 字符 ≈ 1 token）

    Args:
        text: 要估算的文本

    Returns:
        int: 估算的 token 数量
    """
    return len(text) // 4
