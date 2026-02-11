import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from task_agent.safety import is_safe_command
from task_agent.shell_command_parser import (
    ParsedCommandInvocation,
    bash_parser_available,
    powershell_parser_available,
)


@pytest.mark.skipif(not powershell_parser_available(), reason="未检测到 PowerShell 解析器")
def test_git_push_without_force_is_safe():
    assert is_safe_command("git push origin main", ".", tool="ps_call")


@pytest.mark.skipif(not powershell_parser_available(), reason="未检测到 PowerShell 解析器")
def test_git_push_with_force_is_blocked():
    assert not is_safe_command("git push origin main --force", ".", tool="ps_call")


@pytest.mark.skipif(not powershell_parser_available(), reason="未检测到 PowerShell 解析器")
def test_nested_force_push_is_blocked():
    command = "git commit -m 'msg' $(git push origin main --force)"
    assert not is_safe_command(command, ".", tool="ps_call")


@pytest.mark.skipif(not powershell_parser_available(), reason="未检测到 PowerShell 解析器")
def test_outer_push_with_nested_force_text_is_not_false_positive():
    command = 'git push $(git commit -m "x" $(git diff | xxx --force))'
    assert is_safe_command(command, ".", tool="ps_call")


def test_tool_specific_regex_rules_apply_to_bash(monkeypatch):
    monkeypatch.setattr(
        "task_agent.safety.extract_command_invocations",
        lambda command, tool: [  # noqa: ARG005
            ParsedCommandInvocation(
                text="git push origin main --force",
                policy_text="git push origin main --force",
                argv=("git", "push", "origin", "main", "--force"),
                signature="git push",
            )
        ],
    )
    assert not is_safe_command("ignored", ".", tool="bash_call")


@pytest.mark.skipif(not bash_parser_available(), reason="未检测到 bash 解析器")
def test_git_push_without_force_is_safe_bash():
    assert is_safe_command("git push origin main", ".", tool="bash_call")


@pytest.mark.skipif(not bash_parser_available(), reason="未检测到 bash 解析器")
def test_git_push_with_force_is_blocked_bash():
    assert not is_safe_command("git push origin main --force", ".", tool="bash_call")


@pytest.mark.skipif(not bash_parser_available(), reason="未检测到 bash 解析器")
def test_outer_push_with_nested_force_text_is_not_false_positive_bash():
    command = 'git push $(git commit -m "x" $(git diff | xxx --force))'
    assert is_safe_command(command, ".", tool="bash_call")
