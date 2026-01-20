"""极简命令行接口模块"""

import argparse
import os
import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme

from .agent import Executor
from .config import Config
from .llm import create_client

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

import msvcrt

def _clear_input_buffer():
    """清空 stdin 中的残留输入（Windows）"""
    while msvcrt.kbhit():
        msvcrt.getch()


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

    parser.add_argument("--model", "-m", default="qwen3:4b",
                        help="模型名称（默认：qwen3:4b）")

    parser.add_argument("--timeout", "-t", type=int, default=300,
                        help="超时时间（秒，默认：300）")

    parser.add_argument("--host", "-H", default="http://localhost:11434",
                        help="Ollama地址（默认：http://localhost:11434）")

    parser.add_argument("--api-type", "-a", default="ollama",
                        choices=["ollama", "openai"],
                        help="API类型：ollama 或 openai（默认：ollama）")

    parser.add_argument("--base-url", "-b", default="https://api.openai.com/v1",
                        help="OpenAI Base URL（默认：https://api.openai.com/v1）")

    parser.add_argument("--api-key", "-k", default="",
                        help="OpenAI API Key")

    parser.add_argument("--max-tokens", "-M", type=int, default=None,
                        help="最大输出token数（默认：Ollama=1024, OpenAI=8192）")

    parser.add_argument("--verbose", "-v", action="store_true",
                        help="显示详细日志")

    return parser.parse_args()


def check_llm_connection(config: Config) -> bool:
    """检查 LLM 服务连接"""
    try:
        client = create_client(config)
        return client.check_connection()
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
  -m, --model   - 指定模型名称（默认：qwen3:4b）
  -t, --timeout - 超时时间秒（默认：300）
  -a, --api-type- API类型：ollama 或 openai（默认：ollama）
  -H, --host    - Ollama地址（默认：http://localhost:11434）
  -b, --base-url- OpenAI Base URL（默认：https://api.openai.com/v1）
  -k, --api-key - OpenAI API Key
  -M, --max-tokens- 最大输出token（默认：8192，适合大模型）
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

    # 根据 API 类型设置不同的 max_output_tokens 默认值
    # 本地小模型（Ollama）默认 1024，大模型（OpenAI）默认 8192
    if args.api_type == "openai":
        default_max_tokens = 8192
    else:
        default_max_tokens = 1024

    # 用户指定则用用户的，否则用 API 类型对应的默认值
    max_tokens = args.max_tokens if args.max_tokens is not None else default_max_tokens

    # 创建配置
    config = Config(
        api_type=args.api_type,
        ollama_host=args.host,
        openai_base_url=args.base_url,
        openai_api_key=args.api_key,
        model=args.model,
        timeout=args.timeout,
        max_output_tokens=max_tokens,
    )

    # 检查 LLM 连接
    if not check_llm_connection(config):
        service_name = "OpenAI" if config.api_type == "openai" else "Ollama"
        console.print(f"[error]无法连接到 {service_name}[/error]")
        sys.exit(1)

    # print_welcome()  # 调试阶段暂不显示
    console.print(f"[info]模型：{config.model} | 超时：{args.timeout}s[/info]\n")

    if args.task:
        _run_single_task(config, args.task)
        return

    # 交互模式
    while True:
        try:
            _clear_input_buffer()
            task = console.input("[user]请输入任务（q退出）：[/user]")

            if not task:
                continue

            if task.lower() in ["q", "quit", "exit"]:
                console.print("[info]再见！[/info]")
                break

            if task.lower() in ["help", "h"]:
                print_help()
                continue

            _run_single_task(config, task)

        except KeyboardInterrupt:
            console.print("\n\n[info]任务已中断[/info]")
            break

        except Exception as e:
            console.print(f"\n[error]错误：{str(e)}[/error]")
            if args.verbose:
                import traceback
                console.print(traceback.format_exc())


def _run_single_task(config: Config, task: str):
    """执行单个任务"""
    console.print("-" * 60)
    start_time = time.time()

    # 打开日志
    log_file = None
    if os.environ.get("AGENT_LOG_FILE"):
        log_file = open(os.environ["AGENT_LOG_FILE"], "w", encoding="utf-8")

    # 创建执行器
    executor = Executor(config)

    # 用于存储待确认的命令
    pending_command: str | None = None
    waiting_for_confirm = False
    waiting_for_user_input = False  # 新增：等待用户输入标志

    # 执行任务
    for output in executor.run(task):
        if log_file:
            log_file.write(output)
            log_file.flush()

        # 检查是否需要用户确认
        if "<confirm_required>" in output:
            waiting_for_confirm = True
            continue
        if "<confirm_command_end>" in output and pending_command:
            # 等待用户确认
            _clear_input_buffer()
            confirm = console.input("[bold yellow]执行命令[y] / 跳过[n] / 修改建议: [/bold yellow]")
            confirm_lower = confirm.lower().strip()

            if confirm_lower == "y":
                # 用户确认，执行命令
                result = _execute_command(pending_command, config.timeout)

                # 构建明确的结果消息
                if result.returncode == 0:
                    if result.stdout:
                        output = f"命令执行成功，输出：\n{result.stdout}"
                    else:
                        output = "命令执行成功（无输出）"
                else:
                    output = f"命令执行失败（退出码: {result.returncode}）：\n{result.stderr}"

                if executor.current_agent:
                    executor.current_agent._add_message("tool", f'<ps_call_result id="executed">\n{output}\n</ps_call_result>')

            elif confirm_lower == "n":
                console.print("[info]命令已跳过[/info]\n")
                # 发送跳过消息给当前Agent
                if executor.current_agent:
                    executor.current_agent._add_message("user", f'<ps_call_result id="skip">\n命令已跳过\n</ps_call_result>')
            else:
                # 用户输入修改建议
                console.print("[info]已将您的建议发送给 Agent[/info]\n")
                if executor.current_agent:
                    executor.current_agent._add_message("user", f'<ps_call_result id="rejected">\n用户建议：{confirm}\n</ps_call_result>')

            pending_command = None
            waiting_for_confirm = False
            continue

        # 提取命令内容（用于确认）
        if waiting_for_confirm and "命令: " in output:
            pending_command = output.split("命令: ")[1].strip()

        # 检查是否需要用户输入
        if "[等待用户输入]" in output:
            waiting_for_user_input = True

        console.print(output, end="", soft_wrap=True)

    # 如果等待用户输入，继续循环
    while waiting_for_user_input and executor.current_agent:
        console.print("\n[info]请输入内容（空行结束，q 退出）：[/info]")

        lines = []
        while True:
            try:
                _clear_input_buffer()
                line = console.input("  ")

                if line.lower() in ["q", "quit", "exit"]:
                    console.print("[info]任务已终止[/info]")
                    waiting_for_user_input = False
                    break

                if not line:  # 空行，结束输入
                    # 清空当前行的提示信息
                    sys.stdout.write("\r" + " " * 50 + "\r")
                    sys.stdout.flush()
                    console.print("[success]输入已接收，正在处理...[/success]\n", end="")
                    break

                lines.append(line)
            except KeyboardInterrupt:
                console.print("\n[warning]输入已取消[/warning]")
                break

        if not waiting_for_user_input:
            break

        user_input = "\n".join(lines)

        if not user_input:
            console.print("[warning]空输入，跳过[/warning]\n")
            continue

        if log_file:
            log_file.write(f"\n[用户输入]\n{user_input}\n")
            log_file.flush()

        # 继续执行
        waiting_for_user_input = False
        for output in executor.resume(user_input):
            if log_file:
                log_file.write(output)
                log_file.flush()

            # 同样的确认逻辑
            if "<confirm_required>" in output:
                waiting_for_confirm = True
                continue
            if "<confirm_command_end>" in output and pending_command:
                _clear_input_buffer()
                confirm = console.input("[bold yellow]执行命令[y] / 跳过[n] / 修改建议: [/bold yellow]")
                confirm_lower = confirm.lower().strip()

                if confirm_lower == "y":
                    result = _execute_command(pending_command, config.timeout)
                    if result.returncode == 0:
                        if result.stdout:
                            output = f"命令执行成功，输出：\n{result.stdout}"
                        else:
                            output = "命令执行成功（无输出）"
                    else:
                        output = f"命令执行失败（退出码: {result.returncode}）：\n{result.stderr}"
                    if executor.current_agent:
                        executor.current_agent._add_message("user", f'<ps_call_result id="executed">\n{output}\n</ps_call_result>')

                elif confirm_lower == "n":
                    console.print("[info]命令已跳过[/info]\n")
                    if executor.current_agent:
                        executor.current_agent._add_message("user", f'<ps_call_result id="skip">\n命令已跳过\n</ps_call_result>')
                else:
                    console.print("[info]已将您的建议发送给 Agent[/info]\n")
                    if executor.current_agent:
                        executor.current_agent._add_message("user", f'<ps_call_result id="rejected">\n用户建议：{confirm}\n</ps_call_result>')

                pending_command = None
                waiting_for_confirm = False
                continue

            if waiting_for_confirm and "命令: " in output:
                pending_command = output.split("命令: ")[1].strip()

            if "[等待用户输入]" in output:
                waiting_for_user_input = True

            console.print(output, end="", soft_wrap=True)

    if log_file:
        log_file.close()

    console.print("-" * 60)


def _execute_command(command: str, timeout: int):
    """执行命令"""
    import subprocess
    import base64

    # 使用 UTF-16 LE 编码并 Base64 编码命令，避免引号转义问题
    encoded_command = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
    full_cmd = f'powershell -NoProfile -EncodedCommand {encoded_command}'

    process = subprocess.run(
        full_cmd, shell=True, capture_output=True, text=True, encoding='utf-8',
        timeout=timeout
    )
    return process


if __name__ == "__main__":
    main()
