"""Shell 命令解析与子命令提取工具。"""

from __future__ import annotations

import base64
import json
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ParsedCommandInvocation:
    """单条解析出的命令调用。"""

    text: str
    policy_text: str
    argv: tuple[str, ...]
    signature: str


_NESTED_PLACEHOLDER = "$(...)"


def _to_policy_token(token: str) -> str:
    value = token.strip()
    if not value:
        return value
    if value.startswith("$(") and value.endswith(")"):
        return _NESTED_PLACEHOLDER
    # bash/字符串中的命令替换，统一降噪为占位
    value = re.sub(r"\$\((?:[^()]+|\([^()]*\))*\)", _NESTED_PLACEHOLDER, value)
    return value


def _normalize_token(token: str) -> str:
    value = token.strip().strip('"').strip("'")
    return value


def _looks_like_subcommand(token: str) -> bool:
    if not token:
        return False
    if token.startswith("-") or token.startswith("/"):
        return False
    if token.startswith("$("):
        return False
    return True


def _build_signature(tokens: Iterable[str]) -> str:
    argv = [item for item in (_normalize_token(token) for token in tokens) if item]
    if not argv:
        return ""
    if len(argv) >= 2 and _looks_like_subcommand(argv[1]):
        return f"{argv[0]} {argv[1]}"
    return argv[0]


def _build_policy_text(tokens: Iterable[str]) -> str:
    return " ".join(_to_policy_token(token) for token in tokens if str(token).strip())


def _parse_powershell_raw(command: str) -> list[dict]:
    shell_path = shutil.which("pwsh") or shutil.which("powershell")
    if not shell_path:
        return []
    payload = base64.b64encode(command.encode("utf-8")).decode("ascii")
    script = (
        "$raw=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__'));"
        "$tokens=$null;$errors=$null;"
        "$ast=[System.Management.Automation.Language.Parser]::ParseInput($raw,[ref]$tokens,[ref]$errors);"
        "$cmds=$ast.FindAll({param($n) $n -is [System.Management.Automation.Language.CommandAst]},$true)"
        "| ForEach-Object {"
        "[pscustomobject]@{text=$_.Extent.Text;elements=@($_.CommandElements|ForEach-Object{$_.Extent.Text})}"
        "};"
        "[pscustomobject]@{errors=@($errors|ForEach-Object{$_.Message});commands=@($cmds)}"
        "| ConvertTo-Json -Compress -Depth 8"
    ).replace("__PAYLOAD__", payload)
    try:
        result = subprocess.run(
            [shell_path, "-NoProfile", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
    except Exception:
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        data = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return []
    commands = data.get("commands", [])
    if isinstance(commands, dict):
        commands = [commands]
    return [item for item in commands if isinstance(item, dict)]


def _extract_powershell_invocations(command: str) -> list[ParsedCommandInvocation]:
    invocations: list[ParsedCommandInvocation] = []
    for item in _parse_powershell_raw(command):
        elements = item.get("elements") or []
        tokens = [str(token) for token in elements if str(token).strip()]
        if not tokens:
            continue
        signature = _build_signature(tokens)
        if not signature:
            continue
        text = str(item.get("text") or " ".join(tokens)).strip()
        invocations.append(
            ParsedCommandInvocation(
                text=text,
                policy_text=_build_policy_text(tokens),
                argv=tuple(_normalize_token(token) for token in tokens),
                signature=signature,
            )
        )
    return invocations


def _extract_bash_invocations(command: str) -> list[ParsedCommandInvocation]:
    try:
        import bashlex  # type: ignore
    except Exception:
        return []

    invocations: list[ParsedCommandInvocation] = []

    def _walk(node: object) -> None:
        kind = getattr(node, "kind", "")
        if kind == "command":
            parts = getattr(node, "parts", []) or []
            words: list[str] = []
            for part in parts:
                part_kind = getattr(part, "kind", "")
                if part_kind == "word":
                    word = str(getattr(part, "word", "")).strip()
                    if word:
                        words.append(word)
            signature = _build_signature(words)
            if signature:
                text = " ".join(words)
                invocations.append(
                    ParsedCommandInvocation(
                        text=text,
                        policy_text=_build_policy_text(words),
                        argv=tuple(_normalize_token(item) for item in words),
                        signature=signature,
                    )
                )
        for attr in ("parts", "list", "command", "commands", "value", "pipeline", "left", "right"):
            child = getattr(node, attr, None)
            if isinstance(child, list):
                for item in child:
                    if hasattr(item, "kind"):
                        _walk(item)
            elif child is not None and hasattr(child, "kind"):
                _walk(child)

    try:
        trees = bashlex.parse(command)
    except Exception:
        return []
    for tree in trees:
        _walk(tree)
    return invocations


def extract_command_invocations(command: str, tool: str) -> list[ParsedCommandInvocation]:
    shell_tool = (tool or "").strip().lower()
    if shell_tool == "ps_call":
        return _extract_powershell_invocations(command)
    if shell_tool == "bash_call":
        return _extract_bash_invocations(command)
    return []


def extract_command_signatures(command: str, tool: str) -> list[str]:
    signatures: list[str] = []
    for invocation in extract_command_invocations(command, tool):
        if invocation.signature and invocation.signature not in signatures:
            signatures.append(invocation.signature)
    return signatures


def powershell_parser_available() -> bool:
    return bool(shutil.which("pwsh") or shutil.which("powershell"))


def bash_parser_available() -> bool:
    try:
        import bashlex  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False
