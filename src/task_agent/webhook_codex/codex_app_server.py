"""Codex App Server JSON-RPC 客户端封装。"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


def _short_text(value: Any, limit: int = 300) -> str:
    try:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    except Exception:
        text = str(value)
    text = text.replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _extract_command_preview(method: str, params: dict[str, Any]) -> str:
    command = ""
    cwd = ""
    if method == "item/commandExecution/requestApproval":
        command = str(params.get("command", "")).strip()
        cwd = str(params.get("cwd", "")).strip()
        item = params.get("item")
        if isinstance(item, dict):
            if not command:
                command = str(item.get("command", "")).strip()
            if not cwd:
                cwd = str(item.get("cwd", "")).strip()
    if command:
        return f"command={_short_text(command)} cwd={_short_text(cwd, 120) if cwd else '-'}"
    return ""


def _extract_mcp_tool_call_preview(params: dict[str, Any]) -> tuple[str, str]:
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

    if isinstance(args_obj, dict) and "command" in args_obj:
        return tool_name, f"command={_short_text(args_obj.get('command'))}"
    if args_obj is not None:
        return tool_name, _short_text(args_obj)
    return tool_name, ""


class JsonRpcError(RuntimeError):
    """JSON-RPC 调用错误。"""

    def __init__(self, method: str, code: int, message: str):
        super().__init__(f"{method} 失败: code={code}, message={message}")
        self.method = method
        self.code = code
        self.message = message


@dataclass
class TurnCollector:
    """收集单个 turn 的流式输出。"""

    turn_id: Optional[str] = None
    text_parts: list[str] = field(default_factory=list)
    completed_text: Optional[str] = None
    status: str = "inProgress"
    error_message: str = ""
    done_event: threading.Event = field(default_factory=threading.Event)

    def bind_turn(self, turn_id: str) -> None:
        self.turn_id = turn_id

    def on_notification(self, method: str, params: dict[str, Any]) -> None:
        turn_id = str(params.get("turnId", "")).strip()
        if self.turn_id and turn_id and turn_id != self.turn_id:
            return

        if "mcp_tool_call" in method:
            tool_name, preview = _extract_mcp_tool_call_preview(params)
            phase = "begin" if method.endswith("_begin") else ("end" if method.endswith("_end") else "event")
            if preview:
                logger.info(
                    "[codex] turn mcp tool: turn=%s phase=%s tool=%s args=%s",
                    turn_id or "-",
                    phase,
                    tool_name or "unknown",
                    preview,
                )
            else:
                logger.info(
                    "[codex] turn mcp tool: turn=%s phase=%s tool=%s",
                    turn_id or "-",
                    phase,
                    tool_name or "unknown",
                )

        if method == "item/agentMessage/delta":
            delta = str(params.get("delta", ""))
            if delta:
                self.text_parts.append(delta)
            return

        if method == "item/completed":
            item = params.get("item")
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                text = str(item.get("text", "")).strip()
                if text:
                    self.completed_text = text
            return

        if method == "turn/completed":
            turn = params.get("turn")
            if isinstance(turn, dict):
                self.status = str(turn.get("status", "completed"))
                err = turn.get("error")
                if isinstance(err, dict):
                    self.error_message = str(err.get("message", "")).strip()
            self.done_event.set()

    def render_text(self) -> str:
        if self.completed_text:
            return self.completed_text
        return "".join(self.text_parts).strip()


class CodexAppServerClient:
    """面向单个 Codex App Server 进程的客户端。"""

    def __init__(
        self,
        workspace_dir: str,
        model: str = "",
        timeout: int = 300,
        request_handler: Optional[Callable[[str, dict[str, Any]], dict[str, Any]]] = None,
    ):
        self.workspace_dir = workspace_dir
        self.model = model.strip()
        self.timeout = timeout
        self._request_handler = request_handler

        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._id_lock = threading.Lock()
        self._next_id = 1
        self._pending_responses: Dict[int, "queue.Queue[dict[str, Any]]"] = {}
        self._notification_handlers: Dict[int, Callable[[str, dict[str, Any]], None]] = {}
        self._next_handler_id = 1
        self._running = False
        self._trace_lock = threading.Lock()
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        self._trace_file = os.path.join(project_root, "codex_app_trace.jsonl")
        try:
            with open(self._trace_file, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            logger.exception("[codex] 初始化 trace 文件失败")

    def start(self) -> None:
        if self._running:
            return

        if not os.path.isdir(self.workspace_dir):
            raise FileNotFoundError(f"workspace 目录不存在: {self.workspace_dir}")

        codex_bin = shutil.which("codex") or shutil.which("codex.cmd") or shutil.which("codex.exe")
        if not codex_bin:
            raise FileNotFoundError("未找到 codex 可执行文件，请确认已安装并在 PATH 中")

        # 新版 codex 已移除 --listen 参数，直接 app-server 即通过 stdio 通信。
        cmd = [codex_bin, "app-server"]
        logger.info("[codex] 启动 app-server: bin=%s cwd=%s", codex_bin, self.workspace_dir)
        logger.info("[codex] trace file: %s", self._trace_file)
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=self.workspace_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"启动 codex app-server 失败: bin={codex_bin}, cwd={self.workspace_dir}"
            ) from exc
        self._running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True, name="codex_app_reader")
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True, name="codex_app_stderr")
        self._stderr_thread.start()

        init_result = self.request(
            "initialize",
            params={
                "clientInfo": {
                    "name": "task_agent_feishu_bridge",
                    "title": "Task Agent Feishu Bridge",
                    "version": "0.1.0",
                }
            },
            timeout=20,
        )
        logger.info("[codex] initialize 完成: %s", str(init_result)[:200])
        self.notify("initialized", {})

    def close(self) -> None:
        self._running = False
        proc = self._proc
        self._proc = None
        if proc is None:
            return

        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass
        try:
            if proc.stdout and not proc.stdout.closed:
                proc.stdout.close()
        except Exception:
            pass
        try:
            if proc.stderr and not proc.stderr.closed:
                proc.stderr.close()
        except Exception:
            pass

        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def add_notification_handler(self, handler: Callable[[str, dict[str, Any]], None]) -> int:
        with self._state_lock:
            hid = self._next_handler_id
            self._next_handler_id += 1
            self._notification_handlers[hid] = handler
            return hid

    def remove_notification_handler(self, handler_id: int) -> None:
        with self._state_lock:
            self._notification_handlers.pop(handler_id, None)

    def request(self, method: str, params: Optional[dict[str, Any]] = None, timeout: Optional[int] = None) -> dict[str, Any]:
        if not self._running:
            raise RuntimeError("Codex App Server 未启动")

        req_id = self._next_request_id()
        wait_q: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=1)
        with self._state_lock:
            self._pending_responses[req_id] = wait_q

        payload = {"id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        start_ts = time.time()
        logger.info("[codex] request start: id=%s method=%s", req_id, method)
        self._send(payload)

        wait_timeout = timeout if timeout is not None else self.timeout
        try:
            response = wait_q.get(timeout=wait_timeout)
        except queue.Empty as exc:
            with self._state_lock:
                self._pending_responses.pop(req_id, None)
            logger.warning("[codex] request timeout: id=%s method=%s timeout=%ss", req_id, method, wait_timeout)
            raise TimeoutError(f"{method} 超时（{wait_timeout}s）") from exc

        if "error" in response:
            err = response.get("error") or {}
            logger.warning(
                "[codex] request error: id=%s method=%s code=%s message=%s",
                req_id,
                method,
                err.get("code", -1),
                err.get("message", ""),
            )
            raise JsonRpcError(method, int(err.get("code", -1)), str(err.get("message", "")))
        result = response.get("result")
        cost_ms = int((time.time() - start_ts) * 1000)
        logger.info("[codex] request done: id=%s method=%s cost_ms=%s", req_id, method, cost_ms)
        if isinstance(result, dict):
            return result
        return {}

    def notify(self, method: str, params: Optional[dict[str, Any]] = None) -> None:
        payload = {"method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    def _next_request_id(self) -> int:
        with self._id_lock:
            req_id = self._next_id
            self._next_id += 1
            return req_id

    def _send(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("Codex App Server 进程不可用")
        self._trace_append("outgoing", payload)
        line = json.dumps(payload, ensure_ascii=False)
        with self._write_lock:
            proc.stdin.write(line + "\n")
            proc.stdin.flush()

    def _trace_append(self, event_type: str, payload: Any) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "event": event_type,
            "payload": payload,
        }
        try:
            line = json.dumps(record, ensure_ascii=False)
            with self._trace_lock:
                with open(self._trace_file, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            logger.exception("[codex] 写入 trace 失败")

    def _reader_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        while self._running:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    logger.warning("[codex] app-server 已退出，code=%s", proc.returncode)
                    self._fail_all_pending("app-server 已退出")
                    self._running = False
                    return
                time.sleep(0.05)
                continue

            raw = line.strip()
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("[codex] 非法 JSON: %s", raw[:200])
                self._trace_append("incoming_invalid_json", raw)
                continue

            self._trace_append("incoming", message)
            self._dispatch_message(message)

    def _stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        while self._running:
            line = proc.stderr.readline()
            if not line:
                if proc.poll() is not None:
                    return
                time.sleep(0.05)
                continue
            text = line.rstrip()
            if text:
                logger.warning("[codex][stderr] %s", text)
                self._trace_append("stderr", text)

    def _dispatch_message(self, message: dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message):
            raw_response_id = message.get("id")
            response_id: Optional[int] = None
            if isinstance(raw_response_id, int):
                response_id = raw_response_id
            elif isinstance(raw_response_id, str) and raw_response_id.isdigit():
                response_id = int(raw_response_id)

            if response_id is not None:
                logger.info("[codex] response: id=%s has_error=%s", raw_response_id, "error" in message)
                wait_q: Optional["queue.Queue[dict[str, Any]]"] = None
                with self._state_lock:
                    wait_q = self._pending_responses.pop(response_id, None)
                if wait_q is not None:
                    wait_q.put(message)
            return

        method = str(message.get("method", "")).strip()
        if not method:
            return
        params = message.get("params")
        if not isinstance(params, dict):
            params = {}

        if "id" in message:
            request_id = message.get("id")
            if isinstance(request_id, (int, str)):
                logger.info("[codex] server request: id=%s method=%s", request_id, method)
                self._handle_server_request(request_id, method, params)
            else:
                logger.warning("[codex] ignore server request with unsupported id type: %s", type(request_id).__name__)
            return

        if "mcp_tool_call" in method:
            tool_name, preview = _extract_mcp_tool_call_preview(params)
            phase = "begin" if method.endswith("_begin") else ("end" if method.endswith("_end") else "event")
            if preview:
                logger.info("[codex] mcp tool call: phase=%s tool=%s args=%s", phase, tool_name or "unknown", preview)
            else:
                logger.info("[codex] mcp tool call: phase=%s tool=%s", phase, tool_name or "unknown")

        logger.debug("[codex] notification: method=%s", method)
        handlers: list[Callable[[str, dict[str, Any]], None]] = []
        with self._state_lock:
            handlers = list(self._notification_handlers.values())
        for handler in handlers:
            try:
                handler(method, params)
            except Exception:
                logger.exception("[codex] 通知处理异常: method=%s", method)

    def _handle_server_request(self, request_id: Any, method: str, params: dict[str, Any]) -> None:
        result: dict[str, Any] = {}
        error: Optional[dict[str, Any]] = None
        preview = _extract_command_preview(method, params)
        if preview:
            logger.info("[codex] approval request: id=%s method=%s %s", request_id, method, preview)
        try:
            if self._request_handler is not None:
                result = self._request_handler(method, params) or {}
            else:
                result = {"decision": "accept"}
            if method.endswith("/requestApproval"):
                logger.info(
                    "[codex] approval decision: id=%s method=%s decision=%s",
                    request_id,
                    method,
                    _short_text(result.get("decision", ""), 32),
                )
        except Exception as exc:
            error = {"code": -32000, "message": str(exc)}

        if error is not None:
            self._send({"id": request_id, "error": error})
        else:
            self._send({"id": request_id, "result": result})

    def _fail_all_pending(self, reason: str) -> None:
        with self._state_lock:
            pending = list(self._pending_responses.items())
            self._pending_responses.clear()
        for req_id, wait_q in pending:
            wait_q.put({"id": req_id, "error": {"code": -32099, "message": reason}})
