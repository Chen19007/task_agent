import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from task_agent.config import Config
import task_agent.cli as cli


def test_parse_create_schedule_command_success():
    command = (
        "builtin.create_schedule\n"
        "summary: 评审会\n"
        "start_time: 2026-02-12 10:00\n"
        "end_time: 2026-02-12 11:00\n"
        "timezone: Asia/Shanghai\n"
        "description: 讨论版本发布\n"
    )
    args, error = cli._parse_create_schedule_command(command)
    assert error is None
    assert args["summary"] == "评审会"
    assert args["start_time"] == "2026-02-12 10:00"
    assert args["end_time"] == "2026-02-12 11:00"
    assert args["timezone"] == "Asia/Shanghai"


def test_parse_create_schedule_command_requires_summary():
    command = (
        "builtin.create_schedule\n"
        "start_time: 2026-02-12 10:00\n"
    )
    args, error = cli._parse_create_schedule_command(command)
    assert args == {}
    assert "summary" in error


def test_parse_create_schedule_with_attendees():
    command = (
        "builtin.create_schedule\n"
        "summary: 评审会\n"
        "start_time: 2026-02-12 10:00\n"
        "user_id_type: open_id\n"
        "attendee_open_ids: ou_a, ou_b\n"
        "attendee_need_notification: true\n"
    )
    args, error = cli._parse_create_schedule_command(command)
    assert error is None
    assert args["user_id_type"] == "open_id"
    assert args["attendee_open_ids"] == "ou_a, ou_b"
    assert args["attendee_need_notification"] == "true"


def test_execute_create_schedule_requires_calendar_id():
    args = {"summary": "评审会", "start_time": "2026-02-12 10:00"}
    config = Config(webhook_app_id="app", webhook_app_secret="secret", webhook_calendar_id="")
    result = cli._execute_builtin_create_schedule(args, config)
    assert result.returncode == 1
    assert "WEBHOOK_CALENDAR_ID" in result.stderr


def test_execute_create_schedule_success(monkeypatch):
    class _FakeResult:
        ok = True
        message = "创建成功"
        event_id = "event_123"
        raw_data = {}
        warning = ""

    def _fake_create(**kwargs):
        assert kwargs["calendar_id"] == "calendar_1"
        assert kwargs["summary"] == "评审会"
        return _FakeResult()

    monkeypatch.setattr(cli, "create_feishu_calendar_event", _fake_create)

    args = {"summary": "评审会", "start_time": "2026-02-12 10:00"}
    config = Config(
        webhook_app_id="app",
        webhook_app_secret="secret",
        webhook_calendar_id="calendar_1",
    )
    result = cli._execute_builtin_create_schedule(args, config)
    assert result.returncode == 0
    assert "创建日程成功" in result.stdout
    assert "event_123" in result.stdout


def test_execute_create_schedule_passes_attendee_fields(monkeypatch):
    class _FakeResult:
        ok = True
        message = "创建成功"
        event_id = "event_123"
        raw_data = {}
        warning = "追加参与人失败: code=191002"

    called = {}

    def _fake_create(**kwargs):
        called.update(kwargs)
        return _FakeResult()

    monkeypatch.setattr(cli, "create_feishu_calendar_event", _fake_create)

    args = {
        "summary": "评审会",
        "start_time": "2026-02-12 10:00",
        "user_id_type": "open_id",
        "attendee_open_ids": "ou_a, ou_b",
        "attendee_need_notification": "false",
    }
    config = Config(
        webhook_app_id="app",
        webhook_app_secret="secret",
        webhook_calendar_id="calendar_1",
    )
    result = cli._execute_builtin_create_schedule(args, config)
    assert result.returncode == 0
    assert called["user_id_type"] == "open_id"
    assert called["attendee_open_ids"] == ["ou_a", "ou_b"]
    assert called["attendee_need_notification"] is False
    assert "attendee_count: 2" in result.stdout
    assert "warning: 追加参与人失败: code=191002" in result.stdout


def test_execute_create_schedule_uses_default_attendee_when_missing(monkeypatch):
    class _FakeResult:
        ok = True
        message = "创建成功"
        event_id = "event_123"
        raw_data = {}
        warning = ""

    called = {}

    def _fake_create(**kwargs):
        called.update(kwargs)
        return _FakeResult()

    monkeypatch.setattr(cli, "create_feishu_calendar_event", _fake_create)

    args = {
        "summary": "评审会",
        "start_time": "2026-02-12 10:00",
    }
    config = Config(
        webhook_app_id="app",
        webhook_app_secret="secret",
        webhook_calendar_id="calendar_1",
        webhook_default_attendee_open_id="ou_default",
    )
    result = cli._execute_builtin_create_schedule(args, config)
    assert result.returncode == 0
    assert called["attendee_open_ids"] == ["ou_default"]
    assert "attendee_count: 1" in result.stdout
