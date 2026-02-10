"""
task-agent-webhook CLI 入口

启动飞书长连接服务，接收消息并执行任务
"""

import argparse
import logging
import os
import sys

import rich.console as console

console_inst = console.Console()

logger = logging.getLogger(__name__)


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="task-agent webhook 服务 - 飞书长连接模式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  task-agent-webhook
  task-agent-webhook --model gpt-4o
  task-agent-webhook --timeout 600 --verbose

环境变量:
  WEBHOOK_APP_ID      - 飞书应用 ID (必需)
  WEBHOOK_APP_SECRET  - 飞书应用密钥 (必需)
        """,
    )

    parser.add_argument("--model", "-m", default="minimax-m2",
                        help="模型名称（默认：minimax-m2）")

    parser.add_argument("--timeout", "-t", type=int, default=300,
                        help="超时时间（秒，默认：300）")

    parser.add_argument("--host", "-H", default="http://localhost:11434",
                        help="Ollama地址（默认：http://localhost:11434）")

    parser.add_argument("--api-type", "-a", default="openai",
                        choices=["ollama", "openai"],
                        help="API类型：ollama 或 openai（默认：openai）")

    parser.add_argument("--base-url", "-b", default="http://localhost:3000/v1",
                        help="OpenAI Base URL（默认：http://localhost:3000/v1）")

    parser.add_argument("--api-key", "-k", default=None,
                        help="OpenAI API Key（可选，未传时读取 OPENAI_API_KEY）")

    parser.add_argument("--max-tokens", "-M", type=int, default=None,
                        help="最大输出token数（默认：Ollama=4096, OpenAI=32768）")

    parser.add_argument("--num-ctx", type=int, default=None,
                        help="上下文窗口大小（Ollama num_ctx，默认 4096）")

    parser.add_argument("--verbose", "-v", action="store_true",
                        help="显示详细日志")

    return parser.parse_args()


def main():
    """task-agent-webhook 命令入口"""
    from ..config import load_local_env

    # 优先从项目本地 .env 加载，避免依赖系统级环境变量
    load_local_env(".env", overwrite=False)
    args = parse_args()

    c = console.Console()
    c.print(
        "\n[bold cyan]╔═══════════════════════════════════════════╗[/bold cyan]"
    )
    c.print("[bold cyan]║[/bold cyan]   [bold cyan]task-agent webhook 服务[/bold cyan]           [bold cyan]║[/bold cyan]")
    c.print(
        "[bold cyan]║[/bold cyan]   [dim]飞书长连接模式 - 无需公网服务器[/dim]    [bold cyan]║[/bold cyan]"
    )
    c.print(
        "[bold cyan]╚═══════════════════════════════════════════╝[/bold cyan]\n"
    )

    # 检查环境变量
    app_id = os.environ.get("WEBHOOK_APP_ID", "")
    app_secret = os.environ.get("WEBHOOK_APP_SECRET", "")

    if not app_id or not app_secret:
        c.print(
            "[error]错误: WEBHOOK_APP_ID 或 WEBHOOK_APP_SECRET 未设置[/error]\n"
        )
        c.print("请先设置环境变量:")
        c.print("  export WEBHOOK_APP_ID='cli_xxx'")
        c.print("  export WEBHOOK_APP_SECRET='xxx'\n")
        c.print("[dim]获取方式:[/dim]")
        c.print("  1. 访问 https://open.feishu.cn")
        c.print("  2. 创建企业自建应用")
        c.print("  3. 在凭证与基础信息页面获取 App ID 和 App Secret")
        c.print("  4. 在事件订阅中选择「使用长连接接收事件」\n")
        sys.exit(1)

    # 根据 API 类型设置不同的 max_output_tokens 默认值
    if args.api_type == "openai":
        default_max_tokens = 8192 * 4
    else:
        default_max_tokens = 4096

    # 根据 API 类型设置不同的 num_ctx 默认值
    if args.api_type == "openai":
        default_num_ctx = 1024 * 200
    else:
        default_num_ctx = 4096

    # 用户指定则用用户的，否则用 API 类型对应的默认值
    max_tokens = args.max_tokens if args.max_tokens is not None else default_max_tokens
    num_ctx = args.num_ctx if args.num_ctx is not None else default_num_ctx

    # 显示配置信息
    c.print(f"[dim]配置信息:[/dim]")
    c.print(f"  - App ID: {app_id[:12]}...")
    c.print(f"  - 模式: 长连接 (无需内网穿透)")
    c.print(f"  - 模型: {args.model}")
    c.print(f"  - API 类型: {args.api_type}")
    c.print(f"  - 超时: {args.timeout}s\n")

    # 尝试检查 LLM 连接
    c.print("[dim]检查 LLM 服务...[/dim]")
    try:
        from ..llm.base import create_client
        from ..config import Config

        api_key = args.api_key if args.api_key else os.environ.get("OPENAI_API_KEY", "")
        config = Config(
            api_type=args.api_type,
            ollama_host=args.host,
            openai_base_url=args.base_url,
            openai_api_key=api_key,
            model=args.model,
            timeout=args.timeout,
            max_output_tokens=max_tokens,
            num_ctx=num_ctx,
        )
        client = create_client(config)

        # 简单测试连接
        if hasattr(client, "_call_api"):
            c.print("[dim]  ✓ LLM 客户端已初始化[/dim]")
    except Exception as e:
        c.print(f"[warning]  ⚠ LLM 连接警告: {e}[/warning]")
        c.print("[dim]    (服务启动后会在调用时重试)[/dim]\n")
    else:
        c.print("")

    c.print("[bold green]✓ 配置检查完成，启动长连接服务...[/bold green]\n")
    c.print("[dim]重要提示:[/dim]")
    c.print("  1. [bold]先启动本服务[/bold]，然后再去飞书后台配置")
    c.print("  2. 飞书后台 → 事件订阅 → 选择「使用长连接接收事件」")
    c.print("  3. 添加事件: im.message.receive_v1")
    c.print("  4. 保存后，本服务会自动连接到飞书平台")
    c.print("  5. 将机器人添加到私聊或群聊，@机器人 发送任务\n")

    # 启动服务器
    try:
        from .server import main as server_main

        server_main(config=config)
    except KeyboardInterrupt:
        c.print("\n[yellow]服务已停止[/yellow]")
        sys.exit(0)
    except Exception as e:
        c.print(f"\n[error]启动失败: {e}[/error]")
        logger.exception("服务启动异常")
        sys.exit(1)


if __name__ == "__main__":
    main()
