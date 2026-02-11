import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from task_agent.shell_command_parser import (
    bash_parser_available,
    extract_command_invocations,
    extract_command_signatures,
    powershell_parser_available,
)


@pytest.mark.skipif(not powershell_parser_available(), reason="未检测到 PowerShell 解析器")
def test_extract_signatures_from_pipeline_and_separator():
    command = "git add a.txt; git log --oneline | Select-Object -First 1"
    signatures = extract_command_signatures(command, "ps_call")
    assert "git add" in signatures
    assert "git log" in signatures
    assert "Select-Object" in signatures


@pytest.mark.skipif(not powershell_parser_available(), reason="未检测到 PowerShell 解析器")
def test_extract_signatures_from_command_substitution():
    command = "Write-Output $(git rev-parse --short HEAD); git status -s"
    signatures = extract_command_signatures(command, "ps_call")
    assert "Write-Output" in signatures
    assert "git rev-parse" in signatures
    assert "git status" in signatures


@pytest.mark.skipif(not powershell_parser_available(), reason="未检测到 PowerShell 解析器")
def test_extract_outer_and_nested_git_commands():
    command = 'git commit -m "msg" $(git push origin main --force)'
    signatures = extract_command_signatures(command, "ps_call")
    assert "git commit" in signatures
    assert "git push" in signatures


@pytest.mark.skipif(not powershell_parser_available(), reason="未检测到 PowerShell 解析器")
def test_policy_text_masks_nested_arguments_on_current_layer():
    command = 'git push $(git commit -m "x" $(git diff | xxx --force))'
    invocations = extract_command_invocations(command, "ps_call")
    outer = invocations[0]
    assert outer.signature == "git push"
    assert "--force" not in outer.policy_text
    assert "$(...)" in outer.policy_text


@pytest.mark.skipif(not bash_parser_available(), reason="未检测到 bash 解析器")
def test_extract_signatures_from_pipeline_and_separator_bash():
    command = "git add a.txt; git log --oneline | head -n 1"
    signatures = extract_command_signatures(command, "bash_call")
    assert "git add" in signatures
    assert "git log" in signatures
    assert "head" in signatures


@pytest.mark.skipif(not bash_parser_available(), reason="未检测到 bash 解析器")
def test_extract_outer_and_nested_git_commands_bash():
    command = 'git commit -m "msg" $(git push origin main --force)'
    signatures = extract_command_signatures(command, "bash_call")
    assert "git commit" in signatures
    assert "git push" in signatures


@pytest.mark.skipif(not bash_parser_available(), reason="未检测到 bash 解析器")
def test_policy_text_masks_nested_arguments_on_current_layer_bash():
    command = 'git push $(git commit -m "x" $(git diff | xxx --force))'
    invocations = extract_command_invocations(command, "bash_call")
    outer = invocations[0]
    assert outer.signature == "git push"
    assert "--force" not in outer.policy_text
    assert "$(...)" in outer.policy_text
