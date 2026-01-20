"""极简命令行接口模块"""

import argparse
import os
import sys
import time

from rich.console import Console
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme

from .agent import Executor
from .config import Config
from .llm import create_client
from .session import SessionManager

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
                        help="最大输出token数（默认：Ollama=4096, OpenAI=8192）")

    parser.add_argument("--num-ctx", type=int, default=None,
                        help="上下文窗口大小（Ollama num_ctx，默认 4096）")


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

[bold yellow]交互模式：[/bold yellow]
  [bold]主循环[/bold] - 等待输入新任务
    提示符: [user]任务>[/user]
    - <任务描述>   - 执行任务（使用当前会话上下文）
    - /new        - 创建新会话（自动保存当前会话）
    - /list       - 列出所有保存的会话
    - /resume <id>- 恢复指定ID的会话
    - q, quit     - 退出程序

  [bold]等待输入模式[/bold] - Agent 询问问题，等待回复
    提示符: [info]>[/info]
    - <内容>      - 输入回复内容（空行结束）
    - q           - 终止当前任务
    - /list       - 查看会话列表

[bold yellow]命令行参数：[/bold yellow]
  -m, --model   - 指定模型名称（默认：qwen3:4b）
  -t, --timeout - 超时时间秒（默认：300）
  -a, --api-type- API类型：ollama 或 openai（默认：ollama）
  -H, --host    - Ollama地址（默认：http://localhost:11434）
  -b, --base-url- OpenAI Base URL（默认：https://api.openai.com/v1）
  -k, --api-key - OpenAI API Key
  -M, --max-tokens- 最大输出token（默认：Ollama=4096, OpenAI=8192，适合大模型）
  --num-ctx        - 上下文窗口大小（Ollama num_ctx，默认 4096）
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
  - 使用 <completion> 标记任务完成

[bold yellow]会话管理：[/bold yellow]
  - 主循环输入任务默认使用当前会话
  - 要创建新会话使用 /new 命令
  - 会话自动保存在 sessions/ 目录""",
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
    # 本地小模型（Ollama）默认 4096，大模型（OpenAI）默认 8192 * 4
    if args.api_type == "openai":
        default_max_tokens = 8192 * 4
    else:
        default_max_tokens = 4096

    # 根据 API 类型设置不同的 num_ctx 默认值
    # 本地小模型（Ollama）默认 4096，大模型（OpenAI）默认 200k
    if args.api_type == "openai":
        default_num_ctx = 1024 * 200
    else:
        default_num_ctx = 4096

    # 用户指定则用用户的，否则用 API 类型对应的默认值
    max_tokens = args.max_tokens if args.max_tokens is not None else default_max_tokens
    num_ctx = args.num_ctx if args.num_ctx is not None else default_num_ctx

    # 创建配置
    config = Config(
        api_type=args.api_type,
        ollama_host=args.host,
        openai_base_url=args.base_url,
        openai_api_key=args.api_key,
        model=args.model,
        timeout=args.timeout,
        max_output_tokens=max_tokens,
        num_ctx=num_ctx,
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
    session_manager = SessionManager()
    executor = Executor(config)  # 保持 Executor 实例，保留上下文

    while True:
        try:
            # 检查是否有待切换的executor（从等待输入循环中的会话操作）
            pending = session_manager.get_pending_executor()
            if pending:
                executor = pending
                if session_manager.current_session_id:
                    console.print(f"\n[dim]已切换到会话: {session_manager.current_session_id}[/dim]\n")
                else:
                    console.print(f"\n[dim]当前会话：临时（未保存）[/dim]\n")

            # 显示当前会话状态
            if session_manager.current_session_id:
                console.print(f"[dim]当前会话 #{session_manager.current_session_id} | 输入任务继续，/new 新建，/list 列表[/dim]")
            else:
                console.print(f"[dim]临时会话 | 输入任务创建会话，/list 列表[/dim]")

            _clear_input_buffer()
            task = console.input("[user]任务> [/user]")

            if not task:
                continue

            if task.lower() in ["q", "quit", "exit"]:
                console.print("[info]再见！[/info]")
                break

            if task.lower() in ["help", "h"]:
                print_help()
                continue

            # 处理会话管理命令
            if task.startswith("/"):
                if task.lower() == "/list":
                    sessions = session_manager.list_sessions()
                    console.print("\n[bold cyan]保存的会话：[/bold cyan]\n")
                    if not sessions:
                        console.print("[dim]  （暂无保存的会话）[/dim]\n")
                    for s in sessions:
                        msg_preview = s.get('first_message', '')
                        if msg_preview:
                            console.print(f"  会话 {s['session_id']} | {s['updated_at'][:19]} | 消息数: {s['message_count']} | 深度: {s['depth']} | [dim]{msg_preview}[/dim]")
                        else:
                            console.print(f"  会话 {s['session_id']} | {s['updated_at'][:19]} | 消息数: {s['message_count']} | 深度: {s['depth']}")
                    console.print("")
                    continue

                if task.lower() == "/new":
                    new_id, new_executor = session_manager.create_new_session(executor)
                    executor = new_executor  # 更新 executor 为新的空实例
                    console.print(f"\n[success]新会话已创建: {new_id}[/success]\n")
                    continue

                parts = task.split()
                if len(parts) == 2 and parts[0].lower() == "/resume":
                    try:
                        session_id = int(parts[1])
                        new_executor = session_manager.load_session(session_id, config)
                        if new_executor:
                            executor = new_executor
                            console.print(f"\n[success]会话已恢复: {session_id}[/success]\n")

                            # 显示历史上下文摘要
                            if executor.current_agent and executor.current_agent.history:
                                console.print("[dim]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/dim]")
                                console.print("[bold cyan]历史上下文：[/bold cyan]\n")

                                history = executor.current_agent.history
                                total = len(history)

                                for i, msg in enumerate(history):
                                    role = msg.role
                                    content = msg.content.strip()

                                    # 跳过系统提示词和会话ID消息
                                    if role == "system":
                                        if content.startswith("你是一个任务执行agent") or content.startswith("会话ID:"):
                                            continue

                                    # 最后一条消息完整显示
                                    is_last = (i == total - 1)

                                    # 格式化显示（使用原始序号 i+1）
                                    if role == "user":
                                        if is_last:
                                            # 最后一条用户消息完整显示
                                            console.print(f"[dim]{i+1}. [/dim][user]用户:[/user]")
                                            console.print(f"{content}\n")
                                        else:
                                            console.print(f"[dim]{i+1}. [/dim][user]用户:[/user] {content[:100]}{'...' if len(content) > 100 else ''}")
                                    elif role == "assistant":
                                        if is_last:
                                            # 最后一条助手消息完整显示
                                            console.print(f"[dim]{i+1}. [/dim][assistant]助手:[/assistant]")
                                            console.print(f"{content}\n")
                                        else:
                                            # 非最后一条只显示预览
                                            preview = content[:50].replace('\n', ' ')
                                            console.print(f"[dim]{i+1}. [/dim][assistant]助手:[/assistant] [dim]{preview}...[/dim]")
                                    elif role == "tool":
                                        console.print(f"[dim]{i+1}. [/dim][info]工具: {content[:80]}...[/info]")
                                    elif role == "system":
                                        # 其他 system 消息正常显示
                                        console.print(f"[dim]{i+1}. [/dim][dim]系统: {content}[/dim]")

                                console.print("[dim]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/dim]\n")
                                console.print(f"[info]已加载 {total} 条历史消息[/info]\n")
                        else:
                            console.print("\n[error]恢复失败[/error]\n")
                    except ValueError:
                        console.print(f"\n[error]无效的会话ID: {parts[1]}[/error]\n")
                    continue

                console.print("[error]未知命令。可用命令: /list, /new, /clear, /resume <id>[/error]\n")
                continue

            _run_single_task(config, task, executor, session_manager)

        except KeyboardInterrupt:
            console.print("\n\n[info]任务已中断[/info]")
            break

        except Exception as e:
            console.print(f"\n[error]错误：{rich_escape(str(e))}[/error]")
            if args.verbose:
                import traceback
                console.print(traceback.format_exc())


def _save_session_if_needed(session_manager: 'SessionManager', executor: 'Executor'):
    """保存会话（如果需要）

    Args:
        session_manager: 会话管理器
        executor: 执行器
    """
    if not session_manager or not executor:
        return

    # 如果 executor 为空（新会话），不保存（避免创建空会话文件）
    if not executor.current_agent:
        return

    # 首次创建会话
    if session_manager.current_session_id is None:
        new_id = session_manager.get_next_session_id()
        session_manager.current_session_id = new_id
        # 不再添加会话ID系统消息，因为没什么用

    # 保存会话
    session_manager.save_session(executor, session_manager.current_session_id)


def _run_single_task(config: Config, task: str, executor: 'Executor' = None, session_manager: 'SessionManager' = None):
    """执行单个任务

    Args:
        config: 配置对象
        task: 任务描述
        executor: 可选的现有 Executor（用于交互模式保留上下文）
        session_manager: 可选的会话管理器
    """
    console.print("-" * 60)

    # 打开日志
    log_file = None
    if os.environ.get("AGENT_LOG_FILE"):
        log_file = open(os.environ["AGENT_LOG_FILE"], "w", encoding="utf-8")

    # 创建或复用执行器
    if executor is None:
        executor = Executor(config)

    # 用于命令确认的状态
    command_batch_id = 0  # 当前处理的命令批次ID
    processed_count = 0  # 已处理的命令数量
    waiting_for_user_input = False  # 等待用户输入标志

    # 执行任务
    for outputs, result in executor.run(task):
        # 先显示非命令框的输出
        for output in outputs:
            console.print(output, end="", soft_wrap=True)

        # 逐个显示命令框并确认
        if result and result.command_blocks:
            # 新的命令批次
            if id(result.command_blocks) != command_batch_id:
                command_batch_id = id(result.command_blocks)
                processed_count = 0

            while processed_count < len(result.command_blocks):
                # 显示当前命令框
                console.print(result.command_blocks[processed_count], end="")

                # 获取对应的命令
                command = result.pending_commands[processed_count]

                # 等待用户确认
                _clear_input_buffer()
                confirm = console.input("[bold yellow]执行命令[y] / 跳过[n] / 修改建议: [/bold yellow]")
                confirm_lower = confirm.lower().strip()

                if confirm_lower == "y":
                    # 用户确认，执行命令
                    cmd_result = _execute_command(command, executor.config.timeout)

                    # 构建明确的结果消息
                    if cmd_result.returncode == 0:
                        if cmd_result.stdout:
                            result_msg = f"命令执行成功，输出：\n{cmd_result.stdout}"
                        else:
                            result_msg = "命令执行成功（无输出）"
                    else:
                        result_msg = f"命令执行失败（退出码: {cmd_result.returncode}）：\n{cmd_result.stderr}"

                    console.print(f"\n[info]{result_msg}[/info]\n")

                    if executor.current_agent:
                        executor.current_agent._add_message("tool", f'<ps_call_result id="executed">\n{result_msg}\n</ps_call_result>')

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

                processed_count += 1

        # 检查是否需要等待用户输入
        if any("[等待用户输入]" in output for output in outputs):
            waiting_for_user_input = True

            # 进入等待状态前保存会话
            _save_session_if_needed(session_manager, executor)

    # 如果等待用户输入，继续循环
    while waiting_for_user_input and executor.current_agent:
        console.print("\n" + "=" * 60)
        console.print("[bold yellow]Agent 等待您的回复[/bold yellow]")
        console.print("[dim]输入内容后按空行结束（q 退出，/list 查看会话）[/dim]")
        console.print("=" * 60 + "\n")

        lines = []
        while True:
            try:
                _clear_input_buffer()
                line = console.input("[info]> [/info]")

                if line.lower() in ["q", "quit", "exit"]:
                    console.print("[info]任务已终止[/info]")
                    waiting_for_user_input = False
                    break

                # 检查是否是会话命令（在收集到完整输入前就检查）
                if line.startswith("/"):
                    if line.lower() == "/list":
                        sessions = session_manager.list_sessions()
                        console.print("\n[bold cyan]保存的会话：[/bold cyan]\n")
                        if not sessions:
                            console.print("[dim]  （暂无保存的会话）[/dim]\n")
                        for s in sessions:
                            msg_preview = s.get('first_message', '')
                            if msg_preview:
                                console.print(f"  会话 {s['session_id']} | {s['updated_at'][:19]} | 消息数: {s['message_count']} | 深度: {s['depth']} | [dim]{msg_preview}[/dim]")
                            else:
                                console.print(f"  会话 {s['session_id']} | {s['updated_at'][:19]} | 消息数: {s['message_count']} | 深度: {s['depth']}")
                        console.print("")
                        continue  # 继续等待输入
                    else:
                        console.print("[warning]提示：在等待输入模式下，只支持 /list 命令[/warning]\n")
                        continue  # 继续等待输入

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

        if user_input.lower() in ["q", "quit", "exit"]:
            console.print("[info]输入已取消[/info]\n")
            waiting_for_user_input = False
            break

        # 继续执行
        waiting_for_user_input = False
        for outputs, result in executor.resume(user_input):
            # 先显示非命令框的输出
            for output in outputs:
                console.print(output, end="", soft_wrap=True)

            # 逐个显示命令框并确认
            if result and result.command_blocks:
                # 新的命令批次
                if id(result.command_blocks) != command_batch_id:
                    command_batch_id = id(result.command_blocks)
                    processed_count = 0

                while processed_count < len(result.command_blocks):
                    # 显示当前命令框
                    console.print(result.command_blocks[processed_count], end="")

                    # 获取对应的命令
                    command = result.pending_commands[processed_count]
                    _clear_input_buffer()
                    confirm = console.input("[bold yellow]执行命令[y] / 跳过[n] / 修改建议: [/bold yellow]")
                    confirm_lower = confirm.lower().strip()

                    if confirm_lower == "y":
                        cmd_result = _execute_command(command, executor.config.timeout)
                        if cmd_result.returncode == 0:
                            if cmd_result.stdout:
                                result_msg = f"命令执行成功，输出：\n{cmd_result.stdout}"
                            else:
                                result_msg = "命令执行成功（无输出）"
                        else:
                            result_msg = f"命令执行失败（退出码: {cmd_result.returncode}）：\n{cmd_result.stderr}"
                        console.print(f"\n[info]{result_msg}[/info]\n")
                        if executor.current_agent:
                            executor.current_agent._add_message("tool", f'<ps_call_result id="executed">\n{result_msg}\n</ps_call_result>')

                    elif confirm_lower == "n":
                        console.print("[info]命令已跳过[/info]\n")
                        if executor.current_agent:
                            executor.current_agent._add_message("user", f'<ps_call_result id="skip">\n命令已跳过\n</ps_call_result>')
                    else:
                        console.print("[info]已将您的建议发送给 Agent[/info]\n")
                        if executor.current_agent:
                            executor.current_agent._add_message("user", f'<ps_call_result id="rejected">\n用户建议：{confirm}\n</ps_call_result>')

                    processed_count += 1

            if any("[等待用户输入]" in output for output in outputs):
                waiting_for_user_input = True

    # 自动保存当前会话
    # 检查是否有待切换的 executor（如果有，说明在等待输入时执行了 /clear）
    if session_manager:
        pending = session_manager.get_pending_executor()
        if pending:
            # 使用新的 executor（空的）保存，这样不会覆盖旧会话
            _save_session_if_needed(session_manager, pending)
        else:
            # 使用当前的 executor 保存
            _save_session_if_needed(session_manager, executor)

    console.print("-" * 60)


def _execute_command(command: str, timeout: int):
    """执行命令"""
    import subprocess
    import base64

    # 设置 PowerShell 输出编码为 UTF-8，避免中文乱码
    # Windows 中文系统默认输出是 GBK (CP936)，需要显式设置
    prefixed_command = f'[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $PSDefaultParameterValues["Out-File:Encoding"] = "utf8"; {command}'

    # 使用 UTF-16 LE 编码并 Base64 编码命令，避免引号转义问题
    encoded_command = base64.b64encode(prefixed_command.encode('utf-16-le')).decode('ascii')
    full_cmd = f'powershell -EncodedCommand {encoded_command}'

    process = subprocess.run(
        full_cmd, shell=True, capture_output=True,
        timeout=timeout
    )

    # 手动解码，处理编码错误
    stdout = process.stdout.decode('utf-8', errors='replace')
    stderr = process.stderr.decode('utf-8', errors='replace')

    # 创建一个类似 CompletedProcess 的对象
    class Result:
        def __init__(self, stdout, stderr, returncode):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    return Result(stdout, stderr, process.returncode)


if __name__ == "__main__":
    main()
