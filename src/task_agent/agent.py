"""极简 Agent 核心模块 - 上下文切换架构"""

import json
import re
import sys
import time
import uuid
import os
from dataclasses import dataclass, field
from typing import Generator, Optional, Any
from enum import Enum

from .config import Config
from .llm import create_client


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
                 agent_name: Optional[str] = None):
        """初始化 Agent

        Args:
            config: 配置对象
            depth: 当前深度（0=顶级）
            max_depth: 最大允许深度（默认4层）
            global_subagent_count: 全局已使用的子Agent数量（累加计数）
            agent_name: 当前 agent 名称（用于过滤 forbidden_agents）
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

        # 初始化系统消息
        self._init_system_prompt()

        # 待执行的子Agent任务请求（step()返回时携带）
        self._pending_child_request: Optional[ChildTaskRequest] = None

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

        # 基础系统提示词（所有Agent共有的核心规则）
        base_system_prompt = f"""你是一个任务执行agent，负责完成用户任务。

{tree_info}
{predefined_section}
**重要说明：**
- 每个 Agent 都有独立的 4k token 上下文窗口
- 子 Agent 不会消耗父 Agent 的上下文
- 鼓励通过创建子 Agent 来拆分复杂任务，充分利用独立上下文

**你的工具：**

**1. 命令执行（PowerShell）：

**1. 命令执行（PowerShell）：**
<ps_call> 直接写PowerShell命令 </ps_call>

**重要约束：只使用非交互式命令**
- ❌ 禁止：Read-Host、交互式确认、等待中途输入的命令
- ✅ 正确：需要用户信息时，先在对话中询问，再生成命令

示例对比：
❌ 错误：<ps_call> Read-Host "请输入姓名" </ps_call>  （会卡住等待输入）
✅ 正确：先问用户"请提供姓名"，再生成 <ps_call> Add-Content -Path name.txt -Value "张三" -Encoding UTF8 </ps_call>

✅ 合法示例：
<ps_call> Get-ChildItem </ps_call>
<ps_call> Get-Process | Where-Object {{$_.CPU -gt 10}} </ps_call>

**2. 子Agent - 任务拆分（推荐）：**

**普通子任务：**
<create_agent> 子任务描述 </create_agent>

**预定义子任务（如果有对应 agent）：**
<create_agent name=agent_name> 任务描述 </create_agent>

**说明：**
- 每个子Agent是独立的任务分支，有独立的上下文窗口
- 整个任务最多创建 32 个子任务（全局配额），请合理分配
- 子Agent完成后会返回摘要，供当前Agent继续决策
- 预定义子Agent会加载详细的工作流程指令，适合复杂标准化任务

**3. 提问等待用户输入：**

当需要用户提供信息才能继续时，直接提问即可。

✅ 正确用法：
- 需要知道文件名时："请问您要创建的文件名是什么？"
- 需要知道用户偏好时："您希望使用哪种编程语言（Python/JavaScript/Go）？"
- 需要确认方案时："方案A侧重性能，方案B侧重开发速度，您倾向哪个？"

⚠️ **注意**：提问后 Agent 会暂停，等待用户输入后自动继续执行。

❌ 错误做法：
- 提问后紧接着输出 `<completion>` - 这是矛盾的，提问意味着未完成
- 使用 `<ps_call> Read-Host "xxx" </ps_call>` - 这是交互式命令，会卡住

**4. 任务完成标记 <completion>（重要）：**

<completion> 任务总结内容 </completion>

【上下文传递机制】
- ⚠️ **只有 <completion> 标签内的内容会被传递给父Agent或下一个任务**
- 中间的对话、命令输出、提问等内容不会被传递
- 父Agent只会看到你的 completion 内容，然后根据它决定下一步

【关键约束】
- ❌ **禁止在任务未完成时使用 <completion>**
- ❌ **有疑问或需要用户确认时，禁止使用 <completion>** - 应该直接提问并等待用户回复
- ✅ 只有在以下情况才能使用：
  1. 所有子Agent都已返回结果
  2. 所有计划的工作都已执行完毕
  3. 没有待处理的命令或待创建的子Agent
  4. 确定任务已完成，不需要进一步用户输入
- ⚠️ 一旦输出 <completion>，Agent将立即停止，无法继续执行

【正确用法示例】
✅ 完成撰写两个文档后：
<completion>
# 完成的工作
- 撰写游戏概念章节
- 撰写核心玩法章节

# 产出物
- game_concept.md
- core_mechanics.md
</completion>

❌ 错误用法（任务未完成）：
<completion>
# 准备开始工作
# 产出物：无
</completion>

**规则：**
1. 子Agent之间互相不知道对方的结果，每个子Agent都是独立执行
   - **无依赖的子Task**：可以同时创建（一起创建多个子Agent）
   - **有依赖的子Task**：必须分开创建（先创建A，等待A完成后，再创建依赖A结果的B）
2. 深度优先遍历：先完成一个分支的所有子任务，创建子Agent后必须等待其完全完成才能创建下一个（你是深度优先遍历的 Agent）
3. 每个子Agent有独立4k上下文，合理拆分任务
4. 整个任务最多使用 32 个子任务（全局配额），每个Agent最多使用 16 个（本地配额）
5. 命令执行后会收到结果反馈，根据结果决定下一步
6. 完成所有工作后必须使用 `<completion>` 标记
7. 用户可以随时输入任务或调整方向

**【全局配额用完时的处理】**

当收到 `[全局配额限制]` 消息时：
- **禁止**输出解释、闲聊、道歉或询问用户
- **必须**立即使用 `<ps_call>` 执行任务
- 任务完成后**必须**使用 `<completion>` 标记结束

❌ 错误示例：
```
我理解您的需求，但由于配额限制，我将直接执行任务...
请问您需要我做什么？
```

✅ 正确示例：
```
<ps_call> Get-Content -Path "文件路径" -Encoding UTF8 </ps_call>
<completion>
任务完成
</completion>
```
"""

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
        """
        # 调用LLM
        content, reasoning = self._call_llm()

        # 组合输出：reasoning（如果有） + content
        # reasoning 不参与标签解析，只做辅助参考
        output_for_display = reasoning + content if reasoning else content
        response = content  # 只有 content 参与标签解析

        self._add_message("assistant", response)

        # 格式化输出
        outputs = []
        if reasoning and reasoning.strip():
            outputs.append(f"\n** 思考过程：\n")
            outputs.append(f"{'━'*50}\n")
            outputs.append(f"{reasoning}\n")
            outputs.append(f"{'━'*50}\n\n")
        if response and response.strip():
            outputs.append(response)

        # 解析并执行标签，收集输出和命令
        tool_outputs, pending_commands, command_blocks = self._parse_tools(response)
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
                self._add_message("user", f"[深度限制] 请直接执行任务: {task}")
                outputs.append(f"\n!! [深度限制]\n")
                outputs.append(f"已达到最大深度 {self.max_depth}，由当前Agent执行\n")
                outputs.append(f"{'═'*50}\n\n")
                return StepResult(outputs=self._add_depth_prefix(outputs), action=Action.CONTINUE, pending_commands=pending_commands, command_blocks=command_blocks)

            # 检查本地配额限制
            if self.total_sub_agents_created >= self.max_depth ** 2:
                outputs.append(f"\n!! [本地配额限制]\n")
                outputs.append(f"当前Agent已用完 {self.max_depth ** 2} 个子Agent配额\n")
                outputs.append(f"{'═'*50}\n\n")
                self._add_message("user", f"[本地配额限制] 请直接执行任务: {task}")
                return StepResult(outputs=self._add_depth_prefix(outputs), action=Action.CONTINUE, pending_commands=pending_commands, command_blocks=command_blocks)

            # 检查全局配额限制（防止层级间循环）- 累加计数
            global_total = self.max_depth ** 2 * 2
            if self._global_subagent_count >= global_total:
                outputs.append(f"\n!! [全局配额限制]\n")
                outputs.append(f"整个任务已用完所有 {global_total} 个子Agent配额\n")
                outputs.append(f"{'═'*50}\n\n")
                self._add_message("user", f"[全局配额限制] 请直接执行任务: {task}")
                return StepResult(outputs=self._add_depth_prefix(outputs), action=Action.CONTINUE, pending_commands=pending_commands, command_blocks=command_blocks)

            self.total_sub_agents_created += 1

            # 获取元数据用于显示
            context_used = self._estimate_context_tokens()
            context_total = self.config.num_ctx

            outputs.append(f"\n{'+'*60}\n")
            agent_info = f" [{agent_name}]" if agent_name else ""
            outputs.append(f"深度: {self.depth + 1}/{self.max_depth}{agent_info} | 任务: {task}\n")
            outputs.append(f"上下文: {context_used}/{context_total} | 配额: {new_global_count}/{global_total}\n")
            outputs.append(f"{'+'*60}\n\n")

            # 返回 ChildTaskRequest 对象
            return StepResult(outputs=self._add_depth_prefix(outputs), action=Action.SWITCH_TO_CHILD, data=request, pending_commands=pending_commands, command_blocks=command_blocks)

        # 检查是否完成
        if self._is_completed(response):
            summary = self._extract_completion(response)
            return StepResult(outputs=self._add_depth_prefix(outputs), action=Action.COMPLETE, data=summary, pending_commands=pending_commands, command_blocks=command_blocks)

        # 检查是否需要等待用户输入
        # 只有当没有任何标签时才等待（reasoning 不影响）
        if not self._has_action_tags(response) and not tool_outputs:
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
            self._add_message("user", summary)
        # 同步全局计数
        self._global_subagent_count = global_count

    def _add_message(self, role: str, content: str):
        """添加消息到历史记录"""
        self.history.append(Message(role=role, content=content))

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

    def _call_llm(self) -> tuple[str, str]:
        """调用LLM，返回 (content, reasoning)"""
        messages = [{"role": msg.role, "content": msg.content} for msg in self.history]

        # 使用 LLM 客户端
        client = create_client(self.config)

        try:
            response = client.chat(messages, self.config.max_output_tokens)
            return response.content, response.reasoning
        except Exception as e:
            raise RuntimeError(f"调用LLM失败: {e}")

    def _has_action_tags(self, response: str) -> bool:
        """检查是否有操作标签"""
        return bool(re.search(r'<(ps_call|create_agent|completion)\b', response, re.IGNORECASE))

    def _is_completed(self, response: str) -> bool:
        """检查是否完成"""
        return bool(re.search(r'<completion\b', response, re.IGNORECASE))

    def _extract_completion(self, response: str) -> str:
        """提取完成内容"""
        match = re.search(r'<completion>\s*(.+?)\s*</completion>', response, re.DOTALL)
        return match.group(1) if match else "任务完成"

    def _parse_tools(self, response: str) -> tuple[list[str], list[str], list[str]]:
        """解析工具标签

        Returns:
            (outputs, commands, command_blocks): 输出内容列表、待执行命令列表、命令框显示内容
        """
        outputs = []
        commands = []
        command_blocks = []

        # 执行PowerShell命令
        for match in re.finditer(r'<ps_call>\s*(.+?)\s*</ps_call>', response, re.DOTALL):
            command = match.group(1).strip()
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

    def __init__(self, config: Optional[Config] = None, max_depth: int = 4):
        """初始化执行器

        Args:
            config: 配置对象
            max_depth: 最大深度
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

        # 自动同意：当前目录安全文件操作自动执行
        self.auto_approve: bool = False

    def run(self, task: str) -> Generator[tuple[list[str], StepResult], None, None]:
        """运行任务

        Args:
            task: 用户任务描述

        Yields:
            tuple[list[str], StepResult]: (输出片段列表, 执行结果)
        """
        # 创建顶级Agent（如果不存在）
        if not self.current_agent:
            self.current_agent = SimpleAgent(
                config=self.config,
                depth=0,
                max_depth=self.max_depth,
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
            result = self.current_agent.step()

            # 保存 StepResult 供 cli.py 访问
            self.last_step_result = result

            # 一次性返回所有输出和结果，让 cli.py 先显示完输出再处理命令
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
                self.current_agent = SimpleAgent(
                    config=self.config,
                    depth=self.current_agent.depth + 1,
                    max_depth=self.max_depth,
                    global_subagent_count=request.global_count,
                    agent_name=request.agent_name or ""
                )

                if predefined_content:
                    # 将预定义内容和用户任务组合（不包含 agent 名称，避免自调用）
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
