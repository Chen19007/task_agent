"""命令安全检查模块

提供命令安全性验证功能，用于 auto_approve 自动执行模式。
"""

import os
import re
from functools import lru_cache
from pathlib import Path

import yaml
from .shell_command_parser import extract_command_invocations

_SAFETY_RULE_FILE = "command_safety.yaml"
_SAFETY_RULE_DIR = "command_safety"


def _candidate_rule_paths() -> list[Path]:
    project_root = Path(__file__).resolve().parents[2]
    return [
        Path.cwd() / _SAFETY_RULE_DIR / _SAFETY_RULE_FILE,
        project_root / _SAFETY_RULE_DIR / _SAFETY_RULE_FILE,
        # 兼容旧路径，后续可移除
        Path.cwd() / "prompt_rules" / _SAFETY_RULE_FILE,
        project_root / "prompt_rules" / _SAFETY_RULE_FILE,
    ]


@lru_cache(maxsize=1)
def _load_command_regex_rules() -> dict:
    for path in _candidate_rule_paths():
        if not path.exists():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {"blocked": [], "allowed": []}
        blocked = data.get("blocked_command_regex", [])
        allowed = data.get("allowed_command_regex", [])
        if not isinstance(blocked, list):
            blocked = []
        if not isinstance(allowed, list):
            allowed = []
        tools = data.get("tools", {})
        if not isinstance(tools, dict):
            tools = {}

        normalized_tools: dict[str, dict[str, list[str]]] = {}
        for tool_name, tool_rules in tools.items():
            if not isinstance(tool_rules, dict):
                continue
            tool_blocked = tool_rules.get("blocked_command_regex", [])
            tool_allowed = tool_rules.get("allowed_command_regex", [])
            if not isinstance(tool_blocked, list):
                tool_blocked = []
            if not isinstance(tool_allowed, list):
                tool_allowed = []
            normalized_tools[str(tool_name).strip().lower()] = {
                "blocked": [str(item).strip() for item in tool_blocked if str(item).strip()],
                "allowed": [str(item).strip() for item in tool_allowed if str(item).strip()],
            }

        return {
            "blocked": [str(item).strip() for item in blocked if str(item).strip()],
            "allowed": [str(item).strip() for item in allowed if str(item).strip()],
            "tools": normalized_tools,
        }
    return {"blocked": [], "allowed": [], "tools": {}}


@lru_cache(maxsize=1)
def _compile_command_regex_rules() -> dict:
    raw_rules = _load_command_regex_rules()
    def _compile_list(values: list[str]) -> list[re.Pattern[str]]:
        compiled: list[re.Pattern[str]] = []
        for pattern in values:
            try:
                compiled.append(re.compile(pattern))
            except re.error:
                continue
        return compiled

    compiled_tools: dict[str, dict[str, list[re.Pattern[str]]]] = {}
    for tool_name, tool_rules in raw_rules.get("tools", {}).items():
        compiled_tools[tool_name] = {
            "blocked": _compile_list(tool_rules.get("blocked", [])),
            "allowed": _compile_list(tool_rules.get("allowed", [])),
        }

    return {
        "blocked": _compile_list(raw_rules["blocked"]),
        "allowed": _compile_list(raw_rules["allowed"]),
        "tools": compiled_tools,
    }


def _select_regex_rules(tool: str) -> tuple[list[re.Pattern[str]], list[re.Pattern[str]]]:
    all_rules = _compile_command_regex_rules()
    normalized_tool = (tool or "").strip().lower()
    blocked = list(all_rules["blocked"])
    allowed = list(all_rules["allowed"])
    tool_rules = all_rules.get("tools", {}).get(normalized_tool)
    if tool_rules:
        blocked.extend(tool_rules.get("blocked", []))
        allowed.extend(tool_rules.get("allowed", []))
    return blocked, allowed


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


def is_safe_command(
    command: str,
    current_dir: str,
    tool: str = "ps_call",
) -> bool:
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

    blocked_regex, allowed_regex = _select_regex_rules(tool)
    for invocation in extract_command_invocations(command, tool):
        text = invocation.policy_text.strip()
        if not text:
            continue
        # allow 规则优先，用于覆盖同一条命令的 block 命中
        if any(pattern.search(text) for pattern in allowed_regex):
            continue
        if any(pattern.search(text) for pattern in blocked_regex):
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
