"""极简 Agent 核心模块 - 上下文切换架构"""

import json
import re
import sys
import threading
import time
import uuid
import os
from dataclasses import dataclass, field
from typing import Generator, Optional, Any, Callable
from enum import Enum

from .config import Config
from .llm import create_client, ChatMessage
from .output_handler import OutputHandler, NullOutputHandler


class Action(Enum):
    """Agent执行后的动作"""
    CONTINUE = "continue"  # 继续执行下一轮
    WAIT = "wait"  # 等待用户输入
    SWITCH_TO_CHILD = "switch_to_child"  # 切换到子Agent
    RETURN_TO_PARENT = "return_to_parent"  # 返回父Agent
    COMPLETE = "complete"  # 任务完成


@dataclass
class StepResult:
    """单步执行结果"""
    outputs: list[str]  # 输出内容列表（不含命令框）
    action: Action  # 下一步动作
    data: Any = None  # 携带数据（切换时的任务/摘要）
    pending_commands: list[str] = field(default_factory=list)  # 待确认的命令列表
    command_blocks: list[str] = field(default_factory=list)  # 命令框显示内容（逐个显示）


@dataclass
class ChildTaskRequest:
    """子任务请求"""
    task: str  # 任务描述
    global_count: int  # 全局子 agent 计数
    agent_name: Optional[str] = None  # 预定义 agent 名称（如果有）


@dataclass
class Message:
    """消息记录"""
    role: str  # system, user, assistant
    content: str
    timestamp: float = field(default_factory=time.time)
    think: str = ""


class SimpleAgent:
    """极简任务执行 Agent - 统一逻辑，上下文切换

    每个Agent独立执行，通过切换上下文实现嵌套：
    1. 执行中：step() 返回 CONTINUE
    2. 需要子Agent：step() 返回 SWITCH_TO_CHILD，携带任务
    3. 子Agent完成：收到摘要，step() 返回 CONTINUE
    4. 等待用户：step() 返回 WAIT
    5. 任务完成：step() 返回 COMPLETE
    """

    def __init__(self, config: Optional[Config] = None,
                 depth: int = 0, max_depth: int = 4,
                 global_subagent_count: int = 0,
                 agent_name: Optional[str] = None,
                 output_handler: Optional[OutputHandler] = None,
                 init_system_prompt: bool = True):
        """初始化 Agent

        Args:
            config: 配置对象
            depth: 当前深度（0=顶级）
            max_depth: 最大允许深度（默认4层）
            global_subagent_count: 全局已使用的子Agent数量（累加计数）
            agent_name: 当前 agent 名称（用于过滤 forbidden_agents）
            output_handler: 输出处理器（可选）
            init_system_prompt: 是否初始化系统提示词（反序列化时应关闭）
        """
        self.config = config or Config.from_env()
        self.history: list[Message] = []
        self.start_time = 0.0

        # Agent标识
        self.agent_id = str(uuid.uuid4())[:8]
        self.depth = depth
        self.max_depth = max_depth
        self.agent_name = agent_name  # 当前 agent 名称

        # 统计
        self.total_sub_agents_created = 0
        self.total_commands_executed = 0

        # 全局配额（累加计数，而不是递减剩余量）
        self._global_subagent_count = global_subagent_count

        # 输出处理器（使用 NullOutputHandler 避免 None 判断）
        self._output_handler = output_handler or NullOutputHandler()

        # 初始化系统消息（反序列化时可关闭）
        if init_system_prompt:
            self._init_system_prompt()

        # 待执行的子Agent任务请求（step()返回时携带）
        self._pending_child_request: Optional[ChildTaskRequest] = None

        # 仅保留上一轮思考内容（同时写入 history 便于回放）
        self.last_think: str = ""

        # LLM调用前后的回调函数（用于保存快照）
        self._before_llm_callback: Optional[callable] = None
        self._after_llm_callback: Optional[callable] = None

        # Executor 引用（用于访问命令确认回调）
        self._executor: Optional['Executor'] = None

        # 上下文压缩状态
        self._last_compact_message_count = 0
        self._last_compact_time = 0.0
        self._is_compacting = False

    def set_before_llm_callback(self, callback: callable):
        """设置LLM调用前的回调函数

        Args:
            callback: 回调函数，签名为 callback(agent: SimpleAgent) -> None
        """
        self._before_llm_callback = callback

    def set_after_llm_callback(self, callback: callable):
        """设置LLM响应后的回调函数

        Args:
            callback: 回调函数，签名为 callback(agent: SimpleAgent) -> None
        """
        self._after_llm_callback = callback

    def _init_system_prompt(self):
        """初始化系统提示词"""
        max_agents = self.max_depth ** 2
        local_remaining = max_agents - self.total_sub_agents_created

        # 全局配额（累加计数）
        global_total = max_agents * 2  # 全局配额是单Agent的2倍
        global_used = self._global_subagent_count

        # 估算当前上下文使用量
        context_used = self._estimate_context_tokens()
        context_total = self.config.num_ctx  # 假设总窗口是输出的4倍
        context_remaining = context_total - context_used
        context_percent = (context_used / context_total) * 100

        tree_info = f"""
**当前状态：**
- 当前工作目录: {os.getcwd()}
- Agent ID: {self.agent_id}
- 当前深度: {self.depth}
- 最大深度: {self.max_depth}
- 已创建子Agent: {self.total_sub_agents_created}
- 本地配额: {local_remaining}/{max_agents}（当前Agent还能创建 {local_remaining} 个子任务）
- 全局配额: {global_used}/{global_total}（整个任务累计已使用 {global_used} 个子任务，总计 {global_total} 个）
- 上下文使用: {context_used}/{context_total} tokens ({context_percent:.1f}%)
- 剩余可用: {context_remaining} tokens
"""

        predefined_agents = self._load_predefined_agent_metadata()
        predefined_section = self._format_predefined_agent_section(predefined_agents)

        # 加载模板并渲染
        template = self._load_system_prompt_template()
        base_system_prompt = template.format(
            tree_info=tree_info,
            predefined_section=predefined_section
        )

        self.history.append(Message(role="system", content=base_system_prompt))

    def _load_predefined_agent_metadata(self) -> list[dict[str, str]]:
        """加载预定义 agent 的元数据（从 .json 文件）"""
        agents_dir = self._get_project_agents_dir()
        if not agents_dir:
            return []
        agents: list[dict[str, str]] = []

        # 遍历 .json 文件
        for filename in sorted(os.listdir(agents_dir)):
            if not filename.lower().endswith(".json"):
                continue

            json_path = os.path.join(agents_dir, filename)
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
                # 确保 name 字段存在（从文件名推断）
                if "name" not in metadata:
                    base_name = os.path.splitext(filename)[0]
                    metadata["name"] = base_name
                metadata["file"] = filename
                agents.append(metadata)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue

        return agents

    def _get_project_agents_dir(self) -> str:
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        agents_dir = os.path.join(project_root, "agents")
        return agents_dir if os.path.isdir(agents_dir) else ""

    def _get_templates_dir(self) -> str:
        """获取模板目录路径"""
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        templates_dir = os.path.join(project_root, "templates")
        return templates_dir if os.path.isdir(templates_dir) else ""

    def _load_system_prompt_template(self) -> str:
        """加载系统提示词模板"""
        templates_dir = self._get_templates_dir()
        if not templates_dir:
            # 如果模板目录不存在，返回默认模板（向后兼容）
            return ""

        template_path = os.path.join(templates_dir, "system_prompt.txt")
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return ""

    def _format_predefined_agent_section(self, agents: list[dict[str, str]]) -> str:
        """格式化预定义 agent 部分，注入 system_prompt_injection

        遍历所有预定义 agent，检查当前 agent_name 是否在各自的 forbidden_agents 中，
        如果不在，则收集该 agent 的 system_prompt_injection，最后返回拼接后的注入内容。
        """
        # 收集不在 forbidden 中的 system_prompt_injection
        injections = []
        for agent in agents:
            agent_name = agent.get("name", "")
            # 获取 forbidden_agents（JSON 中是 list 类型）
            forbidden = agent.get("forbidden_agents", [])

            # 兼容处理：如果是字符串，解析为列表
            forbidden_list = []
            if isinstance(forbidden, list):
                forbidden_list = forbidden
            elif isinstance(forbidden, str):
                # 解析 YAML 列表格式: [a, b, c]
                forbidden = forbidden.strip()
                if forbidden.startswith("[") and forbidden.endswith("]"):
                    items = forbidden[1:-1].split(",")
                    forbidden_list = [item.strip().strip("'").strip('"') for item in items if item.strip()]
                else:
                    # 单个值
                    forbidden_list = [forbidden.strip().strip("'").strip('"')]

            # 检查当前 agent_name 是否在该 agent 的 forbidden_agents 中
            # 如果不在，注入该 agent 的 system_prompt_injection
            should_inject = self.agent_name not in forbidden_list
            injection = agent.get("system_prompt_injection", "")

            if should_inject and injection:
                injections.append(injection)

        # 返回拼接后的注入内容（如果有的话）
        if injections:
            return "\n\n".join(injections) + "\n"

        # 如果没有注入内容，返回空字符串
        return ""

    def _get_agent_forbidden_agents(self, agent_name: str) -> list[str]:
        """获取指定 agent 的 forbidden_agents 列表"""
        agents_dir = self._get_project_agents_dir()
        if not agents_dir:
            return []

        for filename in os.listdir(agents_dir):
            base_name = os.path.splitext(filename)[0].replace('_', '-')
            if base_name.lower() == agent_name.lower().replace('_', '-'):
                path = os.path.join(agents_dir, filename)
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        text = handle.read()
                    metadata = self._parse_agent_frontmatter(text)
                    # 解析 forbidden_agents 列表
                    forbidden_str = metadata.get("forbidden_agents", "")
                    if not forbidden_str:
                        return []
                    # 解析 YAML 列表格式: [a, b, c]
                    forbidden_str = forbidden_str.strip()
                    if forbidden_str.startswith("[") and forbidden_str.endswith("]"):
                        items = forbidden_str[1:-1].split(",")
                        return [item.strip().strip("'").strip('"') for item in items if item.strip()]
                    return [forbidden_str.strip().strip("'").strip('"')]
                except (OSError, UnicodeDecodeError):
                    return []
        return []

    def _load_agent_full_content(self, agent_name: str) -> Optional[str]:
        """加载预定义 agent 的完整 markdown 内容

        Args:
            agent_name: agent 名称（如 'file-edit'）

        Returns:
            markdown 正文内容，如果文件不存在则返回 None
        """
        agents_dir = self._get_project_agents_dir()
        if not agents_dir:
            return None

        # 查找匹配的 .md 文件（支持 file-edit.md 或 file_edit.md）
        for filename in os.listdir(agents_dir):
            # 只处理 .md 文件，跳过 .json 文件
            if not filename.lower().endswith(".md"):
                continue

            base_name = os.path.splitext(filename)[0].replace('_', '-')
            if base_name.lower() == agent_name.lower().replace('_', '-'):
                path = os.path.join(agents_dir, filename)
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        return handle.read().strip()
                except (OSError, UnicodeDecodeError):
                    return None
        return None

    def _get_agent_flow_name(self, agent_name: str) -> Optional[str]:
        """获取预定义 agent 的流程名（读取首个非空行）"""
        agents_dir = self._get_project_agents_dir()
        if not agents_dir:
            return None

        for filename in os.listdir(agents_dir):
            if not filename.lower().endswith(".md"):
                continue

            base_name = os.path.splitext(filename)[0].replace('_', '-')
            if base_name.lower() == agent_name.lower().replace('_', '-'):
                path = os.path.join(agents_dir, filename)
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        for line in handle:
                            stripped = line.strip()
                            if not stripped:
                                continue
                            return stripped.lstrip("#").strip()
                except (OSError, UnicodeDecodeError):
                    return None
        return None

    def start(self, task: str):
        """开始执行任务（初始化）

        Args:
            task: 用户任务描述
        """
        self.start_time = time.time()
        self._add_message("user", task)

    def step(self) -> StepResult:
        """执行一步

        Returns:
            StepResult: 执行结果

        快照时机：
        1. _call_llm() 内部：调用前保存前快照（不含 assistant）
        2. 添加消息后：调用后保存后快照（含 assistant）
        """
        # 调用LLM（内部会触发 before_llm_callback 保存前快照）
        content, reasoning = self._call_llm()
        self.last_think = reasoning.strip() if reasoning else ""

        # 调用回调：输出思考内容
        if reasoning and reasoning.strip():
            self._output_handler.on_think(reasoning)

        # 只有 content 参与标签解析
        response = content
        filtered_response, has_tool_tags = self._filter_action_blocks(response)
        if has_tool_tags:
            response = filtered_response

        # 添加 assistant 消息到历史
        self._add_message("assistant", response, think=reasoning)

        # LLM 响应后：保存后快照（含完整对话）
        if self._after_llm_callback:
            self._after_llm_callback(self)

        # 调用回调：输出普通内容
        if response and response.strip():
            self._output_handler.on_content(response)

        # 格式化输出（保持向后兼容）
        outputs = []
        if reasoning and reasoning.strip():
            outputs.append(f"\n** 思考过程：\n")
            outputs.append(f"{'━'*50}\n")
            outputs.append(f"{reasoning}\n")
            outputs.append(f"{'━'*50}\n\n")
        if response and response.strip():
            outputs.append(response)

        # 解析并执行标签，收集输出和命令（使用带回调的版本）
        tool_outputs, pending_commands, command_blocks = self._parse_tools_with_callbacks(response)
        outputs.extend(tool_outputs)

        # 检查是否需要切换到子Agent
        if self._pending_child_request:
            request = self._pending_child_request
            self._pending_child_request = None

            # 设置正确的 global_count
            request.global_count = self._global_subagent_count + 1

            task = request.task
            agent_name = request.agent_name or ""
            new_global_count = request.global_count

            # 检查深度限制
            if self.depth >= self.max_depth:
                self._add_message("system", f"[深度限制] 请直接执行任务: {task}")
                self._output_handler.on_depth_limit()
                outputs.append(f"\n!! [深度限制]\n")
                outputs.append(f"已达到最大深度 {self.max_depth}，由当前Agent执行\n")
                outputs.append(f"{'═'*50}\n\n")
                return StepResult(outputs=self._add_depth_prefix(outputs), action=Action.CONTINUE, pending_commands=pending_commands, command_blocks=command_blocks)

            # 检查本地配额限制
            if self.total_sub_agents_created >= self.max_depth ** 2:
                self._output_handler.on_quota_limit("local")
                outputs.append(f"\n!! [本地配额限制]\n")
                outputs.append(f"当前Agent已用完 {self.max_depth ** 2} 个子Agent配额\n")
                outputs.append(f"{'═'*50}\n\n")
                self._add_message("system", f"[本地配额限制] 请直接执行任务: {task}")
                return StepResult(outputs=self._add_depth_prefix(outputs), action=Action.CONTINUE, pending_commands=pending_commands, command_blocks=command_blocks)

            # 检查全局配额限制（防止层级间循环）- 累加计数
            global_total = self.max_depth ** 2 * 2
            if self._global_subagent_count >= global_total:
                self._output_handler.on_quota_limit("global")
                outputs.append(f"\n!! [全局配额限制]\n")
                outputs.append(f"整个任务已用完所有 {global_total} 个子Agent配额\n")
                outputs.append(f"{'═'*50}\n\n")
                self._add_message("system", f"[全局配额限制] 请直接执行任务: {task}")
                return StepResult(outputs=self._add_depth_prefix(outputs), action=Action.CONTINUE, pending_commands=pending_commands, command_blocks=command_blocks)

            self.total_sub_agents_created += 1

            # 获取元数据用于显示
            context_used = self._estimate_context_tokens()
            context_total = self.config.num_ctx

            # 调用回调：输出创建子 agent 信息
            self._output_handler.on_create_agent(
                task=task,
                depth=self.depth + 1,
                agent_name=agent_name,
                context_info={
                    "context_used": context_used,
                    "context_total": context_total,
                    "global_count": new_global_count,
                    "global_total": global_total,
                    "max_depth": self.max_depth
                }
            )

            outputs.append(f"\n{'+'*60}\n")
            agent_info = f" [{agent_name}]" if agent_name else ""
            outputs.append(f"深度: {self.depth + 1}/{self.max_depth}{agent_info} | 任务: {task}\n")
            outputs.append(f"上下文: {context_used}/{context_total} | 配额: {new_global_count}/{global_total}\n")
            outputs.append(f"{'+'*60}\n\n")

            # 返回 ChildTaskRequest 对象
            return StepResult(outputs=self._add_depth_prefix(outputs), action=Action.SWITCH_TO_CHILD, data=request, pending_commands=pending_commands, command_blocks=command_blocks)

        # 检查是否完成
        if not has_tool_tags and self._is_completed(response):
            summary = self._extract_return(response)
            # 调用回调：输出完成信息
            stats = self.get_summary()
            self._output_handler.on_agent_complete(summary, stats)
            return StepResult(outputs=self._add_depth_prefix(outputs), action=Action.COMPLETE, data=summary, pending_commands=pending_commands, command_blocks=command_blocks)

        # 检查是否需要等待用户输入
        # 只有当没有任何标签时才等待（reasoning 不影响）
        if not self._has_action_tags(response) and not tool_outputs:
            self._output_handler.on_wait_input()
            outputs.append(f"\n?? 等待用户输入...\n")
            outputs.append("[等待用户输入]\n")  # 保留供 cli.py 检测
            return StepResult(outputs=self._add_depth_prefix(outputs), action=Action.WAIT, pending_commands=pending_commands, command_blocks=command_blocks)

        return StepResult(outputs=self._add_depth_prefix(outputs), action=Action.CONTINUE, pending_commands=pending_commands, command_blocks=command_blocks)

    def on_child_completed(self, summary: str, global_count: int):
        """子Agent完成时的回调

        Args:
            summary: 子Agent的完成摘要
            global_count: 同步全局子Agent计数
        """
        if summary:
            # 子Agent结果是工具执行的输出，用user角色（兼容不支持tool角色的API）
            self._add_message("assistant", f"<child_summary>\n{summary}\n</child_summary>")
        # 同步全局计数
        self._global_subagent_count = global_count

    def _add_message(self, role: str, content: str, think: str = ""):
        """添加消息到历史记录"""
        normalized_think = think.strip() if think else ""
        self.history.append(Message(role=role, content=content, think=normalized_think))

    def _add_depth_prefix(self, outputs: list[str]) -> list[str]:
        """给所有输出添加深度前缀（+号）

        Args:
            outputs: 原始输出列表

        Returns:
            添加了深度前缀的输出列表
        """
        prefix = "+" * self.depth + " " if self.depth > 0 else ""
        if not prefix:
            return outputs

        result = []
        for output in outputs:
            # 按行分割，给每行添加前缀
            lines = output.split('\n')
            prefixed_lines = [prefix + line if line.strip() else line for line in lines]
            result.append('\n'.join(prefixed_lines))
        return result

    def _estimate_context_tokens(self) -> int:
        """估算当前上下文使用的 token 数

        使用简单估算：4 字符 ≈ 1 token（适用于中文混合）
        """
        total_chars = sum(len(msg.content) for msg in self.history)
        return total_chars // 4

    def _format_messages_for_summary(self, messages: list[Message]) -> str:
        """将消息列表格式化为摘要输入文本。"""
        role_map = {
            "system": "系统",
            "user": "用户",
            "assistant": "助手",
            "tool": "工具",
        }
        lines = []
        for msg in messages:
            content = msg.content.strip()
            if not content:
                continue
            role_label = role_map.get(msg.role, msg.role)
            lines.append(f"[{role_label}] {content}")
        return "\n".join(lines)

    def _summarize_text(self, text: str) -> str:
        """调用 LLM 对文本做一次摘要。"""
        system_prompt = (
            "你是会话压缩助手。请用中文将以下对话压缩成可继续执行的工作摘要。\n"
            "要求：\n"
            "1) 保留任务目标、关键决策、已完成事项。\n"
            "2) 保留重要上下文：文件路径、命令、配置、参数、接口、约定。\n"
            "3) 明确未完成的待办和风险。\n"
            "4) 不要编造，保持简洁，优先要点列表。\n"
            "5) 不要输出任何工具标签或 XML 标签。"
        )
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=text),
        ]
        client = create_client(self.config)
        max_tokens = min(2048, self.config.max_output_tokens)
        response = client.chat(messages, max_tokens)
        return response.content.strip()

    def _summarize_long_text(self, text: str) -> str:
        """分段摘要长文本，避免一次输入过长。"""
        chunk_chars = max(2000, int(self.config.compact_chunk_chars))
        if len(text) <= chunk_chars:
            return self._summarize_text(text)

        lines = text.splitlines()
        chunks = []
        current = []
        current_len = 0
        for line in lines:
            line_len = len(line) + 1
            if current and current_len + line_len > chunk_chars:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            current.append(line)
            current_len += line_len
        if current:
            chunks.append("\n".join(current))

        summaries = []
        for idx, chunk in enumerate(chunks, start=1):
            chunk_text = f"分段 {idx}/{len(chunks)}:\n{chunk}"
            summaries.append(self._summarize_text(chunk_text))

        if len(summaries) == 1:
            return summaries[0]

        combined = "\n\n".join(
            f"[分段摘要 {idx}]\n{summary}" for idx, summary in enumerate(summaries, start=1)
        )
        return self._summarize_text(combined)

    def should_auto_compact(self) -> bool:
        """判断是否需要自动压缩上下文。"""
        if not self.config.auto_compact:
            return False
        context_used = self._estimate_context_tokens()
        threshold = int(self.config.num_ctx * self.config.auto_compact_threshold)
        if context_used < threshold:
            return False
        if len(self.history) <= self.config.compact_keep_messages + 2:
            return False
        if self._last_compact_message_count == len(self.history):
            return False
        return True

    def compact_history(self, reason: str = "手动压缩", keep_last: Optional[int] = None) -> dict:
        """压缩历史上下文，保留关键摘要和最近消息。"""
        if self._is_compacting:
            return {"compacted": False, "reason": "compacting"}

        keep_last = keep_last if keep_last is not None else self.config.compact_keep_messages
        total_before = len(self.history)
        if total_before <= keep_last + 2:
            return {"compacted": False, "reason": "too_short", "messages": total_before}

        keep_indices = set()
        if self.history and self.history[0].role == "system":
            keep_indices.add(0)
        tail_start = max(total_before - keep_last, 0)
        keep_indices.update(range(tail_start, total_before))

        messages_to_summarize = [
            msg for idx, msg in enumerate(self.history) if idx not in keep_indices
        ]
        if not messages_to_summarize:
            return {"compacted": False, "reason": "no_target", "messages": total_before}

        summary_input = self._format_messages_for_summary(messages_to_summarize)
        if not summary_input.strip():
            return {"compacted": False, "reason": "empty", "messages": total_before}

        context_before = self._estimate_context_tokens()
        self._is_compacting = True
        try:
            summary = self._summarize_long_text(summary_input)
        except Exception as exc:
            return {"compacted": False, "reason": f"error: {exc}"}
        finally:
            self._is_compacting = False

        summary = summary.strip() or "（空摘要）"
        summary_message = Message(
            role="system",
            content=f"以下是历史摘要（{reason}）：\n{summary}",
        )

        new_history = []
        if 0 in keep_indices:
            new_history.append(self.history[0])
        new_history.append(summary_message)
        for idx in range(tail_start, total_before):
            if idx == 0:
                continue
            new_history.append(self.history[idx])

        self.history = new_history
        self._last_compact_message_count = len(self.history)
        self._last_compact_time = time.time()
        context_after = self._estimate_context_tokens()

        return {
            "compacted": True,
            "reason": reason,
            "messages_before": total_before,
            "messages_after": len(self.history),
            "context_before": context_before,
            "context_after": context_after,
            "summary": summary,
        }

    def _call_llm(self) -> tuple[str, str]:
        """调用LLM，返回 (content, reasoning)"""
        # 触发保存快照回调（在发送消息前保存当前状态）
        if self._before_llm_callback:
            self._before_llm_callback(self)

        messages = [ChatMessage(role=msg.role, content=msg.content) for msg in self.history]

        # 使用 LLM 客户端
        client = create_client(self.config)

        try:
            response = client.chat(messages, self.config.max_output_tokens)
            return response.content, response.reasoning
        except Exception as e:
            raise RuntimeError(f"调用LLM失败: {e}")

    def _filter_action_blocks(self, response: str) -> tuple[str, bool]:
        """Keep only tool tags when present to avoid mixed output."""
        pattern = r"<ps_call>.*?</ps_call>|<builtin>.*?</builtin>|<create_agent(?:\s+name=(\S+?))?\s*>.*?</create_agent>"
        matches = list(re.finditer(pattern, response, re.DOTALL | re.IGNORECASE))
        if not matches:
            return response, False
        blocks = [match.group(0).strip() for match in matches]
        return "\n".join(blocks), True

    def _has_action_tags(self, response: str) -> bool:
        """检查是否有操作标签"""
        return bool(re.search(r'<(ps_call|builtin|create_agent|return)\b', response, re.IGNORECASE))

    def _is_completed(self, response: str) -> bool:
        """检查是否完成"""
        return bool(re.search(r'<return\b', response, re.IGNORECASE))

    def _extract_return(self, response: str) -> str:
        """提取返回内容"""
        match = re.search(r'<return>\s*(.+?)\s*</return>', response, re.DOTALL)
        return match.group(1) if match else "任务完成"

    def _normalize_builtin_command(self, command: str) -> str:
        """将 <builtin> 标签内容补全为 builtin.* 命令格式。"""
        lines = command.splitlines()
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            if line.strip().lower().startswith("builtin."):
                return command
            match = re.match(r"(\s*)(read_file|smart_edit)(\b.*)", line, re.IGNORECASE)
            if match:
                indent, tool, rest = match.groups()
                lines[index] = f"{indent}builtin.{tool}{rest}"
                return "\n".join(lines)
            return command
        return command

    def _parse_tools(self, response: str) -> tuple[list[str], list[str], list[str]]:
        """解析工具标签

        Returns:
            (outputs, commands, command_blocks): 输出内容列表、待执行命令列表、命令框显示内容
        """
        outputs = []
        commands = []
        command_blocks = []

        # 执行PowerShell命令与内置工具
        for match in re.finditer(r'<(ps_call|builtin)>\s*(.+?)\s*</\1>', response, re.DOTALL | re.IGNORECASE):
            tag_name = match.group(1).lower()
            command = match.group(2).strip()
            if tag_name == "builtin":
                command = self._normalize_builtin_command(command)
            self.total_commands_executed += 1  # 解析时分配编号

            # 命令框单独存储，不放入 outputs
            block = f"\n>> [待执行命令 #{self.total_commands_executed}]\n命令: {command}\n{'━'*50}\n\n"
            # 添加深度前缀到命令框
            prefix = "+" * self.depth + " " if self.depth > 0 else ""
            if prefix:
                lines = block.split('\n')
                prefixed_lines = [prefix + line if line.strip() else line for line in lines]
                block = '\n'.join(prefixed_lines)
            command_blocks.append(block)
            commands.append(command)

        # 创建子Agent（解析 name 属性）
        # 支持格式：<create_agent name=xxx>任务</create_agent> 或 <create_agent>任务</create_agent>
        for match in re.finditer(r'<create_agent(?:\s+name=(\S+?))?\s*>(.+?)</create_agent>', response, re.DOTALL):
            if not self._pending_child_request:  # 只处理第一个
                agent_name = match.group(1)  # name 属性值（如果有）
                if agent_name:
                    # 去除可能存在的引号（单引或双引）
                    agent_name = agent_name.strip('"').strip("'")
                task_content = match.group(2).strip()
                # 创建 ChildTaskRequest 对象
                # 注意：global_count 和 new_global_count 在 step() 中设置
                self._pending_child_request = ChildTaskRequest(
                    task=task_content,
                    global_count=0,  # 会在 step() 中更新
                    agent_name=agent_name
                )
                break

        return outputs, commands, command_blocks

    def _parse_tools_with_callbacks(self, response: str) -> tuple[list[str], list[str], list[str]]:
        """解析工具标签并调用回调

        Returns:
            (outputs, commands, command_blocks): 输出内容列表、待执行命令列表、命令框显示内容
        """
        outputs = []
        commands = []
        command_blocks = []

        # 执行PowerShell命令与内置工具
        for match in re.finditer(r'<(ps_call|builtin)>\s*(.+?)\s*</\1>', response, re.DOTALL | re.IGNORECASE):
            tag_name = match.group(1).lower()
            command = match.group(2).strip()
            if tag_name == "builtin":
                command = self._normalize_builtin_command(command)
            self.total_commands_executed += 1  # 解析时分配编号

            # 调用回调
            depth_prefix = "+" * self.depth + " " if self.depth > 0 else ""
            self._output_handler.on_ps_call(command, self.total_commands_executed, depth_prefix)

            # 命令框单独存储，不放入 outputs（兼容 CLI）
            block = f"\n>> [待执行命令 #{self.total_commands_executed}]\n命令: {command}\n{'━'*50}\n\n"
            if depth_prefix:
                lines = block.split('\n')
                prefixed_lines = [depth_prefix + line if line.strip() else line for line in lines]
                block = '\n'.join(prefixed_lines)
            command_blocks.append(block)
            commands.append(command)

        # 创建子Agent（解析 name 属性）
        # 支持格式：<create_agent name=xxx>任务</create_agent> 或 <create_agent>任务</create_agent>
        for match in re.finditer(r'<create_agent(?:\s+name=(\S+?))?\s*>(.+?)</create_agent>', response, re.DOTALL):
            if not self._pending_child_request:  # 只处理第一个
                agent_name = match.group(1)  # name 属性值（如果有）
                if agent_name:
                    # 去除可能存在的引号（单引或双引）
                    agent_name = agent_name.strip('"').strip("'")
                task_content = match.group(2).strip()
                # 创建 ChildTaskRequest 对象
                # 注意：global_count 和 new_global_count 在 step() 中设置
                self._pending_child_request = ChildTaskRequest(
                    task=task_content,
                    global_count=0,  # 会在 step() 中更新
                    agent_name=agent_name
                )
                break

        return outputs, commands, command_blocks

    def _strip_trailing_after_ps_call(self, response: str) -> str:
        """当包含 ps_call 或 builtin 时，去掉最后一个工具标签之后的文本，防止无回执的结果输出"""
        if not re.search(r'<(ps_call|builtin)\b', response, re.IGNORECASE):
            return response
        matches = list(re.finditer(r'</(ps_call|builtin)>', response, re.IGNORECASE))
        if not matches:
            return response
        last_end = matches[-1].end()
        if response[last_end:].strip():
            return response[:last_end].rstrip()
        return response

    def get_summary(self) -> dict:
        """获取执行摘要"""
        return {
            "agent_id": self.agent_id,
            "depth": self.depth,
            "commands": self.total_commands_executed,
            "sub_agents": self.total_sub_agents_created,
            "duration": time.time() - self.start_time,
        }


@dataclass
class CommandResult:
    command: str
    stdout: str
    stderr: str
    returncode: int


class Executor:
    """Agent执行器 - 上下文切换

    维护上下文栈，处理Agent切换逻辑
    """

    def __init__(self, config: Optional[Config] = None, max_depth: int = 4, session_manager: Optional['SessionManager'] = None,
                 output_handler: Optional[OutputHandler] = None, command_confirm_callback: Optional[Callable[[str], str]] = None):
        """初始化执行器

        Args:
            config: 配置对象
            max_depth: 最大深度
            session_manager: 会话管理器（用于保存快照）
            output_handler: 输出处理器（可选）
            command_confirm_callback: 命令确认回调函数（可选）
                输入: command (str)
                输出: result_message (str) 格式: '<ps_call_result id="executed">...</ps_call_result>'
        """
        self.config = config or Config.from_env()
        self.max_depth = max_depth
        self.context_stack: list[SimpleAgent] = []
        self.current_agent: Optional[SimpleAgent] = None
        self._is_running = False

        # 全局子Agent配额（防止层级间循环）- 累加计数
        self._global_subagent_total = max_depth ** 2 * 2  # 总配额
        self._global_subagent_count = 0  # 已使用的子Agent数量

        # 保存最近的 StepResult，供 cli.py 访问
        self.last_step_result: Optional[StepResult] = None

        # 自动同意：当前目录安全文件操作自动执行（线程安全）
        self._auto_approve_lock = threading.Lock()
        self._auto_approve: bool = False

        # 会话管理（用于快照保存）
        self.session_manager = session_manager
        self._snapshot_index = 0  # 当前快照索引

        # 输出处理器（使用 NullOutputHandler 避免 None 判断）
        self._output_handler = output_handler or NullOutputHandler()

        # 命令确认回调（用于 CLI 和 GUI 的不同确认逻辑）
        self._command_confirm_callback = command_confirm_callback

    @property
    def auto_approve(self) -> bool:
        """获取 auto_approve 状态（线程安全）"""
        with self._auto_approve_lock:
            return self._auto_approve

    @auto_approve.setter
    def auto_approve(self, value: bool):
        """设置 auto_approve 状态（线程安全）"""
        with self._auto_approve_lock:
            self._auto_approve = value

    def set_command_confirm_callback(self, callback: Callable[[str], str]):
        """设置命令确认回调

        Args:
            callback: 命令确认回调函数
                输入: command (str)
                输出: result_message (str) 格式: '<ps_call_result id="executed">...</ps_call_result>'
        """
        self._command_confirm_callback = callback

    def _maybe_auto_compact(self) -> Optional[str]:
        """根据阈值自动压缩上下文，返回提示信息。"""
        if not self.current_agent:
            return None
        if not self.current_agent.should_auto_compact():
            return None

        result = self.current_agent.compact_history(reason="自动压缩")
        if not result.get("compacted"):
            return None

        return (
            f"\n[自动压缩] 已压缩历史上下文："
            f"{result.get('messages_before')} -> {result.get('messages_after')} | "
            f"{result.get('context_before')} -> {result.get('context_after')}\n"
        )

    def _create_agent(self, depth: int = 0, global_subagent_count: int = 0, agent_name: Optional[str] = None) -> SimpleAgent:
        """创建 Agent 并设置回调

        Args:
            depth: 当前深度
            global_subagent_count: 全局子Agent计数
            agent_name: 预定义 agent 名称

        Returns:
            配置好的 SimpleAgent 实例
        """
        agent = SimpleAgent(
            config=self.config,
            depth=depth,
            max_depth=self.max_depth,
            global_subagent_count=global_subagent_count,
            agent_name=agent_name,
            output_handler=self._output_handler  # 传递 output_handler
        )
        # 设置双回调（前快照 + 后快照）
        if self.session_manager:
            agent.set_before_llm_callback(self._before_llm_snapshot_callback)
            agent.set_after_llm_callback(self._after_llm_snapshot_callback)
        return agent

    def _before_llm_snapshot_callback(self, agent: SimpleAgent):
        """LLM调用前的保存回调（前快照，用于回滚）

        索引策略：
        - 使用当前索引 N，保存后不递增
        - 等待 after_llm_snapshot_callback 递增到 N+2

        Args:
            agent: 即将调用 LLM 的 Agent
        """
        if self.session_manager and self.session_manager.current_session_id is not None:
            session_id = self.session_manager.current_session_id
            self.session_manager.save_snapshot(
                executor=self,
                session_id=session_id,
                snapshot_index=self._snapshot_index
            )
            # 注意：不递增索引，留给 after_llm_snapshot_callback

    def _after_llm_snapshot_callback(self, agent: SimpleAgent):
        """LLM响应后的保存回调（后快照，含完整状态）

        索引策略：
        - 使用索引 N+1（前快照之后）
        - 保存后递增到 N+2（为下一轮做准备）

        Args:
            agent: 已获取 LLM 响应的 Agent
        """
        if self.session_manager and self.session_manager.current_session_id is not None:
            session_id = self.session_manager.current_session_id
            self.session_manager.save_after_snapshot(
                executor=self,
                session_id=session_id,
                snapshot_index=self._snapshot_index
            )
            # 下一轮递增一次
            self._snapshot_index += 1

    def run(self, task: str) -> Generator[tuple[list[str], StepResult], None, None]:
        """运行任务

        Args:
            task: 用户任务描述

        Yields:
            tuple[list[str], StepResult]: (输出片段列表, 执行结果)
        """
        # 创建顶级Agent（如果不存在）
        if not self.current_agent:
            self.current_agent = self._create_agent(
                depth=0,
                global_subagent_count=self._global_subagent_count
            )
            self.current_agent.start(task)
        else:
            # 复用现有 agent，添加新任务到历史
            self.current_agent.start(task)

        self._is_running = True
        yield from self._execute_loop()

    def resume(self, user_input: str) -> Generator[tuple[list[str], StepResult], None, None]:
        """恢复执行（用户输入后）

        Args:
            user_input: 用户输入内容

        Yields:
            tuple[list[str], StepResult]: (输出片段列表, 执行结果)
        """
        if not self.current_agent:
            return

        self.current_agent._add_message("user", user_input)
        self._is_running = True
        yield from self._execute_loop()

    def _execute_loop(self) -> Generator[tuple[list[str], StepResult], None, None]:
        """执行循环（内部方法）

        Yields:
            tuple[list[str], StepResult]: (输出片段列表, 执行结果)
        """
        while self.current_agent and self._is_running:
            auto_notice = self._maybe_auto_compact()
            if auto_notice:
                yield ([auto_notice], StepResult(outputs=[auto_notice], action=Action.CONTINUE))

            result = self.current_agent.step()

            # 保存 StepResult 供 cli.py 访问
            self.last_step_result = result

            # yield 输出和结果，让 CLI/GUI 先显示输出，然后自己处理命令确认
            yield (result.outputs, result)

            if result.action == Action.SWITCH_TO_CHILD:
                # result.data 现在是 ChildTaskRequest 对象
                request: ChildTaskRequest = result.data
                self.context_stack.append(self.current_agent)

                # 如果有预定义 agent，加载内容并注入到第一条消息
                predefined_content = None
                if request.agent_name:
                    predefined_content = self.current_agent._load_agent_full_content(request.agent_name)

                # 创建子 agent，传递 agent_name 用于 forbidden_agents 过滤
                self.current_agent = self._create_agent(
                    depth=self.current_agent.depth + 1,
                    global_subagent_count=request.global_count,
                    agent_name=request.agent_name or ""
                )

                if predefined_content:
                    # 将流程名加入用户任务首行，避免任务跑偏
                    flow_name = None
                    if request.agent_name:
                        flow_name = self.current_agent._get_agent_flow_name(request.agent_name)
                    # 将预定义内容和用户任务组合（不包含 agent 名称，避免自调用）
                    if flow_name:
                        combined_task = f"{predefined_content}\n\n---\n\n用户任务: Use {flow_name}, {request.task}"
                    else:
                        combined_task = f"{predefined_content}\n\n---\n\n用户任务: {request.task}"
                    self.current_agent.start(combined_task)
                else:
                    # 如果没有预定义内容，按普通任务处理
                    self.current_agent.start(request.task)

            elif result.action == Action.COMPLETE:
                if self.context_stack:
                    parent = self.context_stack.pop()
                    # 需要获取当前 agent 的 global_count 传递给父 agent
                    parent_global_count = self.current_agent._global_subagent_count
                    parent.on_child_completed(result.data or "", parent_global_count)
                    self.current_agent = parent
                else:
                    # 根 agent 完成后，保留 current_agent 状态，等待下一个任务
                    final_outputs = [
                        f"\n{'='*50}\n",
                        f"[任务完成]\n",
                        f"{'='*50}\n",
                        f"{result.data}\n"
                    ]
                    agent_summary = self.current_agent.get_summary()
                    final_outputs.append(f"执行命令: {agent_summary['commands']} | 创建子Agent: {agent_summary['sub_agents']}\n\n")
                    yield (final_outputs, result)
                    self._is_running = False
                    # 不 break，保留 current_agent 供下一个任务使用

            elif result.action == Action.WAIT:
                self._is_running = False
                break

            # CONTINUE 继续循环
