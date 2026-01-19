"""极简命令行接口模块"""

import argparse
import os
import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme

from .agent import SimpleAgent
from .config import Config

# 自定义主题
custom_theme = Theme(
    {
        "user": "bold white",
        "assistant": "italic cyan",
        "command": "bold yellow",
        "success": "green",
        "error": "red",
        "warning": "yellow",
        "info": "dim cyan",
    }
)

console = Console(theme=custom_theme)


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="极简任务执行 Agent - 统一逻辑，支持多级子Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  task-agent "列出当前目录的文件"
  task-agent "安装项目依赖" --timeout 120
  task-agent "帮我重启服务"

特点:
  - 所有Agent逻辑统一，无父子区别
  - 深度优先执行，自动聚合结果
  - 最大支持4层深度（最多16个子Agent）
        """,
    )

    parser.add_argument("task", nargs="?", help="要执行的任务描述")

    parser.add_argument("--model", "-m", default="qwen3-48k:latest",
                        help="模型名称（默认：qwen3-48k:latest）")

    parser.add_argument("--timeout", "-t", type=int, default=300,
                        help="超时时间（秒，默认：300）")

    parser.add_argument("--host", "-H", default="http://localhost:11434",
                        help="Ollama地址（默认：http://localhost:11434）")

    parser.add_argument("--verbose", "-v", action="store_true",
                        help="显示详细日志")

    return parser.parse_args()


def check_ollama_connection(host: str) -> bool:
    """检查Ollama连接"""
    try:
        import requests
        response = requests.get(f"{host}/api/tags", timeout=5)
        return response.ok
    except Exception:
        return False


def print_welcome():
    """打印欢迎信息"""
    console.print(
        Panel(
            Text(
                "极简任务执行 Agent\n\n统一逻辑：创建子Agent → 聚合结果 → 继续/结束\n深度优先执行，自动聚合结果\n最大4层深度（最多16个子Agent）\n输入 q 或 quit 退出",
                justify="center",
                style="bold cyan",
            ),
            title="Simple Agent",
            subtitle="按 Ctrl+C 中断",
        )
    )


def print_help():
    """打印帮助信息"""
    console.print(
        Panel(
            Text(
                """[bold cyan]命令列表[/bold cyan]

[bold yellow]交互模式命令：[/bold yellow]
  help, h      - 显示此帮助信息
  q, quit, exit - 退出程序
  <任务描述>    - 执行指定任务

[bold yellow]命令行参数：[/bold yellow]
  -m, --model   - 指定模型名称（默认：qwen3-48k:latest）
  -t, --timeout - 超时时间秒（默认：300）
  -H, --host    - Ollama地址（默认：http://localhost:11434）
  -v, --verbose - 显示详细日志

[bold yellow]使用示例：[/bold yellow]
  task-agent "列出当前目录的文件"
  task-agent "安装项目依赖" --timeout 120
  task-agent --model qwen2.5:7b "帮我重启服务"

[bold yellow]Agent 特性：[/bold yellow]
  - 统一逻辑，无父子区别
  - 深度优先执行，自动聚合结果
  - 最大支持4层深度（最多16个子Agent）
  - 使用 <ps_call> 执行命令
  - 使用 <create_agent> 创建子Agent
  - 使用 <completion> 标记任务完成""",
                justify="left",
                style="white",
            ),
            title="Task-Agent Help",
            subtitle="按 q 退出",
        )
    )


def main():
    """主函数"""
    args = parse_args()

    # 创建配置
    config = Config(
        ollama_host=args.host,
        model=args.model,
        timeout=args.timeout,
        max_output_tokens=1024,
    )

    # 检查Ollama连接
    if not check_ollama_connection(config.ollama_host):
        console.print(f"[error]无法连接到Ollama：{config.ollama_host}[/error]")
        sys.exit(1)

    print_welcome()
    console.print(f"[info]模型：{config.model} | 超时：{args.timeout}s[/info]\n")

    # 初始化Agent（顶级）
    agent = SimpleAgent(config)

    if args.task:
        _run_single_task(agent, args.task)
        return

    # 交互模式
    while True:
        try:
            task = console.input("[user]请输入任务（q退出）：[/user]")

            if not task:
                continue

            if task.lower() in ["q", "quit", "exit"]:
                console.print("[info]再见！[/info]")
                break

            if task.lower() in ["help", "h"]:
                print_help()
                continue

            _run_single_task(agent, task)

        except KeyboardInterrupt:
            console.print("\n\n[info]任务已中断[/info]")
            break

        except Exception as e:
            console.print(f"\n[error]错误：{str(e)}[/error]")
            if args.verbose:
                import traceback
                console.print(traceback.format_exc())


def _run_single_task(agent: SimpleAgent, task: str):
    """执行单个任务"""
    console.print("-" * 60)
    start_time = time.time()

    # 打开日志
    log_file = None
    if os.environ.get("AGENT_LOG_FILE"):
        log_file = open(os.environ["AGENT_LOG_FILE"], "w", encoding="utf-8")

    # 执行任务（顶级Agent，is_root=True）
    for output in agent.run(task, is_root=True):
        if log_file:
            log_file.write(output)
            log_file.flush()
        console.print(output, end="", soft_wrap=True)

    if log_file:
        log_file.close()

    # 打印摘要
    summary = agent.get_summary()
    duration = time.time() - start_time

    console.print("-" * 60)
    console.print(
        f"\n[success]执行完成[/success]\n"
        f"总耗时：{duration:.2f}秒\n"
        f"Agent: {summary['agent_id']} | 深度: {summary['depth']}\n"
        f"执行命令: {summary['commands']} | 创建子Agent: {summary['sub_agents']}\n"
    )


if __name__ == "__main__":
    main()
