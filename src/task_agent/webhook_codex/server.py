"""飞书到 Codex App Server 的桥接服务。"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Set

from ..config import Config
from ..webhook.message_delivery_pipeline import MessageDeliveryPipeline
from ..webhook.platforms import FeishuPlatform
from .codex_app_server import CodexAppServerClient, TurnCollector

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# 避免 basicConfig 因已有 handler 失效，确保 INFO 日志可见。
logging.getLogger().setLevel(logging.INFO)

_config: Optional[Config] = None
_platform: Optional[FeishuPlatform] = None
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="feishu_codex")
_delivery_pipeline = MessageDeliveryPipeline(max_chars=2800, max_attempts=2, retry_delay=0.3)

_REALTIME_WINDOW_SECONDS = int(os.environ.get("WEBHOOK_REALTIME_WINDOW_SECONDS", "300"))

_processed_uuids: Set[str] = set()
_processed_event_ids: Set[str] = set()
_processed_message_ids: Set[str] = set()
_processed_lock = threading.Lock()

_pending_workspace_cards: Dict[str, Dict[str, Any]] = {}
_pending_workspace_latest_by_chat: Dict[str, str] = {}
_pending_workspace_lock = threading.Lock()


@dataclass
class PendingApproval:
    session_key: str
    method: str
    chat_type: str
    chat_id: str
    source_message_id: str
    command_preview: str
    event: threading.Event = field(default_factory=threading.Event)
    decision: str = "decline"
    reject_reason: str = ""


_pending_approval_cards: Dict[str, PendingApproval] = {}
_pending_approval_latest_by_chat: Dict[str, str] = {}
_pending_approval_lock = threading.Lock()


@dataclass
class PendingUserInput:
    session_key: str
    chat_type: str
    chat_id: str
    source_message_id: str
    questions: list[dict[str, Any]]
    event: threading.Event = field(default_factory=threading.Event)
    raw_text: str = ""


_pending_user_inputs: Dict[str, PendingUserInput] = {}
_pending_user_input_lock = threading.Lock()


@dataclass
class BridgeSession:
    """飞书会话对应的桥接状态。"""

    chat_type: str
    chat_id: str
    workspace_dir: str
    client: CodexAppServerClient
    thread_id: str = ""
    active_turn_id: str = ""
    last_message_id: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)


_sessions: Dict[str, BridgeSession] = {}
_sessions_lock = threading.Lock()


def _build_session_key(chat_type: str, chat_id: str) -> str:
    return f"{chat_type}:{chat_id}"


def _resolve_codex_model() -> str:
    return str(os.environ.get("CODEX_MODEL", "")).strip()


def _send_text(platform: FeishuPlatform, content: str, chat_id: str, chat_type: str, message_id: str) -> None:
    _delivery_pipeline.send_text(
        lambda text: platform.send_message(text, chat_id, chat_type, message_id),
        content,
    )


def _clean_incoming_text(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<at\b[^>]*>.*?</at>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"^(?:@\S+\s*)+", " ", cleaned)
    cleaned = cleaned.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _is_change_workspace_command(text: str) -> bool:
    return re.fullmatch(r"/(?:change_workspace|cw|ws)", text.strip().lower()) is not None


def _is_clear_command(text: str) -> bool:
    return re.fullmatch(r"/clear", text.strip().lower()) is not None


def _is_stop_command(text: str) -> bool:
    return re.fullmatch(r"/stop", text.strip().lower()) is not None


def _format_event_create_time(create_time_ms: str) -> str:
    if not create_time_ms:
        return ""
    try:
        ts = float(create_time_ms) / 1000.0
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _query_zlocation_options(limit: int = 10) -> list[dict[str, str]]:
    command = (
        "Import-Module ZLocation -ErrorAction SilentlyContinue; "
        f"$items = z -l | Select-Object -First {max(1, limit)} Weight,Path; "
        "$items | ConvertTo-Json -Compress"
    )
    options: list[dict[str, str]] = []
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if proc.returncode == 0:
            stdout = (proc.stdout or "").strip()
            if stdout:
                payload = json.loads(stdout)
                rows = payload if isinstance(payload, list) else [payload]
                seen: set[str] = set()
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    path = str(row.get("Path", "")).strip()
                    score = str(row.get("Weight", "")).strip()
                    if not path or path in seen or not os.path.isdir(path):
                        continue
                    seen.add(path)
                    label = f"[{score}] {path}" if score else path
                    if len(label) > 120:
                        label = f"{label[:117]}..."
                    options.append({"text": label, "value": path})
    except Exception as exc:
        logger.warning("[cw] 查询 ZLocation 失败: %s", exc)

    if not options:
        cwd = os.getcwd()
        options.append({"text": f"[current] {cwd}", "value": cwd})
    return options


def _extract_workspace_selection(action_value: Dict[str, Any], event: Any) -> str:
    value = action_value.get("dynamic_list")
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, str):
            return first.strip()
        if isinstance(first, dict):
            candidate = str(first.get("value", "")).strip()
            if candidate:
                return candidate

    action_obj = getattr(event, "action", None) if event else None
    form_value = getattr(action_obj, "form_value", None) if action_obj else None
    if isinstance(form_value, dict):
        for key in ("dynamic_list",):
            raw = form_value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
            if isinstance(raw, list) and raw:
                first = raw[0]
                if isinstance(first, str):
                    return first.strip()
                if isinstance(first, dict):
                    candidate = str(first.get("value", "")).strip()
                    if candidate:
                        return candidate
            if isinstance(raw, dict):
                candidate = str(raw.get("value", "")).strip()
                if candidate:
                    return candidate

        for nested in form_value.values():
            if isinstance(nested, dict):
                raw_nested = nested.get("dynamic_list")
                if isinstance(raw_nested, str) and raw_nested.strip():
                    return raw_nested.strip()
    return ""


def _extract_reject_reason(action_value: Dict[str, Any], event: Any) -> str:
    reason = str(action_value.get("reject_reason", "")).strip()
    if reason:
        return reason
    action_obj = getattr(event, "action", None) if event else None
    form_value = getattr(action_obj, "form_value", None) if action_obj else None
    if isinstance(form_value, dict):
        reason = str(form_value.get("reject_reason", "")).strip()
        if reason:
            return reason
        for value in form_value.values():
            if isinstance(value, dict):
                reason = str(value.get("reject_reason", "")).strip()
                if reason:
                    return reason
    return ""


def _resolve_session(session_key: str) -> Optional[BridgeSession]:
    with _sessions_lock:
        return _sessions.get(session_key)


def _build_approval_preview(method: str, params: dict[str, Any]) -> str:
    if method == "item/commandExecution/requestApproval":
        command = str(params.get("command", "")).strip()
        cwd = str(params.get("cwd", "")).strip()
        if cwd:
            return f"{command}\n[cwd] {cwd}"
        return command or "(empty command)"

    if method == "item/fileChange/requestApproval":
        item = params.get("item")
        if isinstance(item, dict):
            changes = item.get("changes")
            if isinstance(changes, list):
                paths = []
                for change in changes:
                    if isinstance(change, dict):
                        path = str(change.get("path", "")).strip()
                        if path:
                            paths.append(path)
                if paths:
                    return "文件变更:\n" + "\n".join(f"- {p}" for p in paths[:20])
        return "文件变更（详情请在 Codex 输出中查看）"

    if method.endswith("/requestApproval") and ("tool" in method.lower() or "mcp" in method.lower()):
        tool_name = ""
        args_obj: Any = None

        for key in ("tool", "tool_name", "name", "server_tool_name"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                tool_name = value.strip()
                break
        for key in ("arguments", "args", "input"):
            if key in params:
                args_obj = params.get(key)
                break

        item = params.get("item")
        if isinstance(item, dict):
            if not tool_name:
                for key in ("tool", "tool_name", "name", "server_tool_name"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        tool_name = value.strip()
                        break
            if args_obj is None:
                for key in ("arguments", "args", "input"):
                    if key in item:
                        args_obj = item.get(key)
                        break

        tool_title = tool_name or "unknown_tool"
        if args_obj is None:
            return f"工具调用授权: {tool_title}\n[method] {method}"
        try:
            args_text = json.dumps(args_obj, ensure_ascii=False)
        except Exception:
            args_text = str(args_obj)
        if len(args_text) > 1000:
            args_text = args_text[:1000] + "..."
        return f"工具调用授权: {tool_title}\n[method] {method}\n[args] {args_text}"

    return method


def _extract_tool_preview_from_notification(method: str, params: dict[str, Any]) -> tuple[str, bool, str]:
    def _short(s: str, max_len: int = 160) -> str:
        t = str(s or "").strip().replace("\r", " ").replace("\n", " ")
        if len(t) > max_len:
            return t[: max_len - 3] + "..."
        return t

    if method == "item/commandExecution/requestApproval":
        command = _short(str(params.get("command", "")))
        key = str(params.get("itemId", "")).strip() or command
        if command:
            return (f"命令等待授权：{command}", True, f"approval:{key}")

    if method == "codex/event/exec_approval_request":
        msg = params.get("msg")
        if isinstance(msg, dict):
            cmd = msg.get("command")
            command = ""
            if isinstance(cmd, list):
                command = _short(" ".join(str(x) for x in cmd if str(x).strip()))
            else:
                command = _short(str(cmd or ""))
            call_id = str(msg.get("call_id", "")).strip() or command
            if command:
                return (f"命令等待授权：{command}", True, f"approval:{call_id}")

    if method == "codex/event/exec_command_begin":
        msg = params.get("msg")
        if isinstance(msg, dict):
            cmd = msg.get("command")
            command = ""
            if isinstance(cmd, list):
                command = _short(" ".join(str(x) for x in cmd if str(x).strip()))
            else:
                command = _short(str(cmd or ""))
            call_id = str(msg.get("call_id", "")).strip() or command
            if command:
                return (f"开始执行命令：{command}", True, f"begin:{call_id}")

    if method == "codex/event/exec_command_end":
        msg = params.get("msg")
        if isinstance(msg, dict):
            cmd = msg.get("command")
            command = ""
            if isinstance(cmd, list):
                command = _short(" ".join(str(x) for x in cmd if str(x).strip()))
            else:
                command = _short(str(cmd or ""))
            call_id = str(msg.get("call_id", "")).strip() or command
            exit_code = str(msg.get("exit_code", "")).strip()
            if command:
                exit_part = f"（exit={exit_code}）" if exit_code else ""
                return (f"命令执行完成{exit_part}：{command}", True, f"end:{call_id}")

    if "mcp_tool_call" in method and method.endswith("_begin"):
        tool_name = ""
        args_obj: Any = None
        item = params.get("item")
        if isinstance(item, dict):
            for key in ("tool", "tool_name", "name", "server_tool_name"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    tool_name = value.strip()
                    break
            for key in ("arguments", "args", "input"):
                if key in item:
                    args_obj = item.get(key)
                    break
        if not tool_name:
            for key in ("tool", "tool_name", "name", "server_tool_name"):
                value = params.get(key)
                if isinstance(value, str) and value.strip():
                    tool_name = value.strip()
                    break
        if args_obj is None:
            for key in ("arguments", "args", "input"):
                if key in params:
                    args_obj = params.get(key)
                    break

        preview = f"正在执行工具：{tool_name or 'unknown'}"
        if isinstance(args_obj, dict):
            command = _short(str(args_obj.get("command", "")))
            if command:
                preview += f"\n命令：{command}"
        return (preview, True, f"mcp_begin:{tool_name or 'unknown'}")

    if "mcp_tool_call" in method and method.endswith("_end"):
        tool_name = ""
        item = params.get("item")
        if isinstance(item, dict):
            for key in ("tool", "tool_name", "name", "server_tool_name"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    tool_name = value.strip()
                    break
        if not tool_name:
            for key in ("tool", "tool_name", "name", "server_tool_name"):
                value = params.get(key)
                if isinstance(value, str) and value.strip():
                    tool_name = value.strip()
                    break
        preview = f"工具执行完成：{tool_name or 'unknown'}"
        return (preview, True, f"mcp_end:{tool_name or 'unknown'}")

    return ("", False, "")


def _extract_reasoning_delta(method: str, params: dict[str, Any]) -> str:
    # 只使用一类 delta 事件，避免同一 token 在不同事件通道重复累加。
    if method == "item/reasoning/summaryTextDelta":
        return str(params.get("delta", ""))
    return ""


def _build_think_summary(reasoning_buffer: str, max_lines: int = 3, max_chars: int = 280) -> str:
    normalized = str(reasoning_buffer or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if not lines:
        return ""
    summary = "\n".join(lines[:max_lines]).strip()
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3] + "..."
    return summary


def _extract_command_event(method: str, params: dict[str, Any]) -> Optional[dict[str, str]]:
    def _short_text(raw: str, max_chars: int = 240) -> str:
        text = str(raw or "").replace("\r", " ").replace("\n", " ").strip()
        if len(text) > max_chars:
            return text[: max_chars - 3] + "..."
        return text

    if method == "item/commandExecution/requestApproval":
        command = _short_text(str(params.get("command", "")))
        call_id = str(params.get("itemId", "")).strip() or command
        if command:
            return {"phase": "approval", "call_id": call_id, "command": command}

    if method == "codex/event/exec_approval_request":
        msg = params.get("msg")
        if isinstance(msg, dict):
            cmd = msg.get("command")
            command = " ".join(str(x) for x in cmd) if isinstance(cmd, list) else str(cmd or "")
            command = _short_text(command)
            call_id = str(msg.get("call_id", "")).strip() or command
            if command:
                return {"phase": "approval", "call_id": call_id, "command": command}

    if method == "codex/event/exec_command_begin":
        msg = params.get("msg")
        if isinstance(msg, dict):
            cmd = msg.get("command")
            command = " ".join(str(x) for x in cmd) if isinstance(cmd, list) else str(cmd or "")
            command = _short_text(command)
            call_id = str(msg.get("call_id", "")).strip() or command
            if command:
                return {"phase": "begin", "call_id": call_id, "command": command}

    if method == "codex/event/exec_command_end":
        msg = params.get("msg")
        if isinstance(msg, dict):
            cmd = msg.get("command")
            command = " ".join(str(x) for x in cmd) if isinstance(cmd, list) else str(cmd or "")
            command = _short_text(command)
            call_id = str(msg.get("call_id", "")).strip() or command
            exit_code = str(msg.get("exit_code", "")).strip()
            raw_out = str(msg.get("formatted_output") or msg.get("aggregated_output") or msg.get("stdout") or "")
            out_lines = [line.strip() for line in raw_out.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]
            out_preview = "\n".join(out_lines[:3]) if out_lines else ""
            out_preview = _short_text(out_preview, 300)
            return {
                "phase": "end",
                "call_id": call_id,
                "command": command,
                "exit_code": exit_code,
                "output_preview": out_preview,
            }

    return None


def _wait_human_approval(session_key: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    session = _resolve_session(session_key)
    if session is None or _platform is None:
        logger.warning("[approval] 会话或平台不可用，默认拒绝: session=%s", session_key)
        return {"decision": "decline"}

    preview = _build_approval_preview(method, params)
    chat_id = session.chat_id
    chat_type = session.chat_type
    source_message_id = session.last_message_id
    card_message_id = _platform.send_authorization_card(
        chat_id=chat_id,
        chat_type=chat_type,
        message_id=source_message_id,
        command_content=preview,
        input_content=method,
    )
    if not card_message_id:
        logger.warning("[approval] 授权卡片发送失败，默认拒绝: session=%s", session_key)
        return {"decision": "decline"}

    pending = PendingApproval(
        session_key=session_key,
        method=method,
        chat_type=chat_type,
        chat_id=chat_id,
        source_message_id=source_message_id,
        command_preview=preview,
    )
    with _pending_approval_lock:
        _pending_approval_cards[card_message_id] = pending
        _pending_approval_latest_by_chat[chat_id] = card_message_id

    timeout_seconds = 300
    pending.event.wait(timeout=timeout_seconds)

    with _pending_approval_lock:
        _pending_approval_cards.pop(card_message_id, None)
        latest = _pending_approval_latest_by_chat.get(chat_id)
        if latest == card_message_id:
            _pending_approval_latest_by_chat.pop(chat_id, None)

    if not pending.event.is_set():
        _platform.update_authorization_card_result(card_message_id, preview, "授权超时，已自动拒绝")
        _platform.send_message("授权超时，已自动拒绝。", chat_id, chat_type, source_message_id)
        return {"decision": "decline"}

    if pending.decision == "accept":
        _platform.update_authorization_card_result(card_message_id, preview, "已授权执行")
        return {"decision": "accept"}

    reason = pending.reject_reason or "用户拒绝"
    _platform.update_authorization_card_result(card_message_id, preview, f"已拒绝授权\n原因: {reason}")
    return {"decision": "decline"}


def _format_request_user_input_prompt(questions: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("需要你补充信息，请按格式回复。")
    lines.append("格式：question_id: 你的答案")
    lines.append("单个问题也可直接回复答案。")
    for q in questions:
        qid = str(q.get("id", "")).strip()
        header = str(q.get("header", "")).strip()
        question = str(q.get("question", "")).strip()
        lines.append("")
        lines.append(f"[{qid}] {header}")
        lines.append(question)
        options = q.get("options")
        if isinstance(options, list) and options:
            for idx, opt in enumerate(options, start=1):
                if not isinstance(opt, dict):
                    continue
                label = str(opt.get("label", "")).strip()
                desc = str(opt.get("description", "")).strip()
                if label:
                    if desc:
                        lines.append(f"{idx}. {label} - {desc}")
                    else:
                        lines.append(f"{idx}. {label}")
    return "\n".join(lines).strip()


def _is_approval_like_user_input(questions: list[dict[str, Any]]) -> bool:
    labels: set[str] = set()
    for q in questions:
        options = q.get("options")
        if not isinstance(options, list):
            continue
        for opt in options:
            if not isinstance(opt, dict):
                continue
            label = str(opt.get("label", "")).strip().lower()
            if label:
                labels.add(label)
    if not labels:
        return False
    return bool(labels & {"accept", "decline", "cancel"})


def _parse_request_user_input_answers(raw_text: str, questions: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    text = (raw_text or "").strip()
    by_id: dict[str, dict[str, Any]] = {}
    for q in questions:
        qid = str(q.get("id", "")).strip()
        if qid:
            by_id[qid] = q

    result: dict[str, dict[str, list[str]]] = {}
    if not text:
        for qid in by_id:
            result[qid] = {"answers": []}
        return result

    line_pairs: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip()
        value = v.strip()
        if key and key in by_id:
            line_pairs[key] = value

    if not line_pairs and len(by_id) == 1:
        only_id = next(iter(by_id.keys()))
        line_pairs[only_id] = text

    for qid, q in by_id.items():
        raw_answer = line_pairs.get(qid, "").strip()
        options = q.get("options")
        if raw_answer and isinstance(options, list) and options:
            if raw_answer.isdigit():
                idx = int(raw_answer)
                if 1 <= idx <= len(options):
                    opt = options[idx - 1]
                    if isinstance(opt, dict):
                        raw_answer = str(opt.get("label", "")).strip() or raw_answer
            else:
                normalized = raw_answer.lower()
                for opt in options:
                    if not isinstance(opt, dict):
                        continue
                    label = str(opt.get("label", "")).strip()
                    if label and label.lower() == normalized:
                        raw_answer = label
                        break
        result[qid] = {"answers": [raw_answer] if raw_answer else []}
    return result


def _wait_request_user_input(session_key: str, params: dict[str, Any]) -> dict[str, Any]:
    session = _resolve_session(session_key)
    if session is None or _platform is None:
        return {"answers": {}}

    raw_questions = params.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        return {"answers": {}}

    questions: list[dict[str, Any]] = []
    for item in raw_questions:
        if isinstance(item, dict):
            qid = str(item.get("id", "")).strip()
            if not qid:
                continue
            question = {
                "id": qid,
                "header": str(item.get("header", "")).strip(),
                "question": str(item.get("question", "")).strip(),
                "options": item.get("options"),
            }
            questions.append(question)
    if not questions:
        return {"answers": {}}

    approval_like = _is_approval_like_user_input(questions)
    if approval_like:
        logger.info(
            "[approval] MCP tool approval via requestUserInput: session=%s question_count=%s",
            session_key,
            len(questions),
        )

    prompt = _format_request_user_input_prompt(questions)
    pending = PendingUserInput(
        session_key=session_key,
        chat_type=session.chat_type,
        chat_id=session.chat_id,
        source_message_id=session.last_message_id,
        questions=questions,
    )
    with _pending_user_input_lock:
        _pending_user_inputs[session_key] = pending

    _platform.send_message(prompt, session.chat_id, session.chat_type, session.last_message_id)
    timeout_seconds = 300
    pending.event.wait(timeout=timeout_seconds)

    with _pending_user_input_lock:
        current = _pending_user_inputs.get(session_key)
        if current is pending:
            _pending_user_inputs.pop(session_key, None)

    if not pending.event.is_set():
        _platform.send_message("输入超时，已返回空答案。", session.chat_id, session.chat_type, session.last_message_id)
        return {"answers": _parse_request_user_input_answers("", questions)}

    return {"answers": _parse_request_user_input_answers(pending.raw_text, questions)}


def _build_request_handler(session_key: str) -> Any:
    def _handler(method: str, params: dict[str, Any]) -> dict[str, Any]:
        logger.info("[codex] 收到 server request: method=%s session=%s", method, session_key)
        if method.endswith("/requestApproval"):
            decision = _wait_human_approval(session_key, method, params)
            if decision.get("decision") == "accept":
                return {"decision": "accept"}
            return {"decision": "decline"}
        if method == "item/tool/requestUserInput":
            q = params.get("questions")
            q_count = len(q) if isinstance(q, list) else 0
            logger.info("[codex] route requestUserInput: session=%s question_count=%s", session_key, q_count)
            return _wait_request_user_input(session_key, params)
        return {"decision": "accept"}

    return _handler


def _create_client_for_session(session_key: str, workspace_dir: str) -> CodexAppServerClient:
    model = _resolve_codex_model()
    timeout = int((_config.timeout if _config else 300) or 300)
    client = CodexAppServerClient(
        workspace_dir=workspace_dir,
        model=model,
        timeout=timeout,
        request_handler=_build_request_handler(session_key),
    )
    client.start()
    return client


def _start_new_thread(session: BridgeSession) -> str:
    params: dict[str, Any] = {"cwd": session.workspace_dir}
    model = _resolve_codex_model()
    if model:
        params["model"] = model
    result = session.client.request("thread/start", params=params, timeout=30)
    thread = result.get("thread")
    if not isinstance(thread, dict):
        raise RuntimeError("thread/start 返回缺少 thread")
    thread_id = str(thread.get("id", "")).strip()
    if not thread_id:
        raise RuntimeError("thread/start 返回缺少 thread.id")
    session.thread_id = thread_id
    logger.info("[codex] 已创建新线程: session=%s thread_id=%s", _build_session_key(session.chat_type, session.chat_id), thread_id)
    return thread_id


def _close_session(session: BridgeSession) -> None:
    try:
        session.client.close()
    except Exception:
        logger.exception("[session] 关闭 Codex 客户端失败")


def _get_or_create_session(chat_type: str, chat_id: str) -> BridgeSession:
    session_key = _build_session_key(chat_type, chat_id)
    with _sessions_lock:
        existing = _sessions.get(session_key)
        if existing is not None:
            return existing

    workspace_dir = os.getcwd()
    client = _create_client_for_session(session_key, workspace_dir)
    session = BridgeSession(
        chat_type=chat_type,
        chat_id=chat_id,
        workspace_dir=workspace_dir,
        client=client,
    )
    with session.lock:
        _start_new_thread(session)

    with _sessions_lock:
        _sessions[session_key] = session
    logger.info("[session] 创建新会话: %s cwd=%s", session_key, workspace_dir)
    return session


def _clear_session_thread(session: BridgeSession) -> str:
    with session.lock:
        if session.active_turn_id and session.thread_id:
            try:
                session.client.request(
                    "turn/interrupt",
                    params={"threadId": session.thread_id, "turnId": session.active_turn_id},
                    timeout=10,
                )
            except Exception:
                logger.exception("[clear] 中断当前 turn 失败")
            finally:
                session.active_turn_id = ""
        return _start_new_thread(session)


def _interrupt_active_turn(session: BridgeSession) -> bool:
    with session.lock:
        thread_id = session.thread_id
        turn_id = session.active_turn_id
    if not thread_id or not turn_id:
        return False

    session.client.request(
        "turn/interrupt",
        params={"threadId": thread_id, "turnId": turn_id},
        timeout=10,
    )
    return True


def _switch_workspace(chat_type: str, chat_id: str, workspace_dir: str) -> BridgeSession:
    session_key = _build_session_key(chat_type, chat_id)

    old_session: Optional[BridgeSession] = None
    with _sessions_lock:
        old_session = _sessions.pop(session_key, None)
    if old_session is not None:
        _close_session(old_session)

    client = _create_client_for_session(session_key, workspace_dir)
    new_session = BridgeSession(
        chat_type=chat_type,
        chat_id=chat_id,
        workspace_dir=workspace_dir,
        client=client,
    )
    with new_session.lock:
        _start_new_thread(new_session)

    with _sessions_lock:
        _sessions[session_key] = new_session
    logger.info("[cw] 切换会话目录成功: session=%s cwd=%s", session_key, workspace_dir)
    return new_session


def _execute_turn_async(task: str, chat_type: str, chat_id: str, message_id: str) -> None:
    if _platform is None:
        return

    platform = _platform
    session = _get_or_create_session(chat_type, chat_id)
    collector = TurnCollector()
    handler_id = session.client.add_notification_handler(collector.on_notification)
    progress_state: dict[str, Any] = {
        "pushed_keys": set(),
        "business_round": 1,
        "round_reasoning_buffer": "",
        "round_think_sent": set(),
        "last_event_ts": time.time(),
    }

    def _progress_handler(method: str, params: dict[str, Any]) -> None:
        progress_state["last_event_ts"] = time.time()
        delta = _extract_reasoning_delta(method, params)
        if delta:
            buf = str(progress_state.get("round_reasoning_buffer", "")) + delta
            if len(buf) > 3000:
                buf = buf[-3000:]
            progress_state["round_reasoning_buffer"] = buf

        pushed_keys = progress_state.get("pushed_keys")
        if not isinstance(pushed_keys, set):
            pushed_keys = set()
            progress_state["pushed_keys"] = pushed_keys

        round_no = int(progress_state.get("business_round", 1) or 1)
        think_sent_rounds = progress_state.get("round_think_sent")
        if not isinstance(think_sent_rounds, set):
            think_sent_rounds = set()
            progress_state["round_think_sent"] = think_sent_rounds

        def _push_round_think_summary() -> None:
            if round_no in think_sent_rounds:
                return
            summary = _build_think_summary(str(progress_state.get("round_reasoning_buffer", "")))
            if not summary:
                return
            think_sent_rounds.add(round_no)
            platform.send_message(f"[轮次 {round_no}] 思考摘要（前3行）:\n{summary}", chat_id, chat_type, message_id)

        def _advance_round() -> None:
            progress_state["business_round"] = round_no + 1
            progress_state["round_reasoning_buffer"] = ""

        command_event = _extract_command_event(method, params)
        if command_event is not None:
            call_id = str(command_event.get("call_id", "")).strip() or "unknown"
            phase = str(command_event.get("phase", "")).strip() or "event"
            event_key = f"cmd:{phase}:{call_id}"
            if event_key in pushed_keys:
                return
            pushed_keys.add(event_key)

            if phase == "approval":
                return
            if phase == "begin":
                return
            if phase == "end":
                _push_round_think_summary()
                command = str(command_event.get("command", "")).strip()
                exit_code = str(command_event.get("exit_code", "")).strip()
                output_preview = str(command_event.get("output_preview", "")).strip()
                head = f"[轮次 {round_no}] 命令执行完成（exit={exit_code or '-' }）：{command}"
                if output_preview:
                    platform.send_message(f"{head}\n输出摘要:\n{output_preview}", chat_id, chat_type, message_id)
                else:
                    platform.send_message(head, chat_id, chat_type, message_id)
                _advance_round()
                return

        text, immediate, key = _extract_tool_preview_from_notification(method, params)
        if text and immediate:
            tool_key = key or f"tool:{method}"
            event_key = f"tool:{tool_key}"
            if event_key in pushed_keys:
                return
            _push_round_think_summary()
            pushed_keys.add(event_key)
            platform.send_message(f"[轮次 {round_no}] {text}", chat_id, chat_type, message_id)
            if tool_key.startswith("mcp_end:"):
                _advance_round()

    progress_handler_id = session.client.add_notification_handler(_progress_handler)

    try:
        with session.lock:
            if not session.thread_id:
                _start_new_thread(session)
            if session.active_turn_id:
                platform.send_message("当前会话仍在执行，请稍后再试。", chat_id, chat_type, message_id)
                return

            params: dict[str, Any] = {
                "threadId": session.thread_id,
                "input": [{"type": "text", "text": task}],
                "cwd": session.workspace_dir,
            }
            model = _resolve_codex_model()
            if model:
                params["model"] = model
            result = session.client.request("turn/start", params=params)
            turn = result.get("turn")
            if not isinstance(turn, dict):
                raise RuntimeError("turn/start 返回缺少 turn")
            turn_id = str(turn.get("id", "")).strip()
            if not turn_id:
                raise RuntimeError("turn/start 返回缺少 turn.id")
            collector.bind_turn(turn_id)
            session.active_turn_id = turn_id

        timeout = int((_config.timeout if _config else 300) or 300)
        while True:
            if collector.done_event.wait(timeout=1):
                break
            last_event_ts_raw = progress_state.get("last_event_ts")
            try:
                last_event_ts = float(last_event_ts_raw)
            except Exception:
                last_event_ts = time.time()
            idle_seconds = time.time() - last_event_ts
            if idle_seconds >= timeout:
                raise TimeoutError(f"turn 空闲超时（{timeout}s）")

        if collector.status == "interrupted":
            platform.send_message("已停止当前执行。", chat_id, chat_type, message_id)
            return
        if collector.status == "failed":
            err = collector.error_message or "未知错误"
            platform.send_message(f"执行失败：{err}", chat_id, chat_type, message_id)
            return

        final_round_no = int(progress_state.get("business_round", 1) or 1)
        final_summary = _build_think_summary(str(progress_state.get("round_reasoning_buffer", "")))
        think_sent_rounds = progress_state.get("round_think_sent")
        if not isinstance(think_sent_rounds, set):
            think_sent_rounds = set()
            progress_state["round_think_sent"] = think_sent_rounds
        if final_summary and final_round_no not in think_sent_rounds:
            think_sent_rounds.add(final_round_no)
            platform.send_message(
                f"[轮次 {final_round_no}] 思考摘要（前3行）:\n{final_summary}",
                chat_id,
                chat_type,
                message_id,
            )

        content = collector.render_text()
        if not content:
            content = "已完成，但没有可展示的文本输出。"
        _send_text(platform, content, chat_id, chat_type, message_id)
    except Exception as exc:
        logger.exception("[turn] 执行失败")
        platform.send_message(f"执行失败：{exc}", chat_id, chat_type, message_id)
    finally:
        session.client.remove_notification_handler(handler_id)
        session.client.remove_notification_handler(progress_handler_id)
        with session.lock:
            session.active_turn_id = ""


def _process_workspace_selection_async(card_message_id: str, selected_path: str) -> None:
    with _pending_workspace_lock:
        ws_ctx = _pending_workspace_cards.pop(card_message_id, None)
        if ws_ctx:
            _pending_workspace_latest_by_chat.pop(ws_ctx["chat_id"], None)
    if ws_ctx is None:
        logger.warning("[cw] 未找到卡片上下文: %s", card_message_id)
        return

    platform: FeishuPlatform = ws_ctx["platform"]
    chat_type = str(ws_ctx["chat_type"])
    chat_id = str(ws_ctx["chat_id"])
    source_message_id = str(ws_ctx["source_message_id"])
    allowed_paths = set(ws_ctx.get("allowed_paths", []))

    selected = (selected_path or "").strip()
    if not selected:
        platform.update_workspace_selection_card_result(card_message_id, "切换失败：未选择目录。")
        platform.send_message("未选择目录，请重新发送 /cw", chat_id, chat_type, source_message_id)
        return
    if allowed_paths and selected not in allowed_paths:
        platform.update_workspace_selection_card_result(card_message_id, f"切换失败：目录不在候选列表。\n`{selected}`")
        platform.send_message("目录不在候选列表，请重新发送 /cw", chat_id, chat_type, source_message_id)
        return
    if not os.path.isdir(selected):
        platform.update_workspace_selection_card_result(card_message_id, f"切换失败：目录不存在。\n`{selected}`")
        platform.send_message("目录不存在，请重新发送 /cw", chat_id, chat_type, source_message_id)
        return

    try:
        _switch_workspace(chat_type, chat_id, selected)
        platform.update_workspace_selection_card_result(card_message_id, f"已切换工作目录\n`{selected}`")
        platform.send_message(f"已切换工作目录：{selected}", chat_id, chat_type, source_message_id)
    except Exception as exc:
        logger.exception("[cw] 切换目录失败")
        platform.update_workspace_selection_card_result(card_message_id, f"切换失败：{exc}")
        platform.send_message(f"切换失败：{exc}", chat_id, chat_type, source_message_id)


def _process_approval_action_async(card_message_id: str, decision: str, reject_reason: str) -> None:
    with _pending_approval_lock:
        pending = _pending_approval_cards.get(card_message_id)
    if pending is None:
        logger.warning("[approval] 未找到待处理卡片: %s", card_message_id)
        return
    pending.decision = "accept" if decision == "accept" else "decline"
    pending.reject_reason = reject_reason.strip()
    pending.event.set()


def handle_card_action_trigger(data):
    try:
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )

        event = getattr(data, "event", None)
        if event is None:
            return None
        action = getattr(event, "action", None)
        action_value = getattr(action, "value", None) if action else None
        if isinstance(action_value, str):
            try:
                action_value = json.loads(action_value)
            except Exception:
                action_value = {}
        if not isinstance(action_value, dict):
            action_value = {}

        raw_action = str(action_value.get("action", "")).strip().lower()
        reject_reason = _extract_reject_reason(action_value, event)
        parsed_action = raw_action if raw_action else "unknown"
        context = getattr(event, "context", None) if event else None
        open_message_id = getattr(context, "open_message_id", "") if context else ""
        open_chat_id = getattr(context, "open_chat_id", "") if context else ""

        if not open_message_id and open_chat_id:
            with _pending_workspace_lock:
                open_message_id = _pending_workspace_latest_by_chat.get(open_chat_id, "")
            if not open_message_id:
                with _pending_approval_lock:
                    open_message_id = _pending_approval_latest_by_chat.get(open_chat_id, "")

        if parsed_action in {"approve", "auto_approve"} and open_message_id:
            _executor.submit(_process_approval_action_async, open_message_id, "accept", "")
        elif parsed_action in {"reject", "submit_reject"} and open_message_id:
            _executor.submit(_process_approval_action_async, open_message_id, "decline", reject_reason)
        elif parsed_action == "submit_selection":
            selected_path = _extract_workspace_selection(action_value, event)
            if open_message_id:
                _executor.submit(_process_workspace_selection_async, open_message_id, selected_path)
            else:
                logger.warning("[cw] 缺少 open_message_id，无法处理目录切换")
        else:
            logger.info("[card] 忽略未处理动作: action=%s", parsed_action)

        return P2CardActionTriggerResponse(
            {
                "toast": {
                    "type": "info",
                    "content": "已收到操作，处理中。",
                }
            }
        )
    except Exception:
        logger.exception("处理卡片回调失败")
        return None


def handle_message(data):
    global _platform
    try:
        header = getattr(data, "header", None)
        event_id = getattr(header, "event_id", "") if header else ""
        event_type = getattr(header, "event_type", "") if header else ""
        create_time = getattr(header, "create_time", "") if header else ""
        create_time_readable = _format_event_create_time(create_time)
        logger.info(
            "[event] receive start: event_type=%s event_id=%s create_time=%s",
            event_type,
            event_id,
            create_time_readable or create_time,
        )

        if event_id:
            with _processed_lock:
                if event_id in _processed_event_ids:
                    logger.info("[drop] duplicate event_id=%s", event_id)
                    return
                _processed_event_ids.add(event_id)
                if len(_processed_event_ids) > 2000:
                    _processed_event_ids.clear()

        uuid_val = getattr(data, "uuid", None)
        if uuid_val:
            with _processed_lock:
                if uuid_val in _processed_uuids:
                    logger.info("[drop] duplicate uuid=%s", uuid_val)
                    return
                _processed_uuids.add(uuid_val)
                if len(_processed_uuids) > 1000:
                    _processed_uuids.clear()

        event = getattr(data, "event", None)
        if event is None or not hasattr(event, "message"):
            logger.info("[drop] missing event.message: event_id=%s", event_id)
            return

        message = event.message
        chat_id = getattr(message, "chat_id", "")
        chat_type = getattr(message, "chat_type", "p2p")
        message_id = getattr(message, "message_id", "")
        if not chat_id:
            logger.info("[drop] empty chat_id: event_id=%s message_id=%s", event_id, message_id)
            return

        if message_id:
            with _processed_lock:
                if message_id in _processed_message_ids:
                    logger.info("[drop] duplicate message_id=%s", message_id)
                    return
                _processed_message_ids.add(message_id)
                if len(_processed_message_ids) > 1000:
                    _processed_message_ids.clear()

        sender = getattr(event, "sender", None)
        sender_type = str(getattr(sender, "sender_type", "") if sender else "").lower()
        if sender_type in {"app", "bot"}:
            logger.info("[drop] sender_type=%s message_id=%s", sender_type, message_id)
            return

        content_raw = message.content if hasattr(message, "content") else "{}"
        content = content_raw
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except Exception:
                pass
        text_raw = content.get("text", "") if isinstance(content, dict) else str(content)
        text = _clean_incoming_text(text_raw)
        if not text:
            logger.info("[drop] empty text after clean: message_id=%s raw=%s", message_id, str(text_raw)[:80])
            return

        logger.info(
            "[message] event_id=%s message_id=%s chat=%s:%s create_time=%s text=%s",
            event_id,
            message_id,
            chat_type,
            chat_id,
            create_time_readable,
            text[:120],
        )

        delay_seconds: Optional[float] = None
        if create_time:
            try:
                create_ts = float(create_time) / 1000.0
                delay_seconds = max(0.0, time.time() - create_ts)
            except Exception:
                delay_seconds = None
        if delay_seconds is not None and delay_seconds > _REALTIME_WINDOW_SECONDS:
            logger.info(
                "[drop] stale message: message_id=%s delay=%.3fs window=%ss",
                message_id,
                delay_seconds,
                _REALTIME_WINDOW_SECONDS,
            )
            return

        if _platform is None:
            logger.info("[drop] platform not ready: message_id=%s", message_id)
            return
        platform = _platform
        session_key = _build_session_key(chat_type, chat_id)

        with _pending_user_input_lock:
            pending_user_input = _pending_user_inputs.get(session_key)
        if pending_user_input is not None and not text.startswith("/"):
            logger.info("[pending-input] consume message_id=%s session=%s", message_id, session_key)
            pending_user_input.raw_text = text
            pending_user_input.event.set()
            platform.send_message("已收到输入，继续处理中。", chat_id, chat_type, message_id)
            return

        if _is_change_workspace_command(text):
            dir_options = _query_zlocation_options(limit=10)
            card_message_id = platform.send_workspace_selection_card(
                chat_id=chat_id,
                chat_type=chat_type,
                message_id=message_id,
                dir_list=dir_options,
            )
            if card_message_id:
                with _pending_workspace_lock:
                    _pending_workspace_cards[card_message_id] = {
                        "platform": platform,
                        "chat_type": chat_type,
                        "chat_id": chat_id,
                        "source_message_id": message_id,
                        "allowed_paths": [item.get("value", "") for item in dir_options],
                    }
                    _pending_workspace_latest_by_chat[chat_id] = card_message_id
            else:
                platform.send_message("目录选择卡片发送失败，请稍后重试。", chat_id, chat_type, message_id)
            return

        session_for_meta = _get_or_create_session(chat_type, chat_id)
        with session_for_meta.lock:
            session_for_meta.last_message_id = message_id
        session = session_for_meta

        if _is_clear_command(text):
            try:
                new_thread_id = _clear_session_thread(session)
                platform.send_message(f"已清空上下文，进入新会话：{new_thread_id}", chat_id, chat_type, message_id)
            except Exception as exc:
                platform.send_message(f"清空失败：{exc}", chat_id, chat_type, message_id)
            return

        if _is_stop_command(text):
            try:
                interrupted = _interrupt_active_turn(session)
                if interrupted:
                    platform.send_message("已发送中断请求。", chat_id, chat_type, message_id)
                else:
                    platform.send_message("当前没有正在执行的任务。", chat_id, chat_type, message_id)
            except Exception as exc:
                platform.send_message(f"中断失败：{exc}", chat_id, chat_type, message_id)
            return

        logger.info("[turn] submit: message_id=%s session=%s", message_id, session_key)
        platform.send_message("已收到，开始处理任务。可用 /stop 中断。", chat_id, chat_type, message_id)
        _executor.submit(_execute_turn_async, text, chat_type, chat_id, message_id)
    except Exception:
        logger.exception("处理飞书消息失败")


def main(config: Optional[Config] = None) -> None:
    global _config, _platform, _sessions
    if config is None:
        config = Config.from_env()
    _config = config

    app_id = getattr(config, "webhook_app_id", None) or os.environ.get("WEBHOOK_APP_ID", "")
    app_secret = getattr(config, "webhook_app_secret", None) or os.environ.get("WEBHOOK_APP_SECRET", "")
    if not app_id or not app_secret:
        logger.error("WEBHOOK_APP_ID 或 WEBHOOK_APP_SECRET 未设置")
        return

    _platform = FeishuPlatform(app_id=app_id, app_secret=app_secret)
    with _sessions_lock:
        _sessions = {}

    logger.info("Codex 桥接服务启动：飞书 -> Codex App Server")

    try:
        import lark_oapi as lark

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(handle_message)
            .register_p2_card_action_trigger(handle_card_action_trigger)
            .build()
        )

        cli = lark.ws.Client(
            app_id,
            app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )
        cli.start()
    finally:
        with _sessions_lock:
            sessions = list(_sessions.values())
            _sessions = {}
        for session in sessions:
            _close_session(session)

