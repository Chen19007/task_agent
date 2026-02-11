from __future__ import annotations

from types import SimpleNamespace

from task_agent.agent import CommandSpec
from task_agent.command_approval_flow import CommandApprovalFlow


class _FakeAgent:
    def __init__(self) -> None:
        self.history = []
        self.messages = []

    def _add_message(self, role: str, content: str) -> None:
        self.messages.append((role, content))


class _FakeExecutor:
    def __init__(self, auto_approve: bool = False) -> None:
        self.config = SimpleNamespace(timeout=10)
        self.current_agent = _FakeAgent()
        self.auto_approve = auto_approve


class _FakeCmdResult:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_execute_command(command: str, timeout: int, **kwargs):  # noqa: ARG001
    return _FakeCmdResult(returncode=0, stdout=f"ok:{command}", stderr="")


def test_auto_execute_if_all_safe(monkeypatch):
    monkeypatch.setattr("task_agent.command_approval_flow.can_auto_execute_command", lambda *args, **kwargs: True)
    flow = CommandApprovalFlow(_fake_execute_command)
    executor = _FakeExecutor(auto_approve=True)
    results = []

    ok = flow.auto_execute_if_all_safe(
        executor=executor,
        pending_commands=[CommandSpec(command="echo 1", tool="ps_call")],
        workspace_dir=".",
        output_result=lambda message, status: results.append((status, message)),
    )

    assert ok is True
    assert len(results) == 1
    assert results[0][0] == "executed"
    assert executor.current_agent.messages


def test_auto_execute_fallback_when_contains_manual(monkeypatch):
    def _gate(command_spec, auto_approve, workspace_dir):  # noqa: ARG001
        return command_spec.command == "safe"

    monkeypatch.setattr("task_agent.command_approval_flow.can_auto_execute_command", _gate)
    flow = CommandApprovalFlow(_fake_execute_command)
    executor = _FakeExecutor(auto_approve=True)

    ok = flow.auto_execute_if_all_safe(
        executor=executor,
        pending_commands=[
            CommandSpec(command="safe", tool="ps_call"),
            CommandSpec(command="unsafe", tool="ps_call"),
        ],
        workspace_dir=".",
    )

    assert ok is False
    assert executor.current_agent.messages == []


def test_reject_commands_emits_and_records():
    flow = CommandApprovalFlow(_fake_execute_command)
    executor = _FakeExecutor(auto_approve=False)
    output = []

    flow.reject_commands(
        executor=executor,
        reason="用户拒绝",
        output_result=lambda message, status: output.append((status, message)),
    )

    assert output == [("rejected", "用户拒绝")]
    assert "rejected" in executor.current_agent.messages[0][1]

