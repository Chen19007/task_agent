"""极简命令行接口模块"""

import argparse
import json
import os
import re
import sys
import time
import atexit
import uuid
import shlex
import threading
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

from typing import Optional

from rich.console import Console
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme

from .agent import Executor, CommandSpec
from .command_approval_flow import CommandApprovalFlow
from .command_runtime import (
    format_shell_result as runtime_format_shell_result,
    normalize_command_spec,
)
from .config import Config
from .llm import create_client, ChatMessage
from .output_handler import NullOutputHandler
from .session import SessionManager
from .platform_utils import is_windows
from .hint_utils import select_hint_file
from .builtin_schema import (
    BuiltinParseError,
    normalize_builtin_args_with_schema,
    parse_builtin_args_by_schema,
    parse_builtin_simple_kv_args,
    resolve_path_against_workspace,
)
from .webhook.calendar_service import create_feishu_calendar_event

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

try:
    import msvcrt
except ImportError:  # 非 Windows 环境
    msvcrt = None
try:
    import readline  # noqa: F401
except Exception:
    readline = None


def _clear_input_buffer():
    """清空 stdin 中的残留输入（Windows）"""
    if not msvcrt:
        return
    while msvcrt.kbhit():
        msvcrt.getch()


def _start_esc_skip_listener(executor: Executor, console: Console):
    """仅在执行阶段监听 Esc，触发跳过下一次解析。"""
    if not msvcrt:
        return None

    stop_event = threading.Event()

    def _run():
        while not stop_event.is_set():
            try:
                if executor.is_waiting_for_input():
                    time.sleep(0.05)
                    continue
                if not msvcrt.kbhit():
                    time.sleep(0.05)
                    continue
                key = msvcrt.getch()
                if key == b"\x1b":
                    executor.arm_skip_next_parse("cli_esc")
                    console.print("[info]stop已生效：将跳过下一次解析[/info]")
            except Exception:
                time.sleep(0.05)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return (stop_event, thread)


def _stop_esc_skip_listener(listener) -> None:
    if not listener:
        return
    stop_event, thread = listener
    stop_event.set()
    thread.join(timeout=0.2)


def _resolve_file_references(text: str) -> tuple[str, list[str]]:
    """解析输入中的 @ 文件引用，返回替换后的文本和错误列表

    Args:
        text: 包含 @ 引用的输入文本

    Returns:
        (替换后的文本, 错误列表)
    """
    errors = []
    # 匹配 @filename 格式（@ 后跟非空白字符）
    pattern = r'@(\S+)'

    def replace_match(match):
        file_path = match.group(1)
        if not os.path.exists(file_path):
            return match.group(0)  # 非文件路径，保持原样
        # 检查是否为目录
        if os.path.isdir(file_path):
            errors.append(f"[warning]路径是目录而非文件: @{file_path}[/warning]")
            return match.group(0)  # 保留原 @path
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # 只返回文件内容，不包含文件名
            return content
        except FileNotFoundError:
            errors.append(f"[warning]文件不存在: @{file_path}[/warning]")
            return match.group(0)  # 保留原 @path
        except PermissionError:
            errors.append(f"[warning]无权限访问: @{file_path}[/warning]")
            return match.group(0)  # 保留原 @path
        except Exception as e:
            errors.append(f"[error]读取文件 @{file_path} 失败: {e}[/error]")
            return match.group(0)  # 保留原 @path

    result = re.sub(pattern, replace_match, text)
    return result, errors


console = Console(theme=custom_theme)

_RUN_STATS = {
    "smart_edit_calls": 0,
    "smart_edit_success": 0,
    "smart_edit_failure": 0,
    "smart_edit_rejected": 0,
}

_BACKGROUND_JOBS: dict[str, dict] = {}
_ACTIVE_HINT: Optional[str] = None
_ACTIVE_HINT_MODULES: list[Path] = []
_AGENT_START_DIR = os.getcwd()


def _ps_escape(text: str) -> str:
    return text.replace("'", "''")


def _get_effective_workspace_dir(workspace_dir: Optional[str]) -> str:
    """获取有效工作目录，不对 builtin 做 cwd 隐式兜底。"""
    return (workspace_dir or "").strip()


def _get_editor_command() -> list[str]:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        try:
            return shlex.split(editor)
        except ValueError:
            return [editor]
    if is_windows():
        try:
            result = subprocess.run(
                ["where", "notepad++"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            if result.stdout.strip():
                return ["notepad++"]
        except Exception:
            pass
        return ["notepad"]
    return ["vim"]


def _open_external_editor(initial_text: str) -> Optional[str]:
    editor_cmd = _get_editor_command()
    tmp_file = None
    try:
        tmp_file = tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8", suffix=".txt")
        tmp_file.write(initial_text or "")
        tmp_file.flush()
        tmp_file.close()
        if is_windows() and editor_cmd and "notepad++" in editor_cmd[0].lower():
            cmd = [
                "cmd",
                "/c",
                "start",
                "/wait",
                "",
                editor_cmd[0],
                *editor_cmd[1:],
                "-multiInst",
                "-nosession",
                "-noPlugin",
                tmp_file.name,
            ]
            subprocess.call(cmd)
        else:
            subprocess.call(editor_cmd + [tmp_file.name])
        with open(tmp_file.name, "r", encoding="utf-8") as handle:
            return handle.read()
    except Exception as exc:
        console.print(f"[error]打开外部编辑器失败: {exc}[/error]")
        return None
    finally:
        if tmp_file and tmp_file.name:
            try:
                os.unlink(tmp_file.name)
            except OSError:
                pass


def _find_project_root(start_dir: str) -> Path:
    current = Path(start_dir).resolve()
    for candidate in [current] + list(current.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return current


def _get_ps_jobs_dir() -> Path:
    root = _find_project_root(os.getcwd())
    jobs_dir = root / ".ps_jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return jobs_dir


def _register_background_job(job_id: str, process, log_path: Path) -> None:
    _BACKGROUND_JOBS[job_id] = {
        "process": process,
        "log_path": log_path,
    }


def _cleanup_background_jobs() -> None:
    for job_id, info in list(_BACKGROUND_JOBS.items()):
        process = info.get("process")
        log_path = info.get("log_path")
        try:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except Exception:
                    process.kill()
        except Exception:
            pass
        try:
            if log_path and log_path.exists():
                log_path.unlink()
        except Exception:
            pass
        _BACKGROUND_JOBS.pop(job_id, None)


atexit.register(_cleanup_background_jobs)

def _load_template_text(filename: str) -> str:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    template_path = os.path.join(project_root, "templates", filename)
    try:
        with open(template_path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def _update_smart_edit_stats(command: str, status: str, result_msg: str = "") -> None:
    if not command.strip().lower().startswith("builtin.smart_edit"):
        return
    _RUN_STATS["smart_edit_calls"] += 1
    if status == "rejected":
        _RUN_STATS["smart_edit_rejected"] += 1
        return
    if "成功" in result_msg and "失败" not in result_msg and "错误" not in result_msg:
        _RUN_STATS["smart_edit_success"] += 1
    else:
        _RUN_STATS["smart_edit_failure"] += 1


def _print_run_stats(console: Console) -> None:
    success = _RUN_STATS["smart_edit_success"]
    failure = _RUN_STATS["smart_edit_failure"]
    rejected = _RUN_STATS["smart_edit_rejected"]
    total = success + failure + rejected
    success_rate = (success / total * 100) if total else 0.0
    failure_rate = (failure / total * 100) if total else 0.0
    rejected_rate = (rejected / total * 100) if total else 0.0
    console.print(
        "[info]本次运行 smart_edit 统计："
        f"calls={_RUN_STATS['smart_edit_calls']} "
        f"success={success}({success_rate:.1f}%) "
        f"failure={failure}({failure_rate:.1f}%) "
        f"rejected={rejected}({rejected_rate:.1f}%)"
        "[/info]\n"
    )


def _format_shell_result(status: str, message: str) -> str:
    return runtime_format_shell_result(status, message)


_CLI_APPROVAL_FLOW: Optional[CommandApprovalFlow] = None


def _get_cli_approval_flow() -> CommandApprovalFlow:
    global _CLI_APPROVAL_FLOW
    if _CLI_APPROVAL_FLOW is None:
        _CLI_APPROVAL_FLOW = CommandApprovalFlow(_execute_command)
    return _CLI_APPROVAL_FLOW


def _contains_shell_result_tag(content: str) -> bool:
    return bool(re.search(r"<(ps_call_result|bash_call_result)\b", content, re.IGNORECASE))


def _extract_direct_shell_call(text: str) -> Optional[str]:
    stripped = text.lstrip()
    if not stripped.startswith(":"):
        return None
    command = stripped[1:].strip()
    return command or None


def _execute_direct_shell_call(command: str, console: Console, timeout: int) -> None:
    cmd_result = _execute_command(command, timeout)
    if cmd_result.returncode == 0:
        if cmd_result.stdout:
            result_msg = f"命令执行成功，输出：\n{cmd_result.stdout}"
        else:
            result_msg = "命令执行成功（无输出）"
    else:
        result_msg = f"命令执行失败（退出码: {cmd_result.returncode}）：\n{cmd_result.stderr}"
    _update_smart_edit_stats(command, "executed", result_msg)
    console.print(f"[info]{result_msg}[/info]\n")


def _handle_compact_command(executor: Executor, console: Console, reason: str = "手动压缩") -> None:
    if not executor.current_agent:
        console.print("[warning]暂无可压缩的会话[/warning]\n")
        return

    result = executor.current_agent.compact_history(reason=reason)
    if result.get("compacted"):
        console.print(
            "[success]已压缩历史上下文："
            f"{result.get('messages_before')} -> {result.get('messages_after')} | "
            f"{result.get('context_before')} -> {result.get('context_after')}[/success]\n"
        )
        return

    reason_map = {
        "too_short": "历史过短，无需压缩",
        "no_target": "没有可压缩的消息",
        "empty": "无有效内容可压缩",
        "compacting": "正在压缩中，请稍后再试",
    }
    reason_text = result.get("reason", "未知原因")
    if isinstance(reason_text, str) and reason_text.startswith("error:"):
        reason_text = f"压缩失败：{reason_text[6:].strip()}"
    else:
        reason_text = reason_map.get(reason_text, reason_text)
    console.print(f"[warning]{reason_text}[/warning]\n")


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

    parser.add_argument("--api-key", "-k", default="sk-1qTPR2NfODm9Y8YwQTXtGVONXF0g2bxWWreaZaMvPK4ErKOV",
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
                "极简任务执行 Agent\n\n统一逻辑：创建子Agent → 聚合结果 → 继续/结束\n深度优先执行，自动聚合结果\n最大4层深度（最多16个子Agent）\n输入 /exit 退出",
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
    - : <命令>     - 直接执行命令（不发给模型，不写入历史）
    - /new        - 创建新会话（自动保存当前会话）
    - /list       - 列出所有保存的会话
    - /list-snapshot <id> - 列出指定会话的快照点
    - /resume <id>- 恢复指定ID的会话
    - /rollback <id> <snapshot> - 回滚到指定会话快照点
    - /compact    - 压缩当前会话上下文
    - /edit       - 打开外部编辑器输入（$VISUAL/$EDITOR）
    - /auto       - 切换自动同意
    - /exit       - 退出程序

  [bold]等待输入模式[/bold] - Agent 询问问题，等待回复
    提示符: [info]>[/info]
    - <内容>      - 输入回复内容（空行结束）
    - : <命令>    - 直接执行命令（不发给模型，不写入历史）
    - /exit       - 终止当前任务
    - /list       - 查看会话列表
    - /compact    - 压缩当前会话上下文
    - /edit       - 打开外部编辑器输入（$VISUAL/$EDITOR）

[bold yellow]命令行参数：[/bold yellow]
  -m, --model   - 指定模型名称（默认：minimax-m2）
  -t, --timeout - 超时时间秒（默认：300）
  -a, --api-type- API类型：ollama 或 openai（默认：openai）
  -H, --host    - Ollama地址（默认：http://localhost:11434）
  -b, --base-url- OpenAI Base URL（默认：http://localhost:3000/v1）
  -k, --api-key - OpenAI API Key
  -M, --max-tokens- 最大输出token（默认：Ollama=4096, OpenAI=8192，适合大模型）
  --num-ctx        - 上下文窗口大小（Ollama num_ctx，默认 4096）
  -v, --verbose - 显示详细日志

[bold yellow]使用示例：[/bold yellow]
  task-agent "列出当前目录的文件"
  task-agent "安装项目依赖" --timeout 120
  task-agent --model gpt-4o "帮我重启服务"

[bold yellow]Agent 特性：[/bold yellow]
  - 统一逻辑，无父子区别
  - 深度优先执行，自动聚合结果
  - 最大支持4层深度（最多16个子Agent）
  - Windows 使用 <ps_call> 执行命令，Linux 使用 <bash_call>
  - 使用 <builtin> 调用内置工具（read_file/smart_edit/create_schedule）
  - 使用 <create_agent> 创建子Agent
  - 使用 <return> 标记任务完成

[bold yellow]会话管理：[/bold yellow]
  - 主循环输入任务默认使用当前会话
  - 要创建新会话使用 /new 命令
  - 会话自动保存在 sessions/ 目录""",
                justify="left",
                style="white",
            ),
            title="Task-Agent Help",
            subtitle="输入 /exit 退出",
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
        direct_command = _extract_direct_shell_call(args.task)
        if direct_command:
            _execute_direct_shell_call(direct_command, console, args.timeout)
            return
        _run_single_task(config, args.task)
        return

    # 交互模式
    session_manager = SessionManager()
    executor = Executor(
        config,
        session_manager=session_manager,
        runtime_scene="cli",
        workspace_dir=os.getcwd(),
    )  # 保持 Executor 实例，保留上下文
    paste_mode = False
    paste_lines: list[str] = []
    paste_prompt_shown = False

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
            auto_status = " | [success]自动同意: 启[/success]" if executor.auto_approve else ""
            hint_status = f" | 当前 hint: {_ACTIVE_HINT}" if _ACTIVE_HINT else ""
            if not paste_mode:
                if session_manager.current_session_id:
                    console.print(f"[dim]当前会话 #{session_manager.current_session_id} | 输入任务继续，/edit 外部编辑器，/new 新建，/list 列表，/auto 切换自动同意，/exit 退出{auto_status}{hint_status}[/dim]")
                else:
                    console.print(f"[dim]临时会话 | 输入任务创建会话，/edit 外部编辑器，/list 列表，/auto 切换自动同意，/exit 退出{auto_status}{hint_status}[/dim]")
            elif not paste_prompt_shown:
                paste_prompt_shown = True

            _clear_input_buffer()
            if paste_mode:
                task = input("")
            else:
                task = input("任务> ")

            if paste_mode:
                if task.lower() == "/send":
                    if not paste_lines:
                        console.print("[warning]粘贴内容为空，已忽略[/warning]")
                        continue
                    paste_mode = False
                    task = "\n".join(paste_lines)
                    paste_lines = []
                    paste_prompt_shown = False
                elif task.lower() == "/cancel":
                    paste_mode = False
                    paste_lines = []
                    paste_prompt_shown = False
                    console.print("[info]已取消粘贴[/info]")
                    continue
                else:
                    paste_lines.append(task)
                    continue
            elif task.lower() == "/paste":
                paste_mode = True
                paste_lines = []
                paste_prompt_shown = False
                console.print("[info]粘贴模式：/send 发送，/cancel 取消[/info]")
                continue
            elif task.lower() == "/edit":
                editor_text = _open_external_editor("")
                if editor_text is None:
                    continue
                if not editor_text.strip():
                    console.print("[warning]编辑内容为空，已忽略[/warning]")
                    continue
                task = editor_text

            if not task.strip():
                continue

            if task.lower() == "/exit":
                _print_run_stats(console)
                _cleanup_background_jobs()
                console.print("[info]再见！[/info]")
                break

            if task.lower() in ["help", "h"]:
                print_help()
                continue

            # 处理会话管理命令
            if task.startswith("/"):
                if task.lower() == "/exit":
                    _print_run_stats(console)
                    _cleanup_background_jobs()
                    console.print("[info]再见！[/info]")
                    break
                if task.lower() == "/list":
                    sessions = session_manager.list_sessions()
                    console.print("\n[bold cyan]保存的会话：[/bold cyan]\n")
                    if not sessions:
                        console.print("[dim]  （暂无保存的会话）[/dim]\n")
                    for s in sessions:
                        msg_preview = s.get('first_message', '')
                        if msg_preview:
                            console.print(f"  会话 {s['session_id']} | {s['created_at'][:19]} | 消息数: {s['message_count']} | 深度: {s['depth']} | [dim]{msg_preview}[/dim]")
                        else:
                            console.print(f"  会话 {s['session_id']} | {s['created_at'][:19]} | 消息数: {s['message_count']} | 深度: {s['depth']}")
                    console.print("")
                    continue
                if task.lower().startswith("/list-snapshot"):
                    parts = task.split()
                    if len(parts) != 2:
                        console.print("\n[error]用法: /list-snapshot <session_id>[/error]\n")
                        continue
                    try:
                        session_id = int(parts[1])
                    except ValueError:
                        console.print(f"\n[error]无效的会话ID: {parts[1]}[/error]\n")
                        continue

                    snapshots = session_manager.list_session_snapshots(session_id)
                    console.print("\n[bold cyan]会话快照点：[/bold cyan]\n")
                    if not snapshots:
                        console.print("[dim]  （暂无快照）[/dim]\n")
                        continue
                    for s in snapshots:
                        preview = s.get("last_message", "")
                        if preview:
                            preview = preview.replace("\n", " ").strip()
                            preview = preview[:50] + ("..." if len(preview) > 50 else "")
                        console.print(f"  会话 {s['session_id']} | 快照 {s['snapshot_index']} | {s['created_at'][:19]} | [dim]{preview}[/dim]")
                    console.print("")
                    continue

                if task.lower() == "/new":
                    new_id, new_executor = session_manager.create_new_session(executor)
                    executor = new_executor
                    console.print(f"\n[success]新会话已创建: {new_id}[/success]\n")
                    continue

                parts = task.split()
                if len(parts) == 2 and parts[0].lower() == "/resume":
                    try:
                        session_id = int(parts[1])
                        new_executor = session_manager.load_session(session_id, config, runtime_scene="cli")
                        if new_executor:
                            executor = new_executor
                            console.print(f"\n[success]会话已恢复: {session_id}[/success]\n")

                            if executor.current_agent and executor.current_agent.history:
                                console.print("[dim]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/dim]")
                                console.print("[bold cyan]历史上下文：[/bold cyan]\n")

                                history = executor.current_agent.history
                                total = len(history)

                                for i, msg in enumerate(history):
                                    role = msg.role
                                    content = msg.content.strip()

                                    if role == "system":
                                        if content.startswith("你是一个任务执行agent") or content.startswith("会话ID:"):
                                            continue

                                    is_last = (i == total - 1)

                                    if role == "user":
                                        if is_last:
                                            console.print(f"[dim]{i+1}. [/dim][user]用户:[/user]")
                                            console.print(f"{content}\n")
                                        else:
                                            console.print(f"[dim]{i+1}. [/dim][user]用户:[/user] {content[:100]}{'...' if len(content) > 100 else ''}")
                                    elif role == "assistant":
                                        if is_last:
                                            console.print(f"[dim]{i+1}. [/dim][assistant]助手:[/assistant]")
                                            console.print(f"{content}\n")
                                        else:
                                            preview = content[:50].replace('\n', ' ')
                                            console.print(f"[dim]{i+1}. [/dim][assistant]助手:[/assistant] [dim]{preview}...[/dim]")
                                    elif role == "tool":
                                        console.print(f"[dim]{i+1}. [/dim][info]工具: {content[:80]}...[/info]")
                                    elif role == "system":
                                        console.print(f"[dim]{i+1}. [/dim][dim]系统: {content}[/dim]")

                                console.print("[dim]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/dim]\n")
                                console.print(f"[info]已加载 {total} 条历史消息[/info]\n")
                        else:
                            console.print("\n[error]恢复失败[/error]\n")
                    except ValueError:
                        console.print(f"\n[error]无效的会话ID: {parts[1]}[/error]\n")
                    continue

                if task.lower() == "/auto":
                    executor.auto_approve = not executor.auto_approve
                    status = "启用" if executor.auto_approve else "禁用"
                    console.print(f"\n[success]自动同意已{status}[/success]\n")
                    continue
                if task.lower() == "/compact":
                    _handle_compact_command(executor, console, reason="手动压缩")
                    continue

                if task.lower().startswith("/rollback"):
                    parts = task.split()
                    if len(parts) != 3:
                        console.print("\n[error]用法: /rollback <session_id> <snapshot_index>[/error]\n")
                        continue
                    try:
                        session_id = int(parts[1])
                        snapshot_index = int(parts[2])
                    except ValueError:
                        console.print("\n[error]参数必须是整数[/error]\n")
                        continue

                    def confirm(prompt: str) -> bool:
                        _clear_input_buffer()
                        answer = console.input(f"[bold yellow]{prompt} [y/N][/bold yellow]")
                        return answer.strip().lower() == "y"

                    ok = session_manager.rollback_to_snapshot(
                        session_id,
                        snapshot_index,
                        confirm_callback=confirm
                    )
                    if ok:
                        new_executor = session_manager.load_session(session_id, config, runtime_scene="cli")
                        if new_executor:
                            executor = new_executor
                            console.print("\n[success]回滚完成，已切换到该会话[/success]\n")
                        else:
                            console.print("\n[warning]回滚完成，但会话恢复失败[/warning]\n")
                    else:
                        console.print("\n[warning]回滚已取消或失败[/warning]\n")
                    continue

                console.print("[error]未知命令。可用命令: /list, /list-snapshot <id>, /new, /resume <id>, /rollback <id> <snapshot>, /compact, /auto, /exit[/error]\n")
                continue

            direct_command = _extract_direct_shell_call(task)
            if direct_command:
                _execute_direct_shell_call(direct_command, console, args.timeout)
                continue

            # 解析 @ 文件引用
            task, errors = _resolve_file_references(task)
            if errors:
                for error in errors:
                    console.print(error)

            _run_single_task(config, task, executor, session_manager)

            # 确保 executor 回到根 agent（修复崩溃残留问题）
            # 从 context_stack 中逐层取出父 agent，恢复为 current_agent
            while executor.context_stack:
                parent = executor.context_stack.pop()
                # 传递子 agent 的 global_count（不是父 agent 的），确保计数器同步
                child_global_count = executor.current_agent._global_subagent_count
                parent.on_child_completed("任务中断", child_global_count)
                executor.current_agent = parent

        except KeyboardInterrupt:
            console.print("\n\n[info]任务已中断[/info]")
            break

        except Exception as e:
            console.print(f"\n[error]错误：{rich_escape(str(e))}[/error]")
            if args.verbose:
                import traceback
                console.print(traceback.format_exc())


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

    # CLI 输出由本地打印控制，避免重复展示
    cli_output = NullOutputHandler()

    # 创建或复用执行器
    if executor is None:
        executor = Executor(
            config,
            session_manager=session_manager,
            output_handler=cli_output,
            runtime_scene="cli",
            workspace_dir=os.getcwd(),
        )
    else:
        # 更新现有 executor 的 output_handler
        executor._output_handler = cli_output

    # 设置命令确认回调
    executor.set_command_confirm_callback(
        _create_cli_command_confirm_callback(executor, console)
    )

    # 用于命令确认的状态
    command_batch_id = 0  # 当前处理的命令批次ID
    processed_count = 0  # 已处理的命令数量
    waiting_for_user_input = False  # 等待用户输入标志

    # 首次创建会话ID（确保快照能正常保存）
    if session_manager and session_manager.current_session_id is None:
        new_id = session_manager.get_next_session_id()
        session_manager.current_session_id = new_id

    # 执行任务
    esc_listener = _start_esc_skip_listener(executor, console)
    try:
        for outputs, result in executor.run(task):
            # 显示输出
            for output in outputs:
                console.print(output, end="", soft_wrap=True)

            if result:
                command_batch_id, processed_count = _handle_pending_commands(
                    executor,
                    console,
                    result,
                    command_batch_id,
                    processed_count
                )

            # 检查是否需要等待用户输入
            if any("[等待用户输入]" in output for output in outputs):
                waiting_for_user_input = True
                # 会话快照已保存完整状态，无需额外保存
    finally:
        _stop_esc_skip_listener(esc_listener)

    # 如果等待用户输入，继续循环
    while waiting_for_user_input and executor.current_agent:
        paste_mode = False
        paste_lines: list[str] = []
        paste_prompt_shown = False

        console.print("\n" + "=" * 60)
        console.print("[bold yellow]Agent 等待您的回复[/bold yellow]")
        console.print("[dim]输入内容继续（/exit 退出，/list 查看会话，/compact 压缩上下文，/edit 外部编辑器）[/dim]")
        if _ACTIVE_HINT:
            console.print(f"[dim]当前 hint: {_ACTIVE_HINT}[/dim]")
        console.print("=" * 60 + "\n")

        try:
            _clear_input_buffer()
            if paste_mode:
                if not paste_prompt_shown:
                    paste_prompt_shown = True
                line = input("")
            else:
                line = input("> ")
        except KeyboardInterrupt:
            console.print("\n[warning]输入已取消[/warning]")
            break

        if paste_mode:
            if line.lower() == "/send":
                if not paste_lines:
                    console.print("[warning]粘贴内容为空，已忽略[/warning]")
                    continue
                paste_mode = False
                user_input = "\n".join(paste_lines)
                paste_lines = []
                paste_prompt_shown = False
            elif line.lower() == "/cancel":
                paste_mode = False
                paste_lines = []
                paste_prompt_shown = False
                console.print("[info]已取消粘贴[/info]")
                continue
            else:
                paste_lines.append(line)
                continue
        else:
            if line.lower() == "/paste":
                paste_mode = True
                paste_lines = []
                paste_prompt_shown = False
                console.print("[info]粘贴模式：/send 发送，/cancel 取消[/info]")
                continue
            if line.lower() == "/edit":
                editor_text = _open_external_editor("")
                if editor_text is None:
                    continue
                if not editor_text.strip():
                    console.print("[warning]编辑内容为空，已忽略[/warning]")
                    continue
                user_input = editor_text
            elif line.lower() == "/exit":
                _print_run_stats(console)
                console.print("[info]任务已终止[/info]")
                waiting_for_user_input = False
                break
            elif line.startswith("/"):
                if line.lower() == "/list":
                    sessions = session_manager.list_sessions()
                    console.print("\n[bold cyan]保存的会话：[/bold cyan]\n")
                    if not sessions:
                        console.print("[dim]  （暂无保存的会话）[/dim]\n")
                    for s in sessions:
                        msg_preview = s.get('first_message', '')
                        if msg_preview:
                            console.print(f"  会话 {s['session_id']} | {s['created_at'][:19]} | 消息数: {s['message_count']} | 深度: {s['depth']} | [dim]{msg_preview}[/dim]")
                        else:
                            console.print(f"  会话 {s['session_id']} | {s['created_at'][:19]} | 消息数: {s['message_count']} | 深度: {s['depth']}")
                    console.print("")
                    continue
                if line.lower() == "/compact":
                    _handle_compact_command(executor, console, reason="手动压缩")
                    continue
                console.print("[warning]提示：在等待输入模式下，只支持 /list 和 /compact 命令[/warning]\n")
                continue
            else:
                user_input = line

        if user_input.strip():
            console.print("[success]输入已接收，正在处理...[/success]\n", end="")

        if not user_input.strip():
            console.print("[warning]空输入，已忽略[/warning]\n")
            continue

        if user_input.lower() == "/exit":
            console.print("[info]输入已取消[/info]\n")
            waiting_for_user_input = False
            break

        # 解析 @ 文件引用
        user_input, errors = _resolve_file_references(user_input)
        if errors:
            for error in errors:
                console.print(error)

        direct_command = _extract_direct_shell_call(user_input)
        if direct_command:
            _execute_direct_shell_call(direct_command, console, args.timeout)
            waiting_for_user_input = False
            continue

        # 继续执行
        waiting_for_user_input = False
        esc_listener = _start_esc_skip_listener(executor, console)
        try:
            for outputs, result in executor.resume(user_input):
                # 显示输出（命令框已通过回调处理）
                for output in outputs:
                    console.print(output, end="", soft_wrap=True)

                if result:
                    command_batch_id, processed_count = _handle_pending_commands(
                        executor,
                        console,
                        result,
                        command_batch_id,
                        processed_count
                    )

                # 检查是否需要等待用户输入
                if any("[等待用户输入]" in output for output in outputs):
                    waiting_for_user_input = True
        finally:
            _stop_esc_skip_listener(esc_listener)

    # 会话快照已自动保存所有状态，无需额外保存

    console.print("-" * 60)


def _create_cli_command_confirm_callback(executor: 'Executor', console: Console):
    """创建 CLI 的命令确认回调函数

    Args:
        executor: Executor 实例
        console: Rich Console 实例

    Returns:
        命令确认回调函数
    """

    def confirm_callback(command: str) -> str:
        """CLI 的命令确认逻辑（同步）

        Args:
            command: 待执行的命令

        Returns:
            命令结果消息，格式: '<shell_call_result id="executed">...</shell_call_result>'
        """
        flow = _get_cli_approval_flow()
        command_spec = normalize_command_spec(CommandSpec(command=command))
        current_dir = _get_effective_workspace_dir(executor.workspace_dir)
        auto_commands, manual_commands = flow.split_auto_executable(
            [command_spec], executor.auto_approve, current_dir
        )

        if auto_commands and not manual_commands:
            # 自动执行安全命令
            console.print("[dim](自动执行)[/dim]\n", end="")
            executed = flow.execute_commands(
                executor=executor,
                commands=auto_commands,
                workspace_dir=current_dir,
            )
            result_msg = executed[0].message if executed else "命令执行成功（无输出）"

            console.print(f"[dim]{result_msg}[/dim]\n")
            _update_smart_edit_stats(command, "executed", result_msg)
            return _format_shell_result("executed", result_msg)

        # 等待用户确认
        _clear_input_buffer()
        auto_status = " (自动: 启)" if executor.auto_approve else ""
        confirm = input(f"执行命令[y] / 取消[c] / 执行并开启自动[a]{auto_status} ")
        confirm_lower = confirm.lower().strip()

        if confirm_lower == "a":
            # 启用自动同意并执行当前命令
            executor.auto_approve = True
            console.print("[success]自动同意已启用[/success]\n")
            confirm_lower = "y"

        if confirm_lower == "y":
            # 用户确认，执行命令
            executed = flow.execute_commands(
                executor=executor,
                commands=[command_spec],
                workspace_dir=current_dir,
            )
            result_msg = executed[0].message if executed else "命令执行成功（无输出）"

            console.print(f"\n[info]{result_msg}[/info]\n")
            _update_smart_edit_stats(command, "executed", result_msg)
            return _format_shell_result("executed", result_msg)

        elif confirm_lower == "c":
            console.print("[info]命令已取消[/info]\n")
            _update_smart_edit_stats(command, "rejected")
            flow.reject_commands(executor, "用户取消了命令执行")
            return _format_shell_result("rejected", "用户取消了命令执行")

        else:
            # 用户输入修改建议
            console.print("[info]已将您的建议发送给 Agent[/info]\n")
            _update_smart_edit_stats(command, "rejected")
            flow.reject_commands(executor, f"用户建议：{confirm}")
            return _format_shell_result("rejected", f"用户建议：{confirm}")

    return confirm_callback


def _handle_pending_commands(executor: 'Executor', console: Console, result: 'StepResult',
                             command_batch_id: int, processed_count: int) -> tuple[int, int]:
    """处理待确认命令，返回更新后的批次ID和处理计数"""
    if not result.pending_commands:
        return command_batch_id, processed_count
    flow = _get_cli_approval_flow()

    # 新的命令批次
    if id(result.command_blocks) != command_batch_id:
        command_batch_id = id(result.command_blocks)
        processed_count = 0

    while processed_count < len(result.command_blocks):
        # 显示当前命令框
        console.print(result.command_blocks[processed_count], end="")

        # 获取对应的命令
        command_spec = normalize_command_spec(result.pending_commands[processed_count])
        command = command_spec.command

        current_dir = _get_effective_workspace_dir(executor.workspace_dir)
        auto_commands, manual_commands = flow.split_auto_executable(
            [command_spec], executor.auto_approve, current_dir
        )

        if auto_commands and not manual_commands:
            # 自动执行安全命令
            console.print("[dim](自动执行)[/dim]\n", end="")
            executed = flow.execute_commands(
                executor=executor,
                commands=auto_commands,
                workspace_dir=current_dir,
            )
            result_msg = executed[0].message if executed else "命令执行成功（无输出）"

            console.print(f"[dim]{result_msg}[/dim]\n")
            _update_smart_edit_stats(command, "executed", result_msg)
        else:
            # 等待用户确认
            _clear_input_buffer()
            auto_status = " (自动: 启)" if executor.auto_approve else ""
            confirm = input(f"执行命令[y] / 取消[c] / 执行并开启自动[a]{auto_status} ")
            confirm_lower = confirm.lower().strip()

            if confirm_lower == "a":
                # 启用自动同意并执行当前命令
                executor.auto_approve = True
                console.print("[success]自动同意已启用[/success]\n")
                confirm_lower = "y"

            if confirm_lower == "y":
                # 用户确认，执行命令
                executed = flow.execute_commands(
                    executor=executor,
                    commands=[command_spec],
                    workspace_dir=current_dir,
                )
                result_msg = executed[0].message if executed else "命令执行成功（无输出）"

                console.print(f"\n[info]{result_msg}[/info]\n")
                _update_smart_edit_stats(command, "executed", result_msg)

            elif confirm_lower == "c":
                console.print("[info]命令已取消[/info]\n")
                _update_smart_edit_stats(command, "rejected")
                flow.reject_commands(executor, "用户取消了命令执行")
            else:
                # 用户输入修改建议
                console.print("[info]已将您的建议发送给 Agent[/info]\n")
                _update_smart_edit_stats(command, "rejected")
                flow.reject_commands(executor, f"用户建议：{confirm}")

        processed_count += 1

    return command_batch_id, processed_count


class _ExecResult:
    def __init__(self, stdout: str, stderr: str, returncode: int):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


_BUILTIN_TOOL_PATTERN = re.compile(r"^\s*builtin\.(\w+)\s*(\{[\s\S]*\})?\s*$")


def _prefix_builtin_result(tool_name: str, result: _ExecResult) -> _ExecResult:
    """为内置工具输出添加统一前缀，方便识别来源。"""
    prefix = f"{tool_name}: "

    def add_prefix(text: str) -> str:
        if not text:
            return text
        if text.startswith(prefix):
            return text
        first_line, *rest = text.splitlines()
        first_line = prefix + first_line
        return "\n".join([first_line] + rest)

    result.stdout = add_prefix(result.stdout)
    result.stderr = add_prefix(result.stderr)
    return result


def _format_builtin_parse_error(tool_name: str, error: BuiltinParseError) -> str:
    if error.kind == "empty_command":
        return f"{tool_name} 命令为空"
    if error.kind == "invalid_format":
        return f"{tool_name} 命令格式错误"
    if error.kind == "invalid_line":
        return f"无法解析 {tool_name} 行: {error.detail}"
    if error.kind == "unknown":
        if tool_name == "create_schedule":
            return f"不支持的 create_schedule 参数: {error.detail}"
        return f"不支持的 {tool_name} 参数: {error.detail}"
    if error.kind == "required":
        return f"{tool_name} 需要参数: {error.detail}"
    if error.kind == "empty_value":
        return f"{error.detail} 不能为空"
    return f"{tool_name} 参数解析失败"


def _parse_builtin_kv_command(
    command: str,
    tool_name: str,
    *,
    allow_invalid_kv_lines: bool = False,
) -> tuple[dict, Optional[str]]:
    args, error = parse_builtin_args_by_schema(
        command,
        tool_name,
        allow_invalid_kv_lines=allow_invalid_kv_lines,
    )
    if error is None:
        return args, None
    return {}, _format_builtin_parse_error(tool_name, error)


def _execute_builtin_tool(
    command: str,
    config: Optional[Config] = None,
    context_messages: Optional[list] = None,
    workspace_dir: str = "",
) -> Optional[_ExecResult]:
    stripped = command.strip()
    if stripped.lower().startswith("builtin.smart_edit"):
        parsed_args, error = _parse_smart_edit_command(stripped)
        if error:
            return _prefix_builtin_result("smart_edit", _ExecResult("", error, 1))
        return _prefix_builtin_result("smart_edit", _execute_builtin_smart_edit(parsed_args, workspace_dir))
    if stripped.lower().startswith("builtin.read_file"):
        parsed_args, error = _parse_read_file_command(stripped)
        if error:
            return _prefix_builtin_result("read_file", _ExecResult("", error, 1))
        return _prefix_builtin_result("read_file", _execute_builtin_read_file(parsed_args, workspace_dir))
    if stripped.lower().startswith("builtin.get_job_log"):
        parsed_args, error = _parse_job_log_command(stripped)
        if error:
            return _prefix_builtin_result("get_job_log", _ExecResult("", error, 1))
        return _prefix_builtin_result("get_job_log", _execute_builtin_job_log(parsed_args))
    if stripped.lower().startswith("builtin.get_resource"):
        parsed_args, error = _parse_get_resource_command(stripped)
        if error:
            return _prefix_builtin_result("get_resource", _ExecResult("", error, 1))
        return _prefix_builtin_result("get_resource", _execute_builtin_get_resource(parsed_args))
    if stripped.lower().startswith("builtin.memory_query"):
        parsed_args, error = _parse_memory_query_command(stripped)
        if error:
            return _prefix_builtin_result("memory_query", _ExecResult("", error, 1))
        if not config:
            return _prefix_builtin_result("memory_query", _ExecResult("", "memory_query 缺少配置", 1))
        return _prefix_builtin_result(
            "memory_query",
            _execute_builtin_memory_query(parsed_args, config, context_messages or []),
        )
    if stripped.lower().startswith("builtin.create_schedule"):
        parsed_args, error = _parse_create_schedule_command(stripped)
        if error:
            return _prefix_builtin_result("create_schedule", _ExecResult("", error, 1))
        if not config:
            return _prefix_builtin_result("create_schedule", _ExecResult("", "create_schedule 缺少配置", 1))
        return _prefix_builtin_result(
            "create_schedule",
            _execute_builtin_create_schedule(parsed_args, config),
        )
    if stripped.lower().startswith("builtin.hint"):
        parsed_args, error = _parse_hint_command(stripped)
        if error:
            return _ExecResult("", error, 1)
        return _execute_builtin_hint(parsed_args)

    match = _BUILTIN_TOOL_PATTERN.match(command)
    if not match:
        return None

    tool_name = match.group(1).lower()
    return _prefix_builtin_result(tool_name, _ExecResult("", f"未知内置工具: {tool_name}", 1))


def _read_text_file(path: str, start_line: int, max_lines: int, encoding: str):
    if not os.path.exists(path):
        return None, None, None, None, None, f"文件不存在: {path}"

    if os.path.isdir(path):
        return None, None, None, None, None, f"路径是目录而非文件: {path}"

    if start_line < 1 or max_lines < 1:
        return None, None, None, None, None, "start_line 和 max_lines 必须大于等于 1"

    max_lines_cap = 2000
    capped = False
    if max_lines > max_lines_cap:
        max_lines = max_lines_cap
        capped = True

    lines = []
    has_more = False
    try:
        with open(path, "r", encoding=encoding, errors="replace") as handle:
            for index, line in enumerate(handle, start=1):
                if index < start_line:
                    continue
                if len(lines) >= max_lines:
                    has_more = True
                    break
                lines.append(line)
    except Exception as exc:
        return None, None, None, None, None, f"读取文件失败: {exc}"

    returned = len(lines)
    if returned:
        end_line = start_line + returned - 1
    else:
        end_line = start_line - 1
    return lines, has_more, capped, end_line, max_lines, None


def _resolve_builtin_file_path(raw_path: str, workspace_dir: str) -> tuple[Optional[str], Optional[str]]:
    """解析 builtin 文件路径，禁止 cwd 隐式兜底。"""
    resolved, error = resolve_path_against_workspace(raw_path, workspace_dir)
    if error:
        return None, error
    if resolved is None:
        return None, "路径解析失败"
    return str(resolved), None


def _execute_builtin_read_file(args: dict, workspace_dir: str = "") -> _ExecResult:
    path = args.get("path") or args.get("file") or args.get("filepath")
    if not path:
        return _ExecResult("", "read_file 需要参数: path", 1)
    abs_path, resolve_error = _resolve_builtin_file_path(str(path), workspace_dir)
    if resolve_error:
        return _ExecResult("", resolve_error, 1)
    assert abs_path is not None

    try:
        start_line = int(args.get("start_line", 1))
        max_lines = int(args.get("max_lines", 200))
    except (TypeError, ValueError):
        return _ExecResult("", "start_line 和 max_lines 必须是整数", 1)

    encoding = args.get("encoding") or "utf-8-sig"
    lines, has_more, capped, end_line, max_lines, error = _read_text_file(
        abs_path, start_line, max_lines, encoding
    )
    if error:
        return _ExecResult("", error, 1)

    returned = len(lines)
    header_lines = [
        "内置工具 builtin.read_file 执行成功。",
        f"路径: {abs_path}",
        f"返回行范围: {start_line}-{end_line}",
        f"返回行数: {returned}",
    ]

    if capped:
        header_lines.append(f"提示: max_lines 超过上限，已截断为 2000 行。")

    if has_more:
        next_start = start_line + returned
        header_lines.append(f"还有更多内容。如需继续读取，请使用 start_line={next_start}，max_lines={max_lines}。")
    else:
        header_lines.append("已到文件末尾。")

    content = "".join(lines)
    if not content:
        content = "(空内容)"

    result_text = "\n".join(header_lines) + "\n---\n" + content
    return _ExecResult(result_text, "", 0)


def _execute_builtin_job_log(args: dict) -> _ExecResult:
    job_id = args.get("job_id")
    if not job_id:
        return _ExecResult("", "get_job_log 需要参数: job_id", 1)

    try:
        start_line = int(args.get("start_line", 1))
        max_lines = int(args.get("max_lines", 200))
    except (TypeError, ValueError):
        return _ExecResult("", "start_line 和 max_lines 必须是整数", 1)

    log_path = _get_ps_jobs_dir() / f"job_{job_id}.log"
    encoding = args.get("encoding") or "utf-8-sig"
    lines, has_more, capped, end_line, max_lines, error = _read_text_file(
        str(log_path), start_line, max_lines, encoding
    )
    if error:
        return _ExecResult("", error, 1)

    returned = len(lines)
    header_lines = [
        "内置工具 builtin.get_job_log 执行成功。",
        f"job_id: {job_id}",
        f"日志路径: {log_path}",
        f"返回行范围: {start_line}-{end_line}",
        f"返回行数: {returned}",
    ]

    if capped:
        header_lines.append(f"提示: max_lines 超过上限，已截断为 2000 行。")

    if has_more:
        next_start = start_line + returned
        header_lines.append(f"还有更多内容。如需继续读取，请使用 start_line={next_start}，max_lines={max_lines}。")
    else:
        header_lines.append("已到文件末尾。")

    content = "".join(lines)
    if not content:
        content = "(空内容)"

    result_text = "\n".join(header_lines) + "\n---\n" + content
    return _ExecResult(result_text, "", 0)


def _parse_hint_command(command: str) -> tuple[dict, Optional[str]]:
    args, error = _parse_builtin_kv_command(command, "hint")
    if error:
        return {}, error
    action = str(args.get("action", "")).strip().lower()
    if action == "load" and not str(args.get("name", "")).strip():
        return {}, "hint load 需要参数: name"
    return args, None


def _get_hints_root() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return project_root / "hints"


def _collect_hint_modules(name: str) -> list[Path]:
    hints_root = _get_hints_root()
    modules_dir = hints_root / name / "modules"
    if not modules_dir.exists() or not modules_dir.is_dir():
        return []
    if is_windows():
        modules = list(modules_dir.glob("*.psm1")) + list(modules_dir.glob("*.ps1"))
    else:
        modules = list(modules_dir.glob("*.sh"))
    return sorted(modules)


def _execute_builtin_hint(args: dict) -> _ExecResult:
    global _ACTIVE_HINT
    global _ACTIVE_HINT_MODULES
    action = args.get("action")
    if not action:
        return _ExecResult("", "hint 需要参数: action", 1)
    action = str(action).lower()
    if action == "load":
        name = args.get("name")
        if not name:
            return _ExecResult("", "hint load 需要参数: name", 1)
        hints_root = _get_hints_root()
        hint_dir = hints_root / str(name)
        prompt_path = select_hint_file(hint_dir, ".md")
        if not prompt_path or not prompt_path.exists():
            return _ExecResult("", "hint 提示词不存在", 1)
        try:
            content = prompt_path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            return _ExecResult("", "读取 hint 提示词失败", 1)
        if not content:
            return _ExecResult("", "hint 提示词为空", 1)
        _ACTIVE_HINT = str(name)
        _ACTIVE_HINT_MODULES = _collect_hint_modules(str(name))
        return _ExecResult(content, "", 0)
    if action == "unload":
        _ACTIVE_HINT = None
        _ACTIVE_HINT_MODULES = []
        return _ExecResult("hint 已卸载", "", 0)
    return _ExecResult("", f"未知 hint action: {action}", 1)


def _parse_smart_edit_command(command: str) -> tuple[dict, Optional[str]]:
    lines = command.splitlines()
    if not lines:
        return {}, "smart_edit 命令为空"

    if not lines[0].strip().lower().startswith("builtin.smart_edit"):
        return {}, "smart_edit 命令格式错误"

    args: dict[str, str] = {}
    index = 1
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        lower = line.lower()
        if lower.startswith("path:") or lower.startswith("file:") or lower.startswith("filepath:"):
            value = line.split(":", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            if not value:
                return {}, "path 不能为空"
            args["path"] = value
            index += 1
            continue
        if lower.startswith("mode:"):
            value = line.split(":", 1)[1].strip()
            if value:
                args["mode"] = value
            index += 1
            continue
        if lower.startswith("old_text:") or lower.startswith("new_text:"):
            key = "old_text" if lower.startswith("old_text:") else "new_text"
            index += 1
            if index >= len(lines) or lines[index].strip() != "<<<":
                return {}, f"{key} 必须使用 <<< 开始块"
            index += 1
            block_lines = []
            while index < len(lines) and lines[index].strip() != ">>>":
                block_lines.append(lines[index])
                index += 1
            if index >= len(lines):
                return {}, f"{key} 缺少 >>> 结束标记"
            args[key] = "\n".join(block_lines)
            index += 1
            continue

        return {}, f"无法解析 smart_edit 行: {line}"

    raw_args = parse_builtin_simple_kv_args(command)
    base_raw = {}
    for key in ("path", "file", "filepath", "mode"):
        if key in raw_args and str(raw_args.get(key, "")).strip():
            base_raw[key] = str(raw_args[key]).strip()
    base_args, schema_error = normalize_builtin_args_with_schema("smart_edit", base_raw)
    if schema_error:
        return {}, _format_builtin_parse_error("smart_edit", schema_error)
    args.update(base_args)
    return args, None


def _parse_read_file_command(command: str) -> tuple[dict, Optional[str]]:
    return _parse_builtin_kv_command(command, "read_file")


def _parse_get_resource_command(command: str) -> tuple[dict, Optional[str]]:
    return _parse_builtin_kv_command(command, "get_resource")


def _is_safe_relative_path(path: str) -> bool:
    if not path:
        return False
    if os.path.isabs(path):
        return False
    if re.match(r"^[a-zA-Z]:", path):
        return False
    parts = Path(path).parts
    return ".." not in parts


def _resolve_resource_path(rel_path: str, roots: list[Path]) -> tuple[Optional[Path], Optional[str]]:
    if not _is_safe_relative_path(rel_path):
        return None, "路径不合法，仅允许相对路径且禁止 .."

    for root in roots:
        try:
            root_resolved = root.resolve()
            candidate = (root / rel_path).resolve()
        except Exception:
            continue
        if not str(candidate).startswith(str(root_resolved)):
            continue
        if candidate.exists() and candidate.is_file():
            return candidate, None
    return None, "资源不存在"


def _get_active_resource_path() -> Optional[Path]:
    if not _ACTIVE_HINT:
        return None
    hint_root = _get_hints_root() / _ACTIVE_HINT / "resources"
    if hint_root.exists() and hint_root.is_dir():
        return hint_root
    return None


def _execute_builtin_get_resource(args: dict) -> _ExecResult:
    rel_path = args.get("path")
    if not rel_path:
        return _ExecResult("", "get_resource 需要参数: path", 1)

    active_root = _get_active_resource_path()
    if not active_root:
        return _ExecResult("", "未激活 hint 或资源目录不存在", 1)

    resolved, error = _resolve_resource_path(str(rel_path), [active_root])
    if error:
        return _ExecResult("", error, 1)
    if not resolved:
        return _ExecResult("", "资源不存在", 1)

    encoding = args.get("encoding") or "utf-8-sig"
    try:
        content = resolved.read_text(encoding=encoding)
    except Exception as exc:
        return _ExecResult("", f"读取资源失败: {exc}", 1)
    if content == "":
        content = "(空内容)"
    return _ExecResult(content, "", 0)

def _parse_job_log_command(command: str) -> tuple[dict, Optional[str]]:
    return _parse_builtin_kv_command(command, "get_job_log")


def _parse_memory_query_command(command: str) -> tuple[dict, Optional[str]]:
    return _parse_builtin_kv_command(command, "memory_query")


def _parse_create_schedule_command(command: str) -> tuple[dict, Optional[str]]:
    return _parse_builtin_kv_command(command, "create_schedule")


def _tz_for_name(tz_name: str):
    if tz_name == "Asia/Shanghai":
        return timezone(timedelta(hours=8))
    if tz_name in {"UTC", "Etc/UTC"}:
        return timezone.utc
    return None


def _parse_time_to_epoch(raw: str, tz_name: str) -> tuple[Optional[int], Optional[str]]:
    value = (raw or "").strip()
    if not value:
        return None, "时间不能为空"

    if re.fullmatch(r"\d{10}", value):
        return int(value), None

    if re.fullmatch(r"\d{13}", value):
        return int(value) // 1000, None

    normalized = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            tzinfo = _tz_for_name(tz_name)
            if tzinfo is None:
                return None, f"不支持的时区: {tz_name}（请改用秒级时间戳）"
            dt = dt.replace(tzinfo=tzinfo)
        return int(dt.timestamp()), None
    except ValueError:
        pass

    tzinfo = _tz_for_name(tz_name)
    if tzinfo is None:
        return None, f"不支持的时区: {tz_name}（请改用秒级时间戳）"

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(value, fmt).replace(tzinfo=tzinfo)
            return int(dt.timestamp()), None
        except ValueError:
            continue

    return None, f"无法解析时间: {value}"


def _load_snapshot_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return json.load(handle)
    except Exception:
        return None


def _iter_snapshot_sequences(snapshot_data: dict) -> list[list[dict]]:
    sequences: list[list[dict]] = []

    def extract_messages(agent_data: dict) -> list[dict]:
        history = agent_data.get("history", []) or []
        messages: list[dict] = []
        for msg in history:
            role = msg.get("role", "")
            if role not in ("user", "assistant"):
                continue
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            if _contains_shell_result_tag(content):
                continue
            messages.append({"role": role, "content": content})
        return messages

    current = snapshot_data.get("current_agent") or {}
    current_messages = extract_messages(current)
    if current_messages:
        sequences.append(current_messages)

    for agent in snapshot_data.get("context_stack", []) or []:
        agent_messages = extract_messages(agent or {})
        if agent_messages:
            sequences.append(agent_messages)

    return sequences


def _score_content(content: str, terms: list[str]) -> int:
    text = content.lower()
    score = 0
    for term in terms:
        score += text.count(term)
    return score


def _build_window_text(seq: list[dict], start: int, end: int, max_chars: int) -> str:
    lines = []
    total = 0
    for msg in seq[start:end]:
        role = msg.get("role", "")
        role_label = "用户" if role == "user" else "助手"
        line = f"{role_label}: {msg.get('content', '')}"
        if total + len(line) > max_chars:
            remaining = max_chars - total
            if remaining > 0:
                lines.append(line[:remaining] + "...")
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def _extract_context_tail(context_messages: list, count: int) -> str:
    kept = []
    for msg in reversed(context_messages or []):
        role = getattr(msg, "role", "")
        if role not in ("user", "assistant"):
            continue
        content = (getattr(msg, "content", "") or "").strip()
        if not content or _contains_shell_result_tag(content):
            continue
        role_label = "用户" if role == "user" else "助手"
        kept.append(f"{role_label}: {content}")
        if len(kept) >= count:
            break
    kept.reverse()
    return "\n".join(kept)


def _parse_json_array(text: str) -> Optional[list]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _llm_expand_query_terms(client, query: str) -> list[str]:
    template = _load_template_text("memory_terms_prompt.txt")
    if not template:
        template = "请将用户问题拆分成关键词数组（JSON）。\n用户问题：{query}"
    prompt = template.format(query=query)
    response = client.chat([ChatMessage(role="user", content=prompt)], 1024)
    parsed = _parse_json_array(response.content or "")
    if not isinstance(parsed, list):
        return []
    terms = []
    for item in parsed:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                terms.append(cleaned)
    return terms


def _llm_filter_windows(client, query: str, windows: list[dict], batch_size: int) -> dict[int, dict]:
    results: dict[int, dict] = {}
    template = _load_template_text("memory_filter_prompt.txt")
    if not template:
        template = "用户问题：{query}\n\n候选片段：\n{items}"
    for start in range(0, len(windows), batch_size):
        batch = windows[start:start + batch_size]
        items = []
        for item in batch:
            items.append(f"[{item['index']}]\n{item['text']}")
        prompt = template.format(
            query=query,
            items="\n\n".join(items),
        )
        response = client.chat([ChatMessage(role="user", content=prompt)], 2048)
        parsed = _parse_json_array(response.content or "")
        if not parsed:
            for item in batch:
                results[item["index"]] = {"relevant": True, "score": 50, "reason": "fallback"}
            continue
        for item in parsed:
            try:
                index = int(item.get("index"))
            except Exception:
                continue
            relevant = bool(item.get("relevant"))
            try:
                score = int(item.get("score", 0))
            except Exception:
                score = 0
            results[index] = {"relevant": relevant, "score": score, "reason": item.get("reason", "")}
    return results


def _llm_summarize_windows(client, query: str, context_text: str, windows: list[dict]) -> str:
    items = []
    for item in windows:
        items.append(f"[{item['index']}]\n{item['text']}")
    template = _load_template_text("memory_summary_prompt.txt")
    if not template:
        template = "用户问题：{query}\n\n当前上下文：\n{context}\n\n相关片段：\n{items}"
    prompt = template.format(
        query=query,
        context=context_text,
        items="\n\n".join(items),
    )
    response = client.chat([ChatMessage(role="user", content=prompt)], 2048)
    return (response.content or "").strip()


def _execute_builtin_memory_query(args: dict, config: Config, context_messages: list) -> _ExecResult:
    query = args.get("query", "").strip()
    if not query:
        return _ExecResult("", "memory_query 需要参数: query", 1)

    def to_int(value: str, default: int, min_value: int, max_value: int) -> int:
        try:
            number = int(value)
        except Exception:
            return default
        return max(min_value, min(max_value, number))

    limit = to_int(args.get("limit", "5"), 5, 1, 20)
    window = to_int(args.get("window", "10"), 10, 2, 30)
    candidate = to_int(args.get("candidate", "50"), 50, 5, 200)
    batch = to_int(args.get("batch", "6"), 6, 2, 10)
    topn = to_int(args.get("topn", "8"), 8, 1, 20)
    context_tail = to_int(args.get("context_tail", "8"), 8, 1, 20)

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sessions_dir = os.path.join(project_root, "sessions")
    if not os.path.isdir(sessions_dir):
        return _ExecResult("", "sessions 目录不存在", 1)

    try:
        client = create_client(config)
    except Exception as exc:
        return _ExecResult("", f"memory_query 失败: {exc}", 1)

    base_terms = [t.lower() for t in re.split(r"\s+", query) if t.strip()]
    llm_terms = []
    try:
        llm_terms = _llm_expand_query_terms(client, query)
    except Exception:
        llm_terms = []
    terms = []
    for term in base_terms + [t.lower() for t in llm_terms]:
        if term and term not in terms:
            terms.append(term)
    if not terms:
        return _ExecResult("", "query 不能为空", 1)

    candidates = {}
    max_chars_per_window = 1500
    files = sorted(
        f for f in os.listdir(sessions_dir)
        if f.endswith(".json") and os.path.isfile(os.path.join(sessions_dir, f))
    )

    source_index = 0
    for filename in files:
        snapshot = _load_snapshot_json(os.path.join(sessions_dir, filename))
        if not snapshot:
            continue
        sequences = _iter_snapshot_sequences(snapshot)
        for seq in sequences:
            seq_len = len(seq)
            if seq_len == 0:
                continue
            for i, msg in enumerate(seq):
                score = _score_content(msg.get("content", ""), terms)
                if score <= 0:
                    continue
                half = window // 2
                start = max(i - half, 0)
                end = min(i + half + 1, seq_len)
                key = (source_index, start, end)
                if key in candidates:
                    candidates[key] = max(candidates[key], score)
                else:
                    candidates[key] = score
            source_index += 1

    if not candidates:
        return _ExecResult("memory_query: 未命中", "", 0)

    candidate_items = []
    for (source_id, start, end), score in candidates.items():
        seq = None
        # source_id tracks incremental sequences in traversal order
        # we only need to rebuild the window text by re-reading snapshots
        candidate_items.append((source_id, start, end, score))

    candidate_items.sort(key=lambda x: x[3], reverse=True)
    candidate_items = candidate_items[:candidate]

    windows = []
    seq_lookup: list[list[dict]] = []
    source_index = 0
    for filename in files:
        snapshot = _load_snapshot_json(os.path.join(sessions_dir, filename))
        if not snapshot:
            continue
        sequences = _iter_snapshot_sequences(snapshot)
        for seq in sequences:
            seq_lookup.append(seq)
            source_index += 1

    for idx, (source_id, start, end, score) in enumerate(candidate_items, start=1):
        if source_id >= len(seq_lookup):
            continue
        seq = seq_lookup[source_id]
        text = _build_window_text(seq, start, end, max_chars_per_window)
        windows.append({"index": idx, "text": text, "score": score})

    try:
        filtered = _llm_filter_windows(client, query, windows, batch)
    except Exception as exc:
        return _ExecResult("", f"memory_query 失败: {exc}", 1)
    relevant = []
    for item in windows:
        meta = filtered.get(item["index"])
        if not meta:
            continue
        if not meta.get("relevant"):
            continue
        relevant.append({"index": item["index"], "text": item["text"], "score": meta.get("score", 0)})

    if not relevant:
        return _ExecResult("memory_query: 未命中", "", 0)

    relevant.sort(key=lambda x: x["score"], reverse=True)
    relevant = relevant[:topn]

    context_text = _extract_context_tail(context_messages, context_tail)
    try:
        summary = _llm_summarize_windows(client, query, context_text, relevant)
    except Exception as exc:
        return _ExecResult("", f"memory_query 失败: {exc}", 1)
    summary = summary or "（无摘要）"

    result_text = f"memory_query: 成功\n命中: {len(relevant)}\n---\n{summary}"
    return _ExecResult(result_text, "", 0)


def _execute_builtin_create_schedule(args: dict, config: Config) -> _ExecResult:
    summary = (args.get("summary") or "").strip()
    start_time_raw = (args.get("start_time") or "").strip()
    end_time_raw = (args.get("end_time") or "").strip()
    timezone_name = (args.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai"
    description = (args.get("description") or "").strip()
    calendar_id = (args.get("calendar_id") or config.webhook_calendar_id or "").strip()
    need_notification = str(args.get("need_notification", "false")).strip().lower() in {"1", "true", "yes", "y", "on"}
    user_id_type = (args.get("user_id_type") or "open_id").strip() or "open_id"
    attendee_need_notification = str(args.get("attendee_need_notification", "true")).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    attendee_open_ids_raw = str(args.get("attendee_open_ids") or "").strip()
    attendee_open_ids = []
    if attendee_open_ids_raw:
        attendee_open_ids = [x.strip() for x in attendee_open_ids_raw.split(",") if x.strip()]
    default_attendee_open_id = (config.webhook_default_attendee_open_id or "").strip()
    if not attendee_open_ids and default_attendee_open_id:
        attendee_open_ids = [default_attendee_open_id]

    if not summary:
        return _ExecResult("", "create_schedule 需要参数: summary", 1)
    if not start_time_raw:
        return _ExecResult("", "create_schedule 需要参数: start_time", 1)
    if not calendar_id:
        return _ExecResult("", "缺少默认日历配置：WEBHOOK_CALENDAR_ID", 1)

    start_ts, start_err = _parse_time_to_epoch(start_time_raw, timezone_name)
    if start_err:
        return _ExecResult("", f"start_time 无效: {start_err}", 1)

    if end_time_raw:
        end_ts, end_err = _parse_time_to_epoch(end_time_raw, timezone_name)
        if end_err:
            return _ExecResult("", f"end_time 无效: {end_err}", 1)
    else:
        end_ts = start_ts + 30 * 60

    if end_ts <= start_ts:
        return _ExecResult("", "end_time 必须晚于 start_time", 1)

    result = create_feishu_calendar_event(
        app_id=config.webhook_app_id,
        app_secret=config.webhook_app_secret,
        calendar_id=calendar_id,
        summary=summary,
        start_timestamp=start_ts,
        end_timestamp=end_ts,
        timezone=timezone_name,
        description=description,
        need_notification=need_notification,
        user_id_type=user_id_type,
        attendee_open_ids=attendee_open_ids,
        attendee_need_notification=attendee_need_notification,
    )
    if not result.ok:
        return _ExecResult("", result.message, 1)

    lines = [
        "创建日程成功",
        f"calendar_id: {calendar_id}",
        f"event_id: {result.event_id or '(未返回)'}",
        f"summary: {summary}",
        f"start_time: {start_ts}",
        f"end_time: {end_ts}",
        f"timezone: {timezone_name}",
    ]
    if attendee_open_ids:
        lines.append(f"attendee_count: {len(attendee_open_ids)}")
    if result.warning:
        lines.append(f"warning: {result.warning}")
    return _ExecResult("\n".join(lines), "", 0)


def _execute_builtin_smart_edit(args: dict, workspace_dir: str = "") -> _ExecResult:
    path = args.get("path") or args.get("file") or args.get("filepath")
    if not path:
        return _ExecResult("", "smart_edit 需要参数: path", 1)
    abs_path, resolve_error = _resolve_builtin_file_path(str(path), workspace_dir)
    if resolve_error:
        return _ExecResult("", resolve_error, 1)
    assert abs_path is not None

    mode = (args.get("mode") or "Patch").strip().lower()
    mode_map = {
        "patch": "Patch",
        "create": "Create",
        "append": "Append",
        "prepend": "Prepend",
    }
    if mode not in mode_map:
        return _ExecResult("", "mode 必须是 Patch/Create/Append/Prepend 之一", 1)
    mode = mode_map[mode]

    old_text = args.get("old_text") or ""
    new_text = args.get("new_text")
    if new_text is None:
        return _ExecResult("", "smart_edit 需要参数: new_text", 1)

    if mode == "Create":
        if os.path.exists(abs_path):
            return _ExecResult("", f"文件已存在: {path}", 1)
        try:
            with open(abs_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(new_text)
            return _ExecResult("文件创建成功。", "", 0)
        except Exception as exc:
            return _ExecResult("", f"创建失败: {exc}", 1)

    if not os.path.exists(abs_path):
        return _ExecResult("", "文件不存在", 1)

    has_bom = False
    try:
        with open(abs_path, "rb") as handle:
            head = handle.read(3)
        if head == b"\xef\xbb\xbf":
            has_bom = True
    except Exception:
        pass

    try:
        with open(abs_path, "r", encoding="utf-8-sig", errors="replace") as handle:
            content = handle.read()
    except Exception as exc:
        return _ExecResult("", f"读取失败: {exc}", 1)

    target_line_ending = "\r\n" if "\r\n" in content else "\n"
    norm_content = content.replace("\r\n", "\n")
    norm_new = new_text.replace("\r\n", "\n")
    final_norm_content = norm_content

    if mode == "Append":
        if final_norm_content and not final_norm_content.endswith("\n"):
            final_norm_content = final_norm_content + "\n" + norm_new
        else:
            final_norm_content = final_norm_content + norm_new
    elif mode == "Prepend":
        final_norm_content = norm_new + "\n" + norm_content
    elif mode == "Patch":
        if not old_text.strip():
            return _ExecResult("", "Patch 模式需要 old_text。", 1)
        norm_old = old_text.replace("\r\n", "\n")
        match_count = len(re.findall(re.escape(norm_old), norm_content))
        if match_count == 0:
            return _ExecResult("", "匹配失败：未找到 old_text。", 1)
        if match_count > 1:
            return _ExecResult("", f"安全错误：匹配到 {match_count} 处。", 1)
        final_norm_content = norm_content.replace(norm_old, norm_new)
        if final_norm_content == norm_content:
            return _ExecResult("", "严重错误：替换未生效。", 1)

    output_content = final_norm_content
    if target_line_ending == "\r\n":
        output_content = final_norm_content.replace("\r\n", "\n").replace("\n", "\r\n")

    try:
        encoding = "utf-8-sig" if has_bom else "utf-8"
        with open(abs_path, "w", encoding=encoding, newline="") as handle:
            handle.write(output_content)
    except Exception as exc:
        return _ExecResult("", f"写入失败: {exc}", 1)

    return _ExecResult(f"成功 ({mode})", "", 0)


def _execute_command(
    command: str,
    timeout: int,
    config: Optional[Config] = None,
    context_messages: Optional[list] = None,
    background: bool = False,
    workspace_dir: str = "",
):
    """执行命令（CLI 和 GUI 共用）

    Args:
        command: 待执行的命令
        timeout: 超时时间（秒）
        background: 是否后台执行

    Returns:
        Result 对象，包含 stdout, stderr, returncode
    """
    import subprocess
    import base64
    import shlex

    builtin_result = _execute_builtin_tool(command, config, context_messages, workspace_dir=workspace_dir)
    if builtin_result is not None:
        return builtin_result

    def build_import_prefix() -> str:
        if not _ACTIVE_HINT_MODULES:
            return ""
        project_root = Path(__file__).resolve().parents[2]
        start_dir = _ps_escape(_AGENT_START_DIR)
        project_dir = _ps_escape(str(project_root))
        env_prefix = (
            f"$env:AGENT_START_DIR='{start_dir}'; "
            f"$env:AGENT_PROJECT_DIR='{project_dir}'; "
        )
        parts = []
        for module_path in _ACTIVE_HINT_MODULES:
            resolved_path = module_path.resolve()
            path_text = _ps_escape(str(resolved_path))
            module_dir = _ps_escape(str(resolved_path.parent))
            parts.append(f"$env:HINT_MODULE_DIR='{module_dir}'; Import-Module '{path_text}'")
        if not parts:
            return ""
        return env_prefix + "; ".join(parts) + "; "

    if is_windows():
        import_prefix = build_import_prefix()

        # 设置 PowerShell 输出编码为 UTF-8，避免中文乱码
        # Windows 中文系统默认输出是 GBK (CP936)，需要显式设置
        if background:
            prefixed_command = (
                '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; '
                '$PSDefaultParameterValues["Out-File:Encoding"] = "utf8"; '
                '$ProgressPreference = "SilentlyContinue"; '
                '$InformationPreference = "SilentlyContinue"; '
                '$VerbosePreference = "SilentlyContinue"; '
                '$WarningPreference = "SilentlyContinue"; '
                f'{import_prefix}{command}'
            )
        else:
            prefixed_command = (
                '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; '
                '$PSDefaultParameterValues["Out-File:Encoding"] = "utf8"; '
                f'{import_prefix}{command}'
            )

        # 使用 UTF-16 LE 编码并 Base64 编码命令，避免引号转义问题
        encoded_command = base64.b64encode(prefixed_command.encode('utf-16-le')).decode('ascii')
        full_cmd = f'powershell -EncodedCommand {encoded_command}'

        try:
            if background:
                job_id = uuid.uuid4().hex[:8]
                jobs_dir = _get_ps_jobs_dir()
                log_path = jobs_dir / f"job_{job_id}.log"
                log_handle = open(log_path, "ab")
                process = subprocess.Popen(
                    full_cmd, shell=True,
                    stdout=log_handle,
                    stderr=log_handle
                )
                log_handle.close()
                _register_background_job(job_id, process, log_path)
                rel_log = None
                try:
                    rel_log = log_path.relative_to(_find_project_root(os.getcwd()))
                except ValueError:
                    rel_log = log_path
                result_msg = f"后台执行已启动\njob_id: {job_id}\nlog_path: {rel_log}"
                return _ExecResult(stdout=result_msg, stderr="", returncode=0)

            process = subprocess.run(
                full_cmd, shell=True, capture_output=True,
                timeout=timeout
            )
        except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
            # 捕获命令执行异常（如命令行太长、文件未找到等）
            # 创建一个错误结果对象
            return _ExecResult(
                stdout="",
                stderr=str(e),
                returncode=1
            )

        # 手动解码，处理编码错误
        stdout = process.stdout.decode('utf-8', errors='replace')
        stderr = process.stderr.decode('utf-8', errors='replace')
        return _ExecResult(stdout, stderr, process.returncode)

    def build_bash_prefix() -> str:
        if not _ACTIVE_HINT_MODULES:
            return ""
        project_root = Path(__file__).resolve().parents[2]
        start_dir = shlex.quote(_AGENT_START_DIR)
        project_dir = shlex.quote(str(project_root))
        parts = [
            f"export AGENT_START_DIR={start_dir};",
            f"export AGENT_PROJECT_DIR={project_dir};",
        ]
        for module_path in _ACTIVE_HINT_MODULES:
            resolved_path = module_path.resolve()
            module_dir = resolved_path.parent
            parts.append(f"export HINT_MODULE_DIR={shlex.quote(str(module_dir))};")
            parts.append(f"source {shlex.quote(str(resolved_path))};")
        return " ".join(parts) + " "

    import_prefix = build_bash_prefix()
    prefixed_command = f"{import_prefix}{command}"

    try:
        if background:
            job_id = uuid.uuid4().hex[:8]
            jobs_dir = _get_ps_jobs_dir()
            log_path = jobs_dir / f"job_{job_id}.log"
            log_handle = open(log_path, "ab")
            process = subprocess.Popen(
                ["bash", "-lc", prefixed_command],
                stdout=log_handle,
                stderr=log_handle
            )
            log_handle.close()
            _register_background_job(job_id, process, log_path)
            rel_log = None
            try:
                rel_log = log_path.relative_to(_find_project_root(os.getcwd()))
            except ValueError:
                rel_log = log_path
            result_msg = f"后台执行已启动\njob_id: {job_id}\nlog_path: {rel_log}"
            return _ExecResult(stdout=result_msg, stderr="", returncode=0)

        process = subprocess.run(
            ["bash", "-lc", prefixed_command],
            capture_output=True,
            timeout=timeout
        )
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
        return _ExecResult(
            stdout="",
            stderr=str(e),
            returncode=1
        )

    stdout = process.stdout.decode('utf-8', errors='replace')
    stderr = process.stderr.decode('utf-8', errors='replace')
    return _ExecResult(stdout, stderr, process.returncode)


if __name__ == "__main__":
    main()
