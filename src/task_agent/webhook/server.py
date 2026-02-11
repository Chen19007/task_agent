"""
飞书长连接模式服务

使用飞书 SDK 的长连接模式接收事件，无需公网服务器
"""

import logging
import os
import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional, Set, Dict, Any

from ..agent import Action
from ..command_runtime import (
    ExecutionContext,
    can_auto_execute_command,
    execute_command_spec,
    format_shell_result,
    normalize_command_spec,
)
from ..config import Config
from .adapter import WebhookAdapter
from .platforms import FeishuPlatform

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 全局配置与平台
_config: Optional[Config] = None
_platform: Optional[FeishuPlatform] = None
_adapters: Dict[str, WebhookAdapter] = {}
_adapters_lock = threading.Lock()

# 已处理的消息ID去重
_processed_uuids: Set[str] = set()
_processed_message_ids: Set[str] = set()
_processed_lock = threading.Lock()

# 线程池用于异步执行任务
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="feishu_task")
_REALTIME_WINDOW_SECONDS = 60
_pending_authorizations: Dict[str, Dict[str, Any]] = {}
_pending_latest_card_by_chat: Dict[str, str] = {}
_pending_auth_lock = threading.Lock()
_pending_workspace_cards: Dict[str, Dict[str, Any]] = {}
_pending_workspace_latest_by_chat: Dict[str, str] = {}
_pending_workspace_lock = threading.Lock()
_session_workspaces: Dict[str, str] = {}
_session_workspace_lock = threading.Lock()


def _format_event_create_time(create_time_ms: str) -> str:
    """把飞书 create_time(毫秒时间戳)转为本地可读时间。"""
    if not create_time_ms:
        return ""
    try:
        ts = float(create_time_ms) / 1000.0
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _extract_reject_reason(action_value: Dict[str, Any], event: Any) -> str:
    """从卡片回调中提取拒绝原因（优先表单字段）。"""
    # 1) action.value 里直传
    reason = str(action_value.get("reject_reason", "")).strip()
    if reason:
        return reason

    # 2) action.form_value（常见于 form submit）
    action_obj = getattr(event, "action", None) if event else None
    form_value = getattr(action_obj, "form_value", None) if action_obj else None
    if isinstance(form_value, dict):
        reason = str(form_value.get("reject_reason", "")).strip()
        if reason:
            return reason

        # 兼容嵌套结构，如 {"reject_form": {"reject_reason": "..."}}
        for v in form_value.values():
            if isinstance(v, dict):
                reason = str(v.get("reject_reason", "")).strip()
                if reason:
                    return reason

    return ""


def _clean_incoming_text(text: str) -> str:
    """清洗飞书入站文本，移除 @ 标签与不可见空白。"""
    import re

    if not text:
        return ""

    cleaned = re.sub(r"<at\b[^>]*>.*?</at>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"^(?:@\S+\s*)+", " ", cleaned)
    cleaned = cleaned.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _is_clear_command(text: str) -> bool:
    """判断是否为 clear 命令（仅支持 /clear）。"""
    import re

    if not text:
        return False
    text_norm = text.strip().lower()
    return re.fullmatch(r"/clear", text_norm) is not None


def _is_change_workspace_command(text: str) -> bool:
    """判断是否为切换工作目录命令。"""
    import re

    if not text:
        return False
    text_norm = text.strip().lower()
    return re.fullmatch(r"/(?:change_workspace|cw|ws)", text_norm) is not None


def _query_zlocation_options(limit: int = 10) -> list[dict]:
    """
    获取 ZLocation 候选目录并转换为卡片 select_static options。
    返回格式: [{"text": {"tag": "plain_text", "content": "..."}, "value": "..."}]
    """
    command = (
        "Import-Module ZLocation -ErrorAction SilentlyContinue; "
        f"$items = z -l | Select-Object -First {max(1, limit)} Weight,Path; "
        "$items | ConvertTo-Json -Compress"
    )
    options: list[dict] = []
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if proc.returncode != 0:
            logger.warning(
                f"[change_workspace] Get-ZLocation 执行失败: code={proc.returncode}, stderr={proc.stderr[:200]}"
            )
        else:
            stdout = (proc.stdout or "").strip()
            if stdout:
                data = json.loads(stdout)
                items = data if isinstance(data, list) else [data]
                seen = set()
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    path = str(item.get("Path", "")).strip()
                    score = item.get("Weight", "")
                    if not path or path in seen or not os.path.isdir(path):
                        continue
                    seen.add(path)
                    label = f"[{score}] {path}" if score != "" else path
                    if len(label) > 120:
                        label = f"{label[:117]}..."
                    options.append(
                        {
                            "text": label,
                            "value": path,
                        }
                    )
    except Exception as e:
        logger.warning(f"[change_workspace] 读取 ZLocation 候选失败: {e}")

    # 兜底：至少提供当前目录
    if not options:
        cwd = os.getcwd()
        options.append(
            {
                "text": f"[current] {cwd}",
                "value": cwd,
            }
        )
    return options


def _extract_workspace_selection(action_value: Dict[str, Any], event: Any) -> str:
    """从卡片 submit_selection 回调中提取 dynamic_list 选项值。"""
    value = action_value.get("dynamic_list")
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list) and value:
        v0 = value[0]
        if isinstance(v0, str):
            return v0.strip()
        if isinstance(v0, dict):
            candidate = str(v0.get("value", "")).strip()
            if candidate:
                return candidate

    action_obj = getattr(event, "action", None) if event else None
    form_value = getattr(action_obj, "form_value", None) if action_obj else None
    if isinstance(form_value, dict):
        # 常见结构：{"dynamic_list": "..."} 或 {"selection_form": {"dynamic_list": "..."}}
        raw = form_value.get("dynamic_list")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if isinstance(raw, dict):
            candidate = str(raw.get("value", "")).strip()
            if candidate:
                return candidate
        if isinstance(raw, list) and raw:
            first = raw[0]
            if isinstance(first, str):
                return first.strip()
            if isinstance(first, dict):
                candidate = str(first.get("value", "")).strip()
                if candidate:
                    return candidate

        for nested in form_value.values():
            if isinstance(nested, dict):
                raw_nested = nested.get("dynamic_list")
                if isinstance(raw_nested, str) and raw_nested.strip():
                    return raw_nested.strip()
                if isinstance(raw_nested, dict):
                    candidate = str(raw_nested.get("value", "")).strip()
                    if candidate:
                        return candidate
                if isinstance(raw_nested, list) and raw_nested:
                    first = raw_nested[0]
                    if isinstance(first, str):
                        return first.strip()
                    if isinstance(first, dict):
                        candidate = str(first.get("value", "")).strip()
                        if candidate:
                            return candidate
    return ""


def _build_session_key(chat_type: str, chat_id: str) -> str:
    return f"{chat_type}:{chat_id}"


def _get_session_workspace(chat_type: str, chat_id: str) -> str:
    """获取会话级工作目录，未设置时回退到进程 cwd。"""
    session_key = _build_session_key(chat_type, chat_id)
    with _session_workspace_lock:
        return _session_workspaces.get(session_key, os.getcwd())


def _set_session_workspace(chat_type: str, chat_id: str, workspace_dir: str) -> None:
    """设置会话级工作目录。"""
    session_key = _build_session_key(chat_type, chat_id)
    with _session_workspace_lock:
        _session_workspaces[session_key] = workspace_dir


def _get_or_create_adapter(chat_type: str, chat_id: str) -> WebhookAdapter:
    """按会话键获取独立适配器，避免私聊/群聊共享同一会话。"""
    global _config, _platform, _adapters
    session_key = _build_session_key(chat_type, chat_id)
    with _adapters_lock:
        adapter = _adapters.get(session_key)
        if adapter is None:
            if _config is None or _platform is None:
                raise RuntimeError("Webhook 服务未初始化完成")
            adapter = WebhookAdapter(config=_config, platform=_platform, chat_id=chat_id)
            workspace_dir = _get_session_workspace(chat_type, chat_id)
            adapter.executor.workspace_dir = workspace_dir
            _adapters[session_key] = adapter
            logger.info(f"[会话] 创建新会话适配器: session_key={session_key}")
        else:
            adapter.executor.workspace_dir = _get_session_workspace(chat_type, chat_id)
        return adapter


def _clear_session_context(chat_type: str, chat_id: str) -> bool:
    """清理指定会话上下文（adapter + 待授权状态）。"""
    session_key = _build_session_key(chat_type, chat_id)
    removed = False

    with _adapters_lock:
        if session_key in _adapters:
            del _adapters[session_key]
            removed = True

    with _pending_auth_lock:
        latest_card_id = _pending_latest_card_by_chat.pop(chat_id, None)
        if latest_card_id and latest_card_id in _pending_authorizations:
            del _pending_authorizations[latest_card_id]
            removed = True

        stale_keys = [
            card_id
            for card_id, ctx in _pending_authorizations.items()
            if ctx.get("chat_id") == chat_id and ctx.get("chat_type") == chat_type
        ]
        for card_id in stale_keys:
            del _pending_authorizations[card_id]
            removed = True

    with _pending_workspace_lock:
        latest_ws_card_id = _pending_workspace_latest_by_chat.pop(chat_id, None)
        if latest_ws_card_id and latest_ws_card_id in _pending_workspace_cards:
            del _pending_workspace_cards[latest_ws_card_id]
            removed = True

        stale_ws_keys = [
            card_id
            for card_id, ctx in _pending_workspace_cards.items()
            if ctx.get("chat_id") == chat_id and ctx.get("chat_type") == chat_type
        ]
        for card_id in stale_ws_keys:
            del _pending_workspace_cards[card_id]
            removed = True

    return removed


def _process_workspace_selection_async(card_message_id: str, selected_path: str) -> None:
    """异步处理切换目录卡片选择。"""
    with _pending_workspace_lock:
        ws_ctx = _pending_workspace_cards.pop(card_message_id, None)
        if ws_ctx:
            _pending_workspace_latest_by_chat.pop(ws_ctx["chat_id"], None)

    if not ws_ctx:
        logger.warning(f"[change_workspace] 未找到待处理卡片上下文: card_message_id={card_message_id}")
        return

    platform: FeishuPlatform = ws_ctx["platform"]
    chat_id: str = ws_ctx["chat_id"]
    chat_type: str = ws_ctx["chat_type"]
    source_message_id: str = ws_ctx["source_message_id"]
    allowed_paths = set(ws_ctx.get("allowed_paths", []))

    selected_path = (selected_path or "").strip()
    logger.info(
        f"[change_workspace] 提交选择: card_message_id={card_message_id}, "
        f"selected_path={selected_path!r}, allowed_count={len(allowed_paths)}"
    )

    if not selected_path:
        platform.update_workspace_selection_card_result(card_message_id, "❌ 切换失败：未选择目录。")
        platform.send_message("❌ 未选择目录，请重新发送 /cw", chat_id, chat_type, source_message_id)
        return

    if allowed_paths and selected_path not in allowed_paths:
        platform.update_workspace_selection_card_result(card_message_id, f"❌ 切换失败：目录不在候选列表。\n`{selected_path}`")
        platform.send_message("❌ 目录不在候选列表，请重新发送 /cw", chat_id, chat_type, source_message_id)
        return

    if not os.path.isdir(selected_path):
        platform.update_workspace_selection_card_result(card_message_id, f"❌ 切换失败：目录不存在。\n`{selected_path}`")
        platform.send_message(f"❌ 目录不存在：{selected_path}", chat_id, chat_type, source_message_id)
        return

    try:
        _set_session_workspace(chat_type, chat_id, selected_path)
        logger.info(f"[change_workspace] 已切换会话工作目录: session={_build_session_key(chat_type, chat_id)}, cwd={selected_path}")
        # 切换目录后重建当前会话，避免沿用旧目录注入和旧对话上下文
        _clear_session_context(chat_type, chat_id)
        platform.update_workspace_selection_card_result(card_message_id, f"✅ 已切换工作目录\n`{selected_path}`")
        platform.send_message(f"✅ 已切换工作目录：{selected_path}", chat_id, chat_type, source_message_id)
    except Exception as e:
        logger.error(f"[change_workspace] 切换目录失败: path={selected_path}, error={e}")
        platform.update_workspace_selection_card_result(card_message_id, f"❌ 切换失败：{e}")
        platform.send_message(f"❌ 切换目录失败：{e}", chat_id, chat_type, source_message_id)


def _continue_executor_after_auth(adapter: WebhookAdapter, platform: FeishuPlatform,
                                  chat_id: str, chat_type: str, source_message_id: str,
                                  session_key: str = "") -> None:
    """在命令确认后继续执行 Executor 流程。"""
    total_outputs = 0
    for output_list, step_result in adapter.executor._execute_loop():
        if adapter.output_handler:
            contents = adapter.output_handler.flush()
            if contents:
                total_outputs += len(contents)
                platform.send_message("\n".join(contents), chat_id, chat_type, source_message_id)
            elif output_list and step_result.action != Action.COMPLETE:
                fallback = [item for item in output_list if isinstance(item, str) and item.strip()]
                if fallback:
                    total_outputs += len(fallback)
                    platform.send_message("\n".join(fallback), chat_id, chat_type, source_message_id)

        if step_result.pending_commands:
            if _try_auto_execute_pending_commands(adapter, step_result.pending_commands):
                logger.info("继续流程命中自动授权，已自动执行待授权命令，跳过卡片")
                continue
            command_content = "\n".join(
                [
                    cmd.display() if hasattr(cmd, "display") else str(getattr(cmd, "command", ""))
                    for cmd in step_result.pending_commands
                ]
            ).strip()
            logger.info(
                f"继续流程检测到待授权命令: {len(step_result.pending_commands)} 条，发送授权卡片，命令={command_content[:120]}"
            )
            if isinstance(platform, FeishuPlatform):
                card_message_id = platform.send_authorization_card(
                    chat_id=chat_id,
                    chat_type=chat_type,
                    message_id=source_message_id,
                    command_content=command_content,
                )
                if card_message_id:
                    with _pending_auth_lock:
                        _pending_authorizations[card_message_id] = {
                            "adapter": adapter,
                            "platform": platform,
                            "chat_id": chat_id,
                            "chat_type": chat_type,
                            "source_message_id": source_message_id,
                            "pending_commands": step_result.pending_commands,
                        }
                        _pending_latest_card_by_chat[chat_id] = card_message_id
                    logger.info(f"已缓存待授权上下文: card_message_id={card_message_id}")
            break

        if step_result.action == Action.WAIT:
            logger.info(
                f"授权后流程进入 WAIT 状态，已回到主循环等待输入，"
                f"session_key={session_key or _build_session_key(chat_type, chat_id)}"
            )
            break

    logger.info(f"授权后继续执行完成，本轮输出 {total_outputs} 条")


def _try_auto_execute_pending_commands(adapter: WebhookAdapter, pending_commands: list) -> bool:
    """当 auto_approve 开启且命令安全时，自动执行待授权命令。"""
    if not adapter.executor.auto_approve:
        return False

    try:
        from ..cli import _execute_command

        workspace_dir = getattr(adapter.executor, "workspace_dir", None) or os.getcwd()

        # 先全量判断安全性，任一不安全则回退到卡片授权
        normalized_commands = [normalize_command_spec(item) for item in pending_commands]
        for command_spec in normalized_commands:
            if not can_auto_execute_command(command_spec, True, workspace_dir):
                logger.info(f"[自动授权] 检测到非自动执行命令，回退卡片授权: {command_spec.command}")
                return False

        context = ExecutionContext(
            config=adapter.executor.config,
            workspace_dir=workspace_dir,
            context_messages=(adapter.executor.current_agent.history if adapter.executor.current_agent else None),
        )
        for command_spec in normalized_commands:
            exec_result = execute_command_spec(
                command_spec=command_spec,
                context=context,
                execute_command=_execute_command,
            )

            result_msg = exec_result.human_message()

            if adapter.executor.current_agent:
                adapter.executor.current_agent._add_message(
                    "user", format_shell_result("executed", result_msg)
                )

        logger.info(f"[自动授权] 已自动执行 {len(normalized_commands)} 条命令")
        return True
    except Exception as e:
        logger.error(f"[自动授权] 自动执行失败，回退卡片授权: {e}")
        return False


def _process_card_action_async(card_message_id: str, action: str, auto: bool,
                               action_value: Dict[str, Any]) -> None:
    """异步处理卡片交互，避免阻塞 ACK。"""
    with _pending_auth_lock:
        auth_ctx = _pending_authorizations.pop(card_message_id, None)
        if auth_ctx:
            _pending_latest_card_by_chat.pop(auth_ctx["chat_id"], None)

    if not auth_ctx:
        logger.warning(f"[卡片交互] 未找到待授权上下文: card_message_id={card_message_id}")
        return

    adapter: WebhookAdapter = auth_ctx["adapter"]
    platform: FeishuPlatform = auth_ctx["platform"]
    chat_id: str = auth_ctx["chat_id"]
    chat_type: str = auth_ctx["chat_type"]
    source_message_id: str = auth_ctx["source_message_id"]
    pending_commands = auth_ctx["pending_commands"]

    logger.info(
        f"[卡片交互] 开始处理授权: action={action}, auto={auto}, "
        f"card_message_id={card_message_id}, commands={len(pending_commands)}"
    )

    cmd_preview = " | ".join(
        [
            cmd.display() if hasattr(cmd, "display") else str(getattr(cmd, "command", ""))
            for cmd in pending_commands
        ]
    ).strip()
    reject_reason = str(action_value.get("reject_reason", "")).strip()

    if action == "approve":
        if auto:
            status_text = "✅ 已授权并开启自动授权"
        else:
            status_text = "✅ 已授权执行"
    else:
        status_text = f"⛔ 已拒绝授权\n原因: {reject_reason}" if reject_reason else "⛔ 已拒绝授权"

    platform.update_authorization_card_result(card_message_id, cmd_preview, status_text)

    if auto:
        adapter.executor.auto_approve = True
        logger.info("[卡片交互] 已开启 auto_approve")

    try:
        from ..cli import _execute_command

        if action == "approve":
            workspace_dir = getattr(adapter.executor, "workspace_dir", None) or _get_session_workspace(chat_type, chat_id)
            context = ExecutionContext(
                config=adapter.executor.config,
                workspace_dir=workspace_dir,
                context_messages=(adapter.executor.current_agent.history if adapter.executor.current_agent else None),
            )
            for command_spec in [normalize_command_spec(item) for item in pending_commands]:
                exec_result = execute_command_spec(
                    command_spec=command_spec,
                    context=context,
                    execute_command=_execute_command,
                )

                result_msg = exec_result.human_message()

                if adapter.executor.current_agent:
                    adapter.executor.current_agent._add_message(
                        "user", format_shell_result("executed", result_msg)
                    )
        else:
            reject_reason = reject_reason or str(action_value.get("reason") or "用户取消了命令执行")
            if adapter.executor.current_agent:
                adapter.executor.current_agent._add_message(
                    "user", format_shell_result("rejected", str(reject_reason))
                )

        adapter.executor._is_running = True
        _continue_executor_after_auth(
            adapter, platform, chat_id, chat_type, source_message_id,
            session_key=_build_session_key(chat_type, chat_id)
        )
    except Exception as e:
        logger.error(f"[卡片交互] 授权处理失败: {e}")
        import traceback
        traceback.print_exc()
        platform.send_message(f"❌ 授权处理失败: {str(e)}", chat_id, chat_type, source_message_id)


def handle_card_action_trigger(data):
    """
    处理飞书卡片交互回传事件（card.action.trigger）

    Args:
        data: lark.event.callback.model.p2_card_action_trigger.P2CardActionTrigger
    """
    try:
        import lark_oapi as lark
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )

        logger.info("=" * 50)
        logger.info("收到卡片交互事件！")
        logger.info(f"card.action.trigger payload: {lark.JSON.marshal(data)}")

        event = getattr(data, "event", None)
        action_obj = getattr(event, "action", None) if event else None
        action_value = getattr(action_obj, "value", None) if action_obj else None
        action_value = action_value if isinstance(action_value, dict) else {}
        raw_action = str(action_value.get("action", "")).strip().lower()
        reject_reason = _extract_reject_reason(action_value, event)
        if reject_reason:
            action_value["reject_reason"] = reject_reason

        # 卡片动作约定：approve / reject / auto_approve / submit_reject / submit_selection
        if raw_action == "auto_approve":
            action = "approve"
            auto = True
        elif raw_action == "submit_reject":
            action = "reject"
            auto = False
        elif raw_action == "submit_selection":
            action = "submit_selection"
            auto = False
        elif raw_action in {"approve", "reject"}:
            action = raw_action
            auto = False
        else:
            action = "reject"
            auto = False

        context = getattr(event, "context", None) if event else None
        open_message_id = getattr(context, "open_message_id", "") if context else ""
        open_chat_id = getattr(context, "open_chat_id", "") if context else ""

        if not open_message_id and open_chat_id:
            with _pending_auth_lock:
                open_message_id = _pending_latest_card_by_chat.get(open_chat_id, "")
            if not open_message_id:
                with _pending_workspace_lock:
                    open_message_id = _pending_workspace_latest_by_chat.get(open_chat_id, "")

        logger.info(
            f"[卡片交互] 解析结果: action={action}, auto={auto}, "
            f"open_message_id={open_message_id}, open_chat_id={open_chat_id}, "
            f"reject_reason={reject_reason!r}"
        )

        if action == "submit_selection":
            selected_path = _extract_workspace_selection(action_value, event)
            logger.info(f"[change_workspace] 卡片提交选项: selected_path={selected_path!r}")
            if open_message_id:
                _executor.submit(_process_workspace_selection_async, open_message_id, selected_path)
            else:
                logger.warning("[change_workspace] 缺少 open_message_id，无法处理目录切换")
        elif open_message_id:
            _executor.submit(_process_card_action_async, open_message_id, action, auto, action_value)
        else:
            logger.warning("[卡片交互] 缺少 open_message_id，无法匹配待授权上下文")

        # 立即返回，避免超时重试
        resp = {
            "toast": {
                "type": "info",
                "content": "已收到操作，处理中",
            }
        }
        return P2CardActionTriggerResponse(resp)
    except Exception as e:
        logger.error(f"处理卡片交互事件失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def handle_message(data):
    """
    处理接收到的消息

    Args:
        data: lark.im.v1.P2ImMessageReceiveV1
    """
    global _platform, _processed_uuids, _processed_message_ids, _processed_lock

    try:
        # 事件头信息（用于定位是否同一事件被重投）
        header = getattr(data, "header", None)
        event_id = getattr(header, "event_id", "") if header else ""
        event_type = getattr(header, "event_type", "") if header else ""
        create_time = getattr(header, "create_time", "") if header else ""
        create_time_readable = _format_event_create_time(create_time)
        tenant_key = getattr(header, "tenant_key", "") if header else ""
        app_id = getattr(header, "app_id", "") if header else ""
        schema = getattr(data, "schema", "")

        # 去重检查
        uuid_val = getattr(data, 'uuid', None)
        if uuid_val:
            with _processed_lock:
                if uuid_val in _processed_uuids:
                    logger.info(f"[丢弃事件] 原因=重复uuid uuid={uuid_val}")
                    return
                _processed_uuids.add(uuid_val)
                # 清理旧的uuid（防止内存泄漏）
                if len(_processed_uuids) > 1000:
                    old_count = len(_processed_uuids)
                    _processed_uuids.clear()
                    logger.info(f"[清理] 清理了 {old_count} 条历史记录")

        logger.info("=" * 50)
        logger.info("收到事件！")
        logger.info(
            f"事件头: schema={schema}, event_id={event_id}, event_type={event_type}, "
            f"create_time={create_time}, create_time_readable={create_time_readable}, "
            f"tenant_key={tenant_key}, app_id={app_id}, uuid={uuid_val}"
        )

        # 尝试获取事件内容
        if hasattr(data, 'event'):
            event = data.event

            if hasattr(event, 'message'):
                message = event.message

                if hasattr(message, 'chat_id'):
                    chat_id = message.chat_id
                    logger.info(f"chat_id: {chat_id}")

                # 获取消息类型（私聊/群聊）
                chat_type = getattr(message, 'chat_type', 'p2p')
                message_id = getattr(message, 'message_id', '')
                logger.info(f"chat_type: {chat_type}, message_id: {message_id}")

                # 解析结构化 @ 信息（用于区分 @ 的对象）
                mentions = getattr(message, "mentions", None)
                mention_items = []
                if mentions:
                    try:
                        for m in mentions:
                            mention_id = getattr(m, "id", None)
                            mention_items.append(
                                {
                                    "name": getattr(m, "name", ""),
                                    "key": getattr(m, "key", ""),
                                    "open_id": getattr(mention_id, "open_id", "") if mention_id else "",
                                    "user_id": getattr(mention_id, "user_id", "") if mention_id else "",
                                    "union_id": getattr(mention_id, "union_id", "") if mention_id else "",
                                }
                            )
                    except Exception as e:
                        logger.warning(f"mentions 解析失败: {e}")
                logger.info(f"mentions: count={len(mention_items)}, data={mention_items}")

                # message_id 去重（补充 uuid 去重，防止重复投递）
                if message_id:
                    with _processed_lock:
                        if message_id in _processed_message_ids:
                            logger.info(
                                f"[丢弃事件] 原因=重复message_id message_id={message_id} chat_id={chat_id}"
                            )
                            return
                        _processed_message_ids.add(message_id)
                        if len(_processed_message_ids) > 1000:
                            old_count = len(_processed_message_ids)
                            _processed_message_ids.clear()
                            logger.info(f"[清理] 清理了 {old_count} 条 message_id 记录")

                # 发送者信息（用于排查是否处理了机器人自己的消息）
                sender = getattr(event, 'sender', None)
                sender_type = getattr(sender, 'sender_type', '') if sender else ''
                sender_id = getattr(sender, 'sender_id', None) if sender else None
                sender_open_id = getattr(sender_id, 'open_id', '') if sender_id else ''
                sender_user_id = getattr(sender_id, 'user_id', '') if sender_id else ''
                sender_union_id = getattr(sender_id, 'union_id', '') if sender_id else ''
                logger.info(
                    f"sender_type: {sender_type}, open_id: {sender_open_id}, "
                    f"user_id: {sender_user_id}, union_id: {sender_union_id}"
                )

                if str(sender_type).lower() in {"app", "bot"}:
                    logger.info(
                        f"[丢弃事件] 原因=机器人自身消息 sender_type={sender_type} "
                        f"message_id={message_id} chat_id={chat_id}"
                    )
                    return

                # 获取消息内容
                content_raw = message.content if hasattr(message, 'content') else "{}"
                logger.info(f"入站消息原始 content: {str(content_raw)[:300]}")
                content = content_raw

                if isinstance(content, str):
                    import json
                    try:
                        content = json.loads(content)
                    except:
                        pass

                text_raw = content.get("text", "") if isinstance(content, dict) else str(content)
                text = _clean_incoming_text(text_raw)
                logger.info(f"入站消息解析文本: raw={text_raw!r}, cleaned={text!r}")

                # 内建命令：清理当前会话上下文
                if _is_clear_command(text):
                    cleared = _clear_session_context(chat_type, chat_id)
                    if _platform is not None:
                        if cleared:
                            _platform.send_message(
                                "✅ 已清理当前会话上下文（仅当前私聊/群聊）",
                                chat_id,
                                chat_type,
                                message_id,
                            )
                        else:
                            _platform.send_message(
                                "ℹ️ 当前会话没有可清理的上下文",
                                chat_id,
                                chat_type,
                                message_id,
                            )
                    logger.info(
                        f"[内建命令] /clear 执行完成: cleared={cleared}, "
                        f"session_key={_build_session_key(chat_type, chat_id)}"
                    )
                    return

                # 内建命令：切换当前进程工作目录（通过卡片选择）
                if _is_change_workspace_command(text):
                    if _platform is None:
                        logger.error("[change_workspace] 平台未初始化")
                        return
                    if not isinstance(_platform, FeishuPlatform):
                        _platform.send_message(
                            "❌ 当前平台不支持切换目录卡片。",
                            chat_id,
                            chat_type,
                            message_id,
                        )
                        return

                    dir_options = _query_zlocation_options(limit=10)
                    logger.info(
                        f"[change_workspace] 准备发送目录选择卡片: options={len(dir_options)}, "
                        f"chat_id={chat_id}, message_id={message_id}"
                    )

                    card_message_id = _platform.send_workspace_selection_card(
                        chat_id=chat_id,
                        chat_type=chat_type,
                        message_id=message_id,
                        dir_list=dir_options,
                    )
                    if card_message_id:
                        with _pending_workspace_lock:
                            _pending_workspace_cards[card_message_id] = {
                                "platform": _platform,
                                "chat_id": chat_id,
                                "chat_type": chat_type,
                                "source_message_id": message_id,
                                "allowed_paths": [opt.get("value", "") for opt in dir_options if isinstance(opt, dict)],
                            }
                            _pending_workspace_latest_by_chat[chat_id] = card_message_id
                        logger.info(
                            f"[change_workspace] 已缓存目录选择上下文: card_message_id={card_message_id}, "
                            f"allowed_paths={len(dir_options)}"
                        )
                    else:
                        _platform.send_message(
                            "❌ 目录选择卡片发送失败，请稍后重试。",
                            chat_id,
                            chat_type,
                            message_id,
                        )
                    return

                # 仅处理实时事件：create_time 超过窗口则丢弃（防止历史补投再次触发）
                delay_seconds = None
                if create_time:
                    try:
                        create_ts = float(create_time) / 1000.0
                        delay_seconds = max(0.0, time.time() - create_ts)
                    except Exception:
                        logger.warning(
                            f"[丢弃判断] create_time 解析失败，跳过实时窗口判断 create_time={create_time}"
                        )

                if delay_seconds is not None and delay_seconds > _REALTIME_WINDOW_SECONDS:
                    logger.info(
                        f"[丢弃事件] 原因=非实时事件 delay={delay_seconds:.2f}s "
                        f"window={_REALTIME_WINDOW_SECONDS}s event_id={event_id} "
                        f"message_id={message_id} chat_id={chat_id} "
                        f"create_time={create_time} create_time_readable={create_time_readable} "
                        f"text={text!r}"
                    )
                    return

                if text:
                    # 异步执行任务（不阻塞主线程）
                    session_key = _build_session_key(chat_type, chat_id)
                    _executor.submit(execute_task_async, text, chat_id, chat_type, message_id, session_key)
                else:
                    logger.info(
                        f"[丢弃事件] 原因=文本为空 message_id={message_id} chat_id={chat_id} "
                        f"raw={str(content_raw)[:120]}"
                    )

    except Exception as e:
        logger.error(f"处理消息失败: {e}")
        import traceback
        traceback.print_exc()


def execute_task_async(task: str, chat_id: str, chat_type: str, message_id: str, session_key: str):
    """异步执行任务（在后台线程中）"""
    try:
        if _platform is None:
            raise RuntimeError("平台未初始化")
        platform = _platform
        adapter = _get_or_create_adapter(chat_type, chat_id)
        start_time = time.time()
        workspace_dir = _get_session_workspace(chat_type, chat_id)
        adapter.executor.workspace_dir = workspace_dir

        # 更新 chat_id
        adapter.chat_id = chat_id

        # 确保 output_handler 已创建并同步到 Executor
        if adapter.output_handler is None:
            from .output import WebhookOutput

            adapter.set_output_handler(WebhookOutput(platform, chat_id))
        else:
            adapter.output_handler.chat_id = chat_id
            adapter.set_output_handler(adapter.output_handler)

        # 执行任务，使用 output_handler.flush() 获取输出
        total_outputs = 0
        for output_list, step_result in adapter.execute_task(task):
            if step_result.pending_commands:
                if _try_auto_execute_pending_commands(adapter, step_result.pending_commands):
                    logger.info("命中自动授权，已自动执行待授权命令，跳过卡片")
                    continue
                if adapter.output_handler and hasattr(adapter.output_handler, "clear"):
                    adapter.output_handler.clear()
                command_content = "\n".join(
                    [
                        cmd.display() if hasattr(cmd, "display") else str(getattr(cmd, "command", ""))
                        for cmd in step_result.pending_commands
                    ]
                ).strip()
                logger.info(
                    f"检测到待授权命令: {len(step_result.pending_commands)} 条，发送授权卡片，命令={command_content[:120]}"
                )
                if isinstance(platform, FeishuPlatform):
                    card_message_id = platform.send_authorization_card(
                        chat_id=chat_id,
                        chat_type=chat_type,
                        message_id=message_id,
                        command_content=command_content,
                    )
                    if card_message_id:
                        with _pending_auth_lock:
                            _pending_authorizations[card_message_id] = {
                                "adapter": adapter,
                                "platform": platform,
                                "chat_id": chat_id,
                                "chat_type": chat_type,
                                "source_message_id": message_id,
                                "pending_commands": step_result.pending_commands,
                            }
                            _pending_latest_card_by_chat[chat_id] = card_message_id
                        logger.info(f"已缓存待授权上下文: card_message_id={card_message_id}")
                    else:
                        logger.error("授权卡片发送失败，未返回 card_message_id")
                else:
                    platform.send_message("检测到待授权命令，请在平台侧确认。", chat_id, chat_type, message_id)
                break

            # 使用 flush() 获取内容（通过回调机制）
            if adapter.output_handler:
                contents = adapter.output_handler.flush()
                if contents:
                    total_outputs += len(contents)
                    combined = "\n".join(contents)
                    platform.send_message(combined, chat_id, chat_type, message_id)
                elif output_list and step_result.action != Action.COMPLETE:
                    # 仅在非完成态启用兜底，避免 COMPLETE 阶段重复发送“任务完成”内容
                    fallback = [item for item in output_list if isinstance(item, str) and item.strip()]
                    if fallback:
                        total_outputs += len(fallback)
                        platform.send_message("\n".join(fallback), chat_id, chat_type, message_id)

            # 检查是否需要等待用户输入
            if step_result.action == Action.WAIT:
                logger.info(
                    f"任务进入 WAIT 状态，已回到主循环等待输入，session_key={session_key}"
                )
                platform.send_message(
                    "✅ 已回到主循环，等待你的下一条指令",
                    chat_id,
                    chat_type,
                    message_id,
                )
                break

        elapsed = time.time() - start_time
        logger.info(f"任务执行完成，共 {total_outputs} 条输出，耗时 {elapsed:.2f}秒，session_key={session_key}")

    except Exception as e:
        logger.error(f"执行任务失败: {e}")
        import traceback
        traceback.print_exc()
        platform.send_message(f"❌ 执行失败: {str(e)}", chat_id, chat_type, message_id)


def main(config: Optional[Config] = None):
    """启动长连接服务

    Args:
        config: 配置对象，如果为 None 则从环境变量加载
    """
    global _config, _platform, _adapters

    # 加载配置
    if config is None:
        config = Config.from_env()
    _config = config

    # 检查必要的环境变量
    app_id = getattr(config, "webhook_app_id", None) or os.environ.get(
        "WEBHOOK_APP_ID", ""
    )
    app_secret = getattr(config, "webhook_app_secret", None) or os.environ.get(
        "WEBHOOK_APP_SECRET", ""
    )

    if not app_id or not app_secret:
        logger.error("WEBHOOK_APP_ID 或 WEBHOOK_APP_SECRET 未设置")
        return

    # 创建平台实例
    _platform = FeishuPlatform(app_id=app_id, app_secret=app_secret)

    # 重置会话适配器池（启动时）
    with _adapters_lock:
        _adapters.clear()

    logger.info("✓ 配置加载成功")
    logger.info("✓ 飞书平台初始化完成")
    logger.info("✓ 会话模式: 按 chat_type + chat_id 隔离上下文")
    logger.info("")
    logger.info("正在启动长连接...")

    # 使用 SDK 启动长连接（官方示例用法）
    try:
        import lark_oapi as lark

        # 创建事件处理器（两个参数必须填空字符串）
        event_handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(handle_message) \
            .register_p2_card_action_trigger(handle_card_action_trigger) \
            .build()

        # 创建长连接客户端
        cli = lark.ws.Client(app_id, app_secret,
                             event_handler=event_handler,
                             log_level=lark.LogLevel.INFO)

        logger.info("长连接已启动，连接飞书服务器中...")
        logger.info("连接成功后，请去飞书后台配置「使用长连接接收事件」")
        logger.info("")
        logger.info("飞书后台配置步骤：")
        logger.info("1. 进入事件订阅")
        logger.info("2. 订阅方式选择「使用长连接接收事件」")
        logger.info("3. 添加事件: im.message.receive_v1")
        logger.info("4. 保存后即可在私聊或群里 @机器人 发送任务")
        logger.info("")

        # 启动长连接（阻塞）
        cli.start()

    except AttributeError as e:
        logger.error(f"长连接模块不可用: {e}")
        logger.info("可能的原因：")
        logger.info("1. lark-oapi 版本过旧，请升级: pip install -U lark-oapi")
    except Exception as e:
        logger.error(f"启动长连接失败: {e}")


if __name__ == "__main__":
    main()
