from __future__ import annotations

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

