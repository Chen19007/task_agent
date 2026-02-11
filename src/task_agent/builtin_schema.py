"""builtin 工具 schema 与路径策略。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import json
import re


@dataclass(frozen=True)
class BuiltinPathPolicy:
    """路径字段策略。"""

    relative_to_workspace: bool = True
    allow_absolute: bool = True


@dataclass(frozen=True)
class BuiltinFieldSpec:
    """builtin 参数定义。"""

    name: str
    required: bool = False
    default: Optional[str] = None
    is_path: bool = False
    path_policy: Optional[BuiltinPathPolicy] = None


@dataclass(frozen=True)
class BuiltinToolSpec:
    """builtin 工具定义。"""

    tool_name: str
    fields: tuple[BuiltinFieldSpec, ...]


READ_FILE_SPEC = BuiltinToolSpec(
    tool_name="read_file",
    fields=(
        BuiltinFieldSpec(
            name="path",
            required=True,
            is_path=True,
            path_policy=BuiltinPathPolicy(relative_to_workspace=True, allow_absolute=True),
        ),
        BuiltinFieldSpec(name="start_line", required=False, default="1"),
        BuiltinFieldSpec(name="max_lines", required=False, default="200"),
    ),
)

SMART_EDIT_SPEC = BuiltinToolSpec(
    tool_name="smart_edit",
    fields=(
        BuiltinFieldSpec(
            name="path",
            required=True,
            is_path=True,
            path_policy=BuiltinPathPolicy(relative_to_workspace=True, allow_absolute=True),
        ),
        BuiltinFieldSpec(name="mode", required=False, default="Patch"),
    ),
)

BUILTIN_TOOL_SCHEMAS: dict[str, BuiltinToolSpec] = {
    READ_FILE_SPEC.tool_name: READ_FILE_SPEC,
    SMART_EDIT_SPEC.tool_name: SMART_EDIT_SPEC,
}


def get_builtin_tool_schema(tool_name: str) -> Optional[BuiltinToolSpec]:
    """按工具名获取 schema。"""
    return BUILTIN_TOOL_SCHEMAS.get((tool_name or "").strip().lower())


def parse_builtin_tool_name(command_text: str) -> str:
    """从 builtin 命令文本中提取工具名。"""
    lines = (command_text or "").splitlines()
    if not lines:
        return ""
    first = lines[0].strip().lower()
    if first.startswith("builtin."):
        tail = first.split(".", 1)[1].strip()
        return tail.split(None, 1)[0].strip()
    return first.strip()


def parse_builtin_simple_kv_args(command_text: str) -> dict[str, str]:
    """解析简化 key:value 参数（忽略 <<< >>> 块内容）。"""
    args: dict[str, str] = {}
    inline_match = re.match(r"^\s*builtin\.(\w+)\s*(\{[\s\S]*\})\s*$", (command_text or "").strip())
    if inline_match:
        json_text = inline_match.group(2)
        try:
            parsed = json.loads(json_text)
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    args[str(key).strip().lower()] = str(value).strip()
                return args
        except Exception:
            pass

    lines = (command_text or "").splitlines()
    if len(lines) <= 1:
        return args

    in_block = False
    for raw_line in lines[1:]:
        line = raw_line.strip()
        if not line:
            continue
        if line == "<<<":
            in_block = True
            continue
        if line == ">>>":
            in_block = False
            continue
        if in_block:
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        args[key] = value
    return args


def _is_subpath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_path_against_workspace(path_text: str, workspace_dir: str) -> tuple[Optional[Path], Optional[str]]:
    """解析路径（相对路径强依赖 workspace_dir，不允许 cwd 兜底）。"""
    raw = (path_text or "").strip()
    if not raw:
        return None, "路径参数不能为空"

    path_obj = Path(raw)
    workspace = (workspace_dir or "").strip()

    if path_obj.is_absolute():
        return path_obj.resolve(), None

    if not workspace:
        return None, "workspace_dir 未设置，无法解析相对路径"

    try:
        workspace_path = Path(workspace).resolve()
    except Exception as exc:
        return None, f"workspace_dir 无效: {exc}"

    return (workspace_path / path_obj).resolve(), None


def builtin_requires_authorization(command_text: str, workspace_dir: str) -> bool:
    """判断 builtin 命令是否需要授权（workspace 外绝对路径）。"""
    tool_name = parse_builtin_tool_name(command_text)
    schema = get_builtin_tool_schema(tool_name)
    if not schema:
        return False

    args = parse_builtin_simple_kv_args(command_text)
    if not args:
        return False

    workspace = (workspace_dir or "").strip()
    if not workspace:
        return True

    try:
        workspace_path = Path(workspace).resolve()
    except Exception:
        return True

    for field in schema.fields:
        if not field.is_path:
            continue
        value = args.get(field.name)
        if not value:
            continue
        resolved, error = resolve_path_against_workspace(value, workspace)
        if error or resolved is None:
            return True
        if Path(value).is_absolute() and not _is_subpath(resolved, workspace_path):
            return True

    return False


def build_builtin_read_file_example_lines() -> list[str]:
    """生成 read_file 示例片段。"""
    spec = READ_FILE_SPEC
    defaults = {field.name: field.default for field in spec.fields}
    return [
        "<builtin>",
        "read_file",
        "path: 文件路径",
        f"start_line: {defaults.get('start_line') or '1'}",
        f"max_lines: {defaults.get('max_lines') or '200'}",
        "</builtin>",
    ]


def build_builtin_smart_edit_example_lines() -> list[str]:
    """生成 smart_edit 示例片段。"""
    spec = SMART_EDIT_SPEC
    defaults = {field.name: field.default for field in spec.fields}
    return [
        "<builtin>",
        "smart_edit",
        "path: 文件路径",
        f"mode: {defaults.get('mode') or 'Patch'}",
        "old_text:",
        "<<<",
        "旧内容（保持物理换行）",
        ">>>",
        "new_text:",
        "<<<",
        "新内容（保持物理换行）",
        ">>>",
        "</builtin>",
    ]
