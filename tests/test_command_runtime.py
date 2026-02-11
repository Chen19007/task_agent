import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from task_agent.agent import CommandSpec
from task_agent.command_runtime import (
    ExecutionContext,
    can_auto_execute_command,
    execute_command_spec,
    normalize_command_spec,
    prepare_command_for_execution,
)
from task_agent.config import Config


class _FakeExecResult:
    def __init__(self, command: str):
        self.stdout = f"executed:{command}"
        self.stderr = ""
        self.returncode = 0


def _fake_execute_command(command, timeout, config=None, context_messages=None, background=False):
    return _FakeExecResult(command)


def test_prepare_builtin_command_keeps_original():
    spec = CommandSpec(command="builtin.create_schedule\nsummary: 测试", tool="builtin")
    prepared = prepare_command_for_execution(spec, workspace_dir="E:/project/python/task_agent")
    assert prepared == spec.command


def test_prepare_ps_call_command_injects_workspace():
    spec = CommandSpec(command="Get-ChildItem", tool="ps_call")
    prepared = prepare_command_for_execution(spec, workspace_dir="E:/project/python/task_agent")
    assert prepared.startswith("Set-Location -LiteralPath ")
    assert prepared.endswith("; Get-ChildItem")


def test_execute_command_spec_builtin_not_wrapped():
    spec = CommandSpec(command="builtin.read_file\npath: a.txt", tool="builtin")
    context = ExecutionContext(config=Config(), workspace_dir="E:/project/python/task_agent")
    result = execute_command_spec(spec, context, _fake_execute_command)
    assert "Set-Location" not in result.executed_command
    assert "builtin.read_file" in result.executed_command


def test_can_auto_execute_builtin_when_auto_on():
    spec = normalize_command_spec(CommandSpec(command="builtin.read_file\npath: a.txt", tool="builtin"))
    assert can_auto_execute_command(spec, True, "E:/project/python/task_agent")
