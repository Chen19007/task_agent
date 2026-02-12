"""本地直连 Codex App Server 的调试 CLI。"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from rich.console import Console

from ..config import Config, load_local_env
from .codex_app_server import CodexAppServerClient, TurnCollector


def _auto_accept_handler(method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
        return {"decision": "accept"}
    if method == "item/tool/requestUserInput":
        return {"answers": {}}
    return {"decision": "accept"}


def _start_thread(client: CodexAppServerClient, cwd: str, model: str) -> str:
    params: dict[str, Any] = {"cwd": cwd}
    if model:
        params["model"] = model
    result = client.request("thread/start", params=params, timeout=30)
    thread = result.get("thread")
    if not isinstance(thread, dict):
        raise RuntimeError("thread/start 返回缺少 thread")
    thread_id = str(thread.get("id", "")).strip()
    if not thread_id:
        raise RuntimeError("thread/start 返回缺少 thread.id")
    return thread_id


def _run_turn(
    client: CodexAppServerClient,
    thread_id: str,
    prompt: str,
    cwd: str,
    model: str,
    timeout: int,
) -> str:
    collector = TurnCollector()
    handler_id = client.add_notification_handler(collector.on_notification)
    try:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "cwd": cwd,
        }
        if model:
            params["model"] = model
        result = client.request("turn/start", params=params, timeout=30)
        turn = result.get("turn")
        if not isinstance(turn, dict):
            raise RuntimeError("turn/start 返回缺少 turn")
        turn_id = str(turn.get("id", "")).strip()
        if not turn_id:
            raise RuntimeError("turn/start 返回缺少 turn.id")
        collector.bind_turn(turn_id)

        finished = collector.done_event.wait(timeout=timeout)
        if not finished:
            raise TimeoutError(f"turn 执行超时（{timeout}s）")
        if collector.status == "failed":
            raise RuntimeError(collector.error_message or "turn 执行失败")
        if collector.status == "interrupted":
            return "执行被中断。"
        return collector.render_text() or "已完成，但没有可展示的文本输出。"
    finally:
        client.remove_notification_handler(handler_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="task-agent-codex",
        description="本地直连 Codex App Server，绕过飞书链路。",
    )
    parser.add_argument("message", nargs="+", help="要发送给 Codex 的消息文本")
    parser.add_argument("--cwd", default=os.getcwd(), help="工作目录（默认当前目录）")
    parser.add_argument("--model", default="", help="模型名（默认读取配置）")
    parser.add_argument("--timeout", type=int, default=0, help="执行超时秒数（默认读取配置）")
    return parser


def main() -> None:
    load_local_env(".env", overwrite=False)
    cfg = Config.from_env()
    parser = build_parser()
    args = parser.parse_args()

    cwd = os.path.abspath(str(args.cwd))
    if not os.path.isdir(cwd):
        raise SystemExit(f"工作目录不存在: {cwd}")

    prompt = " ".join(args.message).strip()
    if not prompt:
        raise SystemExit("消息不能为空。")

    model = str(args.model or cfg.model).strip()
    timeout = int(args.timeout or cfg.timeout or 300)
    console = Console()
    client = CodexAppServerClient(
        workspace_dir=cwd,
        model=model,
        timeout=timeout,
        request_handler=_auto_accept_handler,
    )
    try:
        console.print(f"[cyan]启动 Codex 直连调试[/cyan] cwd={cwd}")
        client.start()
        thread_id = _start_thread(client, cwd=cwd, model=model)
        output = _run_turn(client, thread_id=thread_id, prompt=prompt, cwd=cwd, model=model, timeout=timeout)
        console.print(output)
    except Exception as exc:
        console.print(f"[red]执行失败: {exc}[/red]")
        raise SystemExit(1) from exc
    finally:
        client.close()


if __name__ == "__main__":
    main()
