"""命令安全检查模块

提供命令安全性验证功能，用于 auto_approve 自动执行模式。
"""

import os
import re


def _extract_paths_from_command(command: str) -> list[str]:
    """从命令中提取路径参数

    Args:
        command: PowerShell 命令

    Returns:
        提取的路径列表
    """
    paths = []
    # 匹配 -Path 后的参数（带引号和不带引号）
    paths.extend(re.findall(r'-Path\s+"([^"]+)"', command))
    paths.extend(re.findall(r'-Path\s+(\S+)', command))
    # 匹配 -LiteralPath 后的参数
    paths.extend(re.findall(r'-LiteralPath\s+"([^"]+)"', command))
    paths.extend(re.findall(r'-LiteralPath\s+(\S+)', command))
    # 匹配 -Destination 后的参数
    paths.extend(re.findall(r'-Destination\s+"([^"]+)"', command))
    paths.extend(re.findall(r'-Destination\s+(\S+)', command))
    # 匹配直接作为参数的路径（带引号的文件路径）
    paths.extend(re.findall(r'"([a-zA-Z]:[\\/][^"]+)"', command))
    paths.extend(re.findall(r'"(\.\.[\\/][^"]+)"', command))
    paths.extend(re.findall(r'"(\.[\\/][^"]+)"', command))
    # 匹配 git 命令中的文件参数
    git_match = re.search(r'git\s+\w+\s+(.+)', command)
    if git_match:
        git_args = git_match.group(1).strip()
        # 简单处理：按空格分割，去除引号
        for arg in git_args.split():
            arg = arg.strip('"\'')
            if arg and not arg.startswith('-'):
                paths.append(arg)
    return paths


def is_safe_command(command: str, current_dir: str) -> bool:
    """检查命令是否为安全的文件操作且在当前目录内

    安全规则：
    1. 不包含危险命令（删除、移动等）
    2. 所有路径都在当前目录及其子目录内

    Args:
        command: PowerShell 命令
        current_dir: 当前工作目录

    Returns:
        是否为安全命令
    """
    # 危险命令模式（需要确认）
    dangerous_patterns = [
        'Remove-Item', 'rm ', 'rmdir ', 'del ',
        'Move-Item', 'mv ', 'move ',
        'Rename-Item', 'ren ',
        'sudo ',  # Linux 提权命令
        'Remove-Item',
    ]

    command_lower = command.lower()
    for pattern in dangerous_patterns:
        if pattern.lower() in command_lower:
            return False

    # 提取路径并检查范围
    paths = _extract_paths_from_command(command)
    if paths:
        current_abs = os.path.abspath(current_dir)
        for path in paths:
            # 跳过相对路径中的 "." 和 ".." 作为单独参数的情况
            if path in ['.', '..']:
                continue
            # 解析为绝对路径
            try:
                abs_path = os.path.abspath(path)
                # 检查是否在当前目录下
                if not abs_path.startswith(current_abs):
                    return False
            except Exception:
                # 路径解析失败，保守起见需要确认
                return False

    return True
