from __future__ import annotations

import json
import time
from types import SimpleNamespace

import task_agent.webhook.server as webhook_server


class _ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)
        return None


class _FakePlatform:
    def __init__(self):
        self.messages = []

    def send_message(self, text: str, chat_id: str, chat_type: str, message_id: str):
        self.messages.append((text, chat_id, chat_type, message_id))
        return f"msg-{len(self.messages)}"


def _build_message_event(
    text: str,
    *,
    event_id: str,
    message_id: str,
    uuid_value: str,
    chat_type: str = "p2p",
    chat_id: str = "chat-1",
):
    now_ms = str(int(time.time() * 1000))
    header = SimpleNamespace(
        event_id=event_id,
        event_type="im.message.receive_v1",
        create_time=now_ms,
        tenant_key="",
        app_id="",
    )
    message = SimpleNamespace(
        chat_id=chat_id,
        chat_type=chat_type,
        message_id=message_id,
        content=json.dumps({"text": text}, ensure_ascii=False),
    )
    sender_id = SimpleNamespace(open_id="open-1", user_id="user-1", union_id="union-1")
    sender = SimpleNamespace(sender_type="user", sender_id=sender_id)
    event = SimpleNamespace(message=message, sender=sender)
    return SimpleNamespace(header=header, event=event, uuid=uuid_value)


def _reset_runtime_state():
    with webhook_server._processed_lock:
        webhook_server._processed_uuids.clear()
        webhook_server._processed_message_ids.clear()
    with webhook_server._pending_auth_lock:
        webhook_server._pending_authorizations.clear()
        webhook_server._pending_latest_card_by_chat.clear()
    with webhook_server._pending_workspace_lock:
        webhook_server._pending_workspace_cards.clear()
        webhook_server._pending_workspace_latest_by_chat.clear()
    with webhook_server._adapters_lock:
        webhook_server._adapters.clear()
    with webhook_server._session_workspace_lock:
        webhook_server._session_workspaces.clear()


def test_direct_command_p2p_routes_to_local(monkeypatch):
    _reset_runtime_state()
    platform = _FakePlatform()
    monkeypatch.setattr(webhook_server, "_platform", platform)
    monkeypatch.setattr(webhook_server, "_executor", _ImmediateExecutor())

    called = {}

    def fake_local(command: str, chat_type: str, chat_id: str, message_id: str):
        called["command"] = command
        called["chat_type"] = chat_type
        called["chat_id"] = chat_id
        called["message_id"] = message_id

    task_called = {"value": False}

    def fake_task(*args, **kwargs):  # noqa: ARG001
        task_called["value"] = True

    monkeypatch.setattr(webhook_server, "_execute_direct_shell_call_async", fake_local)
    monkeypatch.setattr(webhook_server, "execute_task_async", fake_task)

    data = _build_message_event(
        ":Get-Location",
        event_id="event-local-1",
        message_id="msg-local-1",
        uuid_value="uuid-local-1",
    )
    webhook_server.handle_message(data)

    assert called == {
        "command": "Get-Location",
        "chat_type": "p2p",
        "chat_id": "chat-1",
        "message_id": "msg-local-1",
    }
    assert task_called["value"] is False
    assert platform.messages
    last_message = platform.messages[-1]
    assert last_message[1:] == ("chat-1", "p2p", "msg-local-1")
    assert last_message[0]


def test_direct_command_ignored_in_group(monkeypatch):
    _reset_runtime_state()
    platform = _FakePlatform()
    monkeypatch.setattr(webhook_server, "_platform", platform)
    monkeypatch.setattr(webhook_server, "_executor", _ImmediateExecutor())

    local_called = {"value": False}

    def fake_local(*args, **kwargs):  # noqa: ARG001
        local_called["value"] = True

    task_called = {"value": False}

    def fake_task(*args, **kwargs):  # noqa: ARG001
        task_called["value"] = True

    monkeypatch.setattr(webhook_server, "_execute_direct_shell_call_async", fake_local)
    monkeypatch.setattr(webhook_server, "execute_task_async", fake_task)

    data = _build_message_event(
        ":dir",
        event_id="event-group-1",
        message_id="msg-group-1",
        uuid_value="uuid-group-1",
        chat_type="group",
        chat_id="group-1",
    )
    webhook_server.handle_message(data)

    assert local_called["value"] is False
    assert task_called["value"] is True


def test_direct_command_empty_treated_as_text(monkeypatch):
    _reset_runtime_state()
    platform = _FakePlatform()
    monkeypatch.setattr(webhook_server, "_platform", platform)
    monkeypatch.setattr(webhook_server, "_executor", _ImmediateExecutor())

    local_called = {"value": False}

    def fake_local(*args, **kwargs):  # noqa: ARG001
        local_called["value"] = True

    task_called = {"value": False}

    def fake_task(*args, **kwargs):  # noqa: ARG001
        task_called["value"] = True

    monkeypatch.setattr(webhook_server, "_execute_direct_shell_call_async", fake_local)
    monkeypatch.setattr(webhook_server, "execute_task_async", fake_task)

    data = _build_message_event(
        ":   ",
        event_id="event-empty-1",
        message_id="msg-empty-1",
        uuid_value="uuid-empty-1",
    )
    webhook_server.handle_message(data)

    assert local_called["value"] is False
    assert task_called["value"] is True
