"""task-agent-webhook-codex 命令入口。"""

from __future__ import annotations

import logging
import os
import sys

import rich.console as console

logger = logging.getLogger(__name__)


def main() -> None:
    from ..config import Config, load_local_env
    from .server import main as server_main

    load_local_env(".env", overwrite=False)

    c = console.Console()
    config = Config.from_env()
    app_id, app_secret = config.resolve_webhook_credentials("webhook_codex")
    if not app_id or not app_secret:
        c.print("[error]错误: 未设置 webhook codex 的飞书凭据[/error]")
        c.print("请设置 WEBHOOK_CODEX_APP_ID / WEBHOOK_CODEX_APP_SECRET。")
        sys.exit(1)

    config.webhook_codex_app_id = app_id
    config.webhook_codex_app_secret = app_secret

    c.print("[bold green]启动 Codex 飞书桥接服务...[/bold green]")
    c.print("[dim]本服务仅保留 /cw 本地处理，/clear 与 /stop 映射 Codex 语义。[/dim]")
    try:
        server_main(config=config)
    except KeyboardInterrupt:
        c.print("[yellow]服务已停止。[/yellow]")
    except Exception:
        logger.exception("服务启动失败")
        c.print("[error]服务启动失败，请查看日志。[/error]")
        sys.exit(1)


if __name__ == "__main__":
    main()

