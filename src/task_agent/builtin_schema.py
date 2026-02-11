"""builtin schema and shared parsing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
import json
import re


ValueNormalizer = Callable[[str], str]


@dataclass(frozen=True)
class BuiltinPathPolicy:
    """Path field policy."""

    relative_to_workspace: bool = True
    allow_absolute: bool = True


@dataclass(frozen=True)
class BuiltinFieldSpec:
    """builtin argument definition."""

    name: str
    required: bool = False
    default: Optional[str] = None
    is_path: bool = False
    path_policy: Optional[BuiltinPathPolicy] = None
    aliases: tuple[str, ...] = ()
    normalizer: Optional[ValueNormalizer] = None


@dataclass(frozen=True)
class BuiltinToolSpec:
    """builtin tool definition."""

    tool_name: str
    fields: tuple[BuiltinFieldSpec, ...]
    allow_unknown_fields: bool = False


@dataclass(frozen=True)
class BuiltinParseError:
    """Normalized parse error for builtin commands."""

    kind: str
    detail: str = ""


def _lower_bool(value: str) -> str:
    return str(value).strip().lower()


READ_FILE_SPEC = BuiltinToolSpec(
    tool_name="read_file",
    fields=(
        BuiltinFieldSpec(
            name="path",
            required=True,
            is_path=True,
            path_policy=BuiltinPathPolicy(relative_to_workspace=True, allow_absolute=True),
            aliases=("file", "filepath"),
        ),
        BuiltinFieldSpec(name="start_line", default="1"),
        BuiltinFieldSpec(name="max_lines", default="200"),
        BuiltinFieldSpec(name="encoding"),
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
            aliases=("file", "filepath"),
        ),
        BuiltinFieldSpec(name="mode", default="Patch"),
    ),
)

GET_JOB_LOG_SPEC = BuiltinToolSpec(
    tool_name="get_job_log",
    fields=(
        BuiltinFieldSpec(name="job_id", required=True, aliases=("id",)),
        BuiltinFieldSpec(name="start_line", default="1"),
        BuiltinFieldSpec(name="max_lines", default="200"),
        BuiltinFieldSpec(name="encoding"),
    ),
)

GET_RESOURCE_SPEC = BuiltinToolSpec(
    tool_name="get_resource",
    fields=(
        BuiltinFieldSpec(name="path", required=True),
        BuiltinFieldSpec(name="encoding"),
    ),
)

MEMORY_QUERY_SPEC = BuiltinToolSpec(
    tool_name="memory_query",
    fields=(
        BuiltinFieldSpec(name="query", required=True),
        BuiltinFieldSpec(name="limit", default="5"),
        BuiltinFieldSpec(name="window", default="10"),
        BuiltinFieldSpec(name="candidate", default="50"),
        BuiltinFieldSpec(name="batch", default="6"),
        BuiltinFieldSpec(name="topn", default="8"),
        BuiltinFieldSpec(name="context_tail", default="8"),
    ),
)

CREATE_SCHEDULE_SPEC = BuiltinToolSpec(
    tool_name="create_schedule",
    fields=(
        BuiltinFieldSpec(name="summary", required=True),
        BuiltinFieldSpec(name="start_time", required=True),
        BuiltinFieldSpec(name="end_time"),
        BuiltinFieldSpec(name="timezone"),
        BuiltinFieldSpec(name="description"),
        BuiltinFieldSpec(name="calendar_id"),
        BuiltinFieldSpec(name="need_notification", normalizer=_lower_bool),
        BuiltinFieldSpec(name="user_id_type"),
        BuiltinFieldSpec(name="attendee_open_ids"),
        BuiltinFieldSpec(name="attendee_need_notification", normalizer=_lower_bool),
    ),
)

HINT_SPEC = BuiltinToolSpec(
    tool_name="hint",
    fields=(
        BuiltinFieldSpec(name="action", required=True, normalizer=_lower_bool),
        BuiltinFieldSpec(name="name"),
    ),
)


BUILTIN_TOOL_SCHEMAS: dict[str, BuiltinToolSpec] = {
    READ_FILE_SPEC.tool_name: READ_FILE_SPEC,
    SMART_EDIT_SPEC.tool_name: SMART_EDIT_SPEC,
    GET_JOB_LOG_SPEC.tool_name: GET_JOB_LOG_SPEC,
    GET_RESOURCE_SPEC.tool_name: GET_RESOURCE_SPEC,
    MEMORY_QUERY_SPEC.tool_name: MEMORY_QUERY_SPEC,
    CREATE_SCHEDULE_SPEC.tool_name: CREATE_SCHEDULE_SPEC,
    HINT_SPEC.tool_name: HINT_SPEC,
}


def get_builtin_tool_schema(tool_name: str) -> Optional[BuiltinToolSpec]:
    return BUILTIN_TOOL_SCHEMAS.get((tool_name or "").strip().lower())


def parse_builtin_tool_name(command_text: str) -> str:
    lines = (command_text or "").splitlines()
    if not lines:
        return ""
    first = lines[0].strip().lower()
    if first.startswith("builtin."):
        tail = first.split(".", 1)[1].strip()
        return tail.split(None, 1)[0].strip()
    return first.strip()


def parse_builtin_simple_kv_args(command_text: str) -> dict[str, str]:
    """Parse simplified key:value args and ignore block payload."""
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


def _find_invalid_kv_line(command_text: str) -> Optional[str]:
    lines = (command_text or "").splitlines()
    if len(lines) <= 1:
        return None
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
            return raw_line
    return None


def normalize_builtin_args_with_schema(
    tool_name: str,
    raw_args: dict[str, str],
    *,
    reject_unknown_fields: bool = True,
    enforce_required: bool = True,
) -> tuple[dict[str, str], Optional[BuiltinParseError]]:
    schema = get_builtin_tool_schema(tool_name)
    if not schema:
        return dict(raw_args), None

    fields = {field.name: field for field in schema.fields}
    alias_map = {
        alias.strip().lower(): field.name
        for field in schema.fields
        for alias in field.aliases
        if alias.strip()
    }

    args: dict[str, str] = {}
    for key, value in raw_args.items():
        raw_key = str(key).strip().lower()
        canonical = alias_map.get(raw_key, raw_key)
        field_spec = fields.get(canonical)
        if field_spec is None:
            if reject_unknown_fields and not schema.allow_unknown_fields:
                return {}, BuiltinParseError(kind="unknown", detail=raw_key)
            continue

        text = str(value).strip()
        if text == "":
            return {}, BuiltinParseError(kind="empty_value", detail=field_spec.name)
        if field_spec.normalizer is not None:
            text = field_spec.normalizer(text)
        args[field_spec.name] = text

    for field in schema.fields:
        if field.name not in args and field.default is not None:
            args[field.name] = str(field.default)

    if enforce_required:
        for field in schema.fields:
            if not field.required:
                continue
            value = str(args.get(field.name, "")).strip()
            if not value:
                return {}, BuiltinParseError(kind="required", detail=field.name)

    return args, None


def parse_builtin_args_by_schema(
    command_text: str,
    tool_name: str,
    *,
    allow_invalid_kv_lines: bool = False,
    reject_unknown_fields: bool = True,
) -> tuple[dict[str, str], Optional[BuiltinParseError]]:
    text = (command_text or "").strip()
    if not text:
        return {}, BuiltinParseError(kind="empty_command")

    first_line = text.splitlines()[0].strip().lower()
    expected = f"builtin.{tool_name}"
    if not first_line.startswith(expected):
        return {}, BuiltinParseError(kind="invalid_format")

    if not allow_invalid_kv_lines:
        invalid_line = _find_invalid_kv_line(text)
        if invalid_line is not None:
            return {}, BuiltinParseError(kind="invalid_line", detail=invalid_line)

    raw_args = parse_builtin_simple_kv_args(text)
    return normalize_builtin_args_with_schema(
        tool_name,
        raw_args,
        reject_unknown_fields=reject_unknown_fields,
    )


def _is_subpath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_path_against_workspace(path_text: str, workspace_dir: str) -> tuple[Optional[Path], Optional[str]]:
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
    tool_name = parse_builtin_tool_name(command_text)
    schema = get_builtin_tool_schema(tool_name)
    if not schema:
        return False

    args, parse_error = parse_builtin_args_by_schema(
        command_text,
        tool_name,
        allow_invalid_kv_lines=(tool_name == "smart_edit"),
        reject_unknown_fields=(tool_name != "smart_edit"),
    )
    if parse_error:
        return True

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
