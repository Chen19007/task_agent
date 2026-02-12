from __future__ import annotations

import json
import time
from types import SimpleNamespace

import task_agent.webhook_codex.server as codex_server


def test_request_handler_command_approval_accept(monkeypatch):
    def fake_wait(session_key: str, method: str, params: dict):  # noqa: ARG001
        return {"decision": "accept"}

    monkeypatch.setattr(codex_server, "_wait_human_approval", fake_wait)
    handler = codex_server._build_request_handler("p2p:chat_a")

    resp = handler("item/commandExecution/requestApproval", {"command": "echo ok"})
    assert resp == {"decision": "accept"}


def test_request_handler_file_change_decline(monkeypatch):
    def fake_wait(session_key: str, method: str, params: dict):  # noqa: ARG001
        return {"decision": "decline"}

    monkeypatch.setattr(codex_server, "_wait_human_approval", fake_wait)
    handler = codex_server._build_request_handler("p2p:chat_b")

    resp = handler("item/fileChange/requestApproval", {"reason": "need check"})
    assert resp == {"decision": "decline"}


def test_request_handler_generic_tool_approval(monkeypatch):
    called = {}

    def fake_wait(session_key: str, method: str, params: dict):  # noqa: ARG001
        called["method"] = method
        called["params"] = params
        return {"decision": "accept"}

    monkeypatch.setattr(codex_server, "_wait_human_approval", fake_wait)
    handler = codex_server._build_request_handler("p2p:chat_tool")

    payload = {"tool": "filesystem/read", "arguments": {"path": "README.md"}}
    resp = handler("item/tool/requestApproval", payload)
    assert resp == {"decision": "accept"}
    assert called["method"] == "item/tool/requestApproval"
    assert called["params"] == payload


def test_request_handler_request_user_input_routes_to_correct_method(monkeypatch):
    def fake_wait_input(session_key: str, params: dict):  # noqa: ARG001
        return {"answers": {"q1": {"answers": ["A"]}}}

    monkeypatch.setattr(codex_server, "_wait_request_user_input", fake_wait_input)
    handler = codex_server._build_request_handler("group:chat_c")

    resp = handler(
        "item/tool/requestUserInput",
        {
            "questions": [
                {
                    "id": "q1",
                    "header": "h",
                    "question": "q",
                    "options": [{"label": "A", "description": ""}],
                }
            ]
        },
    )
    assert resp == {"answers": {"q1": {"answers": ["A"]}}}


def test_parse_request_user_input_answers_supports_index_and_free_text():
    questions = [
        {
            "id": "q1",
            "header": "mode",
            "question": "choose",
            "options": [
                {"label": "Alpha", "description": ""},
                {"label": "Beta", "description": ""},
            ],
        },
        {
            "id": "q2",
            "header": "note",
            "question": "input note",
            "options": None,
        },
    ]
    raw_text = "q1: 2\nq2: hello"

    parsed = codex_server._parse_request_user_input_answers(raw_text, questions)
    assert parsed == {
        "q1": {"answers": ["Beta"]},
        "q2": {"answers": ["hello"]},
    }


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
    header = SimpleNamespace(event_id=event_id, event_type="im.message.receive_v1", create_time=now_ms)
    message = SimpleNamespace(
        chat_id=chat_id,
        chat_type=chat_type,
        message_id=message_id,
        content=json.dumps({"text": text}, ensure_ascii=False),
    )
    sender = SimpleNamespace(sender_type="user")
    event = SimpleNamespace(message=message, sender=sender)
    return SimpleNamespace(header=header, event=event, uuid=uuid_value)


def _reset_handle_message_runtime_state():
    with codex_server._processed_lock:
        codex_server._processed_uuids.clear()
        codex_server._processed_event_ids.clear()
        codex_server._processed_message_ids.clear()
    with codex_server._pending_user_input_lock:
        codex_server._pending_user_inputs.clear()


def test_extract_direct_shell_call():
    assert codex_server._extract_direct_shell_call(":Get-Location") == "Get-Location"
    assert codex_server._extract_direct_shell_call("   : dir") == "dir"
    assert codex_server._extract_direct_shell_call(":   ") is None
    assert codex_server._extract_direct_shell_call("/stop") is None


def test_handle_message_colon_command_routes_to_local(monkeypatch):
    _reset_handle_message_runtime_state()
    platform = _FakePlatform()
    monkeypatch.setattr(codex_server, "_platform", platform)
    monkeypatch.setattr(codex_server, "_executor", _ImmediateExecutor())

    called = {}

    def fake_local(command: str, chat_type: str, chat_id: str, message_id: str):
        called["command"] = command
        called["chat_type"] = chat_type
        called["chat_id"] = chat_id
        called["message_id"] = message_id

    turn_called = {"value": False}

    def fake_turn(*args, **kwargs):  # noqa: ARG001
        turn_called["value"] = True

    monkeypatch.setattr(codex_server, "_execute_direct_shell_call_async", fake_local)
    monkeypatch.setattr(codex_server, "_execute_turn_async", fake_turn)

    data = _build_message_event(
        ":Get-Location",
        event_id="event-local-1",
        message_id="msg-local-1",
        uuid_value="uuid-local-1",
    )
    codex_server.handle_message(data)

    assert called == {
        "command": "Get-Location",
        "chat_type": "p2p",
        "chat_id": "chat-1",
        "message_id": "msg-local-1",
    }
    assert turn_called["value"] is False
    assert any(m[0] == "已收到，执行本地命令中。" for m in platform.messages)


def test_pending_user_input_does_not_consume_colon_command(monkeypatch):
    _reset_handle_message_runtime_state()
    platform = _FakePlatform()
    monkeypatch.setattr(codex_server, "_platform", platform)
    monkeypatch.setattr(codex_server, "_executor", _ImmediateExecutor())

    session_key = codex_server._build_session_key("p2p", "chat-1")
    pending = codex_server.PendingUserInput(
        session_key=session_key,
        chat_type="p2p",
        chat_id="chat-1",
        source_message_id="source-1",
        questions=[{"id": "q1", "header": "h", "question": "q", "options": []}],
    )
    with codex_server._pending_user_input_lock:
        codex_server._pending_user_inputs[session_key] = pending

    local_called = {"value": False}

    def fake_local(*args, **kwargs):  # noqa: ARG001
        local_called["value"] = True

    monkeypatch.setattr(codex_server, "_execute_direct_shell_call_async", fake_local)
    monkeypatch.setattr(codex_server, "_execute_turn_async", fake_local)

    data = _build_message_event(
        ":dir",
        event_id="event-local-2",
        message_id="msg-local-2",
        uuid_value="uuid-local-2",
    )
    codex_server.handle_message(data)

    assert local_called["value"] is True
    assert pending.raw_text == ""
    assert pending.event.is_set() is False

