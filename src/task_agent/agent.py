"""极简 Agent 核心模块 - 上下文切换架构"""

import re
import sys
import time
import uuid
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
    outputs: list[str]  # 输出内容列表
    action: Action  # 下一步动作
    data: Any = None  # 携带数据（切换时的任务/摘要）


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
                 global_subagent_count: int = 0):
        """初始化 Agent

        Args:
            config: 配置对象
            depth: 当前深度（0=顶级）
            max_depth: 最大允许深度（默认4层）
            global_subagent_count: 全局已使用的子Agent数量（累加计数）
        """
        self.config = config or Config.from_env()
        self.history: list[Message] = []
        self.start_time = 0.0

        # Agent标识
        self.agent_id = str(uuid.uuid4())[:8]
        self.depth = depth
        self.max_depth = max_depth

        # 统计
        self.total_sub_agents_created = 0
        self.total_commands_executed = 0

        # 全局配额（累加计数，而不是递减剩余量）
        self._global_subagent_count = global_subagent_count

        # 初始化系统消息
        self._init_system_prompt()

        # 待执行的子Agent任务（step()返回时携带）
        self._pending_child_task: Optional[str] = None

    def _init_system_prompt(self):
        """初始化系统提示词"""
        max_agents = self.max_depth ** 2
        local_remaining = max_agents - self.total_sub_agents_created

        # 全局配额（累加计数）
        global_total = max_agents * 2  # 全局配额是单Agent的2倍
        global_used = self._global_subagent_count

        # 估算当前上下文使用量
        context_used = self._estimate_context_tokens()
        context_total = self.config.max_output_tokens * 4  # 假设总窗口是输出的4倍
        context_remaining = context_total - context_used
        context_percent = (context_used / context_total) * 100

        tree_info = f"""
**当前状态：**
- Agent ID: {self.agent_id}
- 当前深度: {self.depth}
- 最大深度: {self.max_depth}
- 已创建子Agent: {self.total_sub_agents_created}
- 本地配额: {local_remaining}/{max_agents}（当前Agent还能创建 {local_remaining} 个子任务）
- 全局配额: {global_used}/{global_total}（整个任务累计已使用 {global_used} 个子任务，总计 {global_total} 个）
- 上下文使用: {context_used}/{context_total} tokens ({context_percent:.1f}%)
- 剩余可用: {context_remaining} tokens
"""

        system_prompt = """你是一个任务执行agent，负责完成用户任务。

""" + tree_info + """
**重要说明：**
- 每个 Agent 都有独立的 4k token 上下文窗口
- 子 Agent 不会消耗父 Agent 的上下文
- 鼓励通过创建子 Agent 来拆分复杂任务，充分利用独立上下文

**你的工具：**

**1. 命令执行（PowerShell）：**
<ps_call> 直接写PowerShell命令 </ps_call>

**重要约束：只使用非交互式命令**
- ❌ 禁止：Read-Host、交互式确认、等待中途输入的命令
- ✅ 正确：需要用户信息时，先在对话中询问，再生成命令

示例对比：
❌ 错误：<ps_call> Read-Host "请输入姓名" </ps_call>  （会卡住等待输入）
✅ 正确：先问用户"请提供姓名"，再生成 <ps_call> Set-Content -Path name.txt -Value "张三" </ps_call>

✅ 合法示例：
<ps_call> Get-ChildItem </ps_call>
<ps_call> Set-Content -Path test.txt -Value "hello" </ps_call>
<ps_call> Get-Process | Where-Object {$_.CPU -gt 10} </ps_call>

**【常用文件操作命令速查】**

**读取文件内容：**
<ps_call> Get-Content -Path "文件路径" -Encoding UTF8 </ps_call>
<ps_call> Get-Content -Path "文件路径" -Raw -Encoding UTF8 </ps_call>  # 一次性读取全部（推荐大文件）

**创建/覆盖文件：**
<ps_call> Set-Content -Path "文件路径" -Value "内容" -Encoding UTF8 </ps_call>

**追加内容到文件末尾：**
<ps_call> Add-Content -Path "文件路径" -Value "追加内容" -Encoding UTF8 </ps_call>

**在指定位置插入内容：**
```powershell
$content = Get-Content -Path "文件路径" -Encoding UTF8
$lines = $content -split "`n"
$lines.Insert(行号, "新行内容") | Set-Content -Path "文件路径" -Encoding UTF8
```
例如在第 5 行后插入：
<ps_call> $lines = Get-Content -Path "file.txt" -Encoding UTF8; $lines.Insert(5, "新行内容") | Set-Content -Path "file.txt" -Encoding UTF8 </ps_call>

**替换文件中的特定内容：**
<ps_call> (Get-Content -Path "文件路径" -Encoding UTF8) -replace "旧内容", "新内容" | Set-Content -Path "文件路径" -Encoding UTF8 </ps_call>

**删除文件：**
<ps_call> Remove-Item -Path "文件路径" -Force </ps_call>

**创建目录：**
<ps_call> New-Item -Path "目录路径" -ItemType Directory -Force </ps_call>

**查看文件是否存在：**
<ps_call> Test-Path -Path "文件路径" </ps_call>

**获取文件大小/信息：**
<ps_call> Get-Item "文件路径" | Select-Object Name, Length, LastWriteTime </ps_call>

**查看文件前 N 行：**
<ps_call> Get-Content -Path "文件路径" -TotalCount 10 -Encoding UTF8 </ps_call>

**查看文件后 N 行：**
<ps_call> Get-Content -Path "文件路径" -Tail 10 -Encoding UTF8 </ps_call>

**搜索文件内容：**
<ps_call> Select-String -Path "文件路径" -Pattern "搜索内容" </ps_call>

⚠️ **重要提示**：
- 大文件用 `-Raw` 一次性读取，不要分块读取再拼接
- 修改文件前可以用 `Test-Path` 检查是否存在
- 路径用双引号括起来，包含空格也没问题

**2. 子Agent - 任务拆分（推荐）：**
<create_agent> 子任务描述 </create_agent>

**说明：**
- 每个子Agent是独立的任务分支，有独立的上下文窗口
- 整个任务最多创建 32 个子任务（全局配额），请合理分配
- 子Agent完成后会返回摘要，供当前Agent继续决策

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
1. 深度优先遍历：先完成一个分支的所有子任务，创建子Agent后必须等待其完全完成才能创建下一个（你是深度优先遍历的 Agent）
2. 每个子Agent有独立4k上下文，合理拆分任务
3. 整个任务最多使用 32 个子任务（全局配额），每个Agent最多使用 16 个（本地配额）
4. 命令执行后会收到结果反馈，根据结果决定下一步
5. 完成所有工作后必须使用 <completion> 标记
6. 用户可以随时输入任务或调整方向
"""

        self.history.append(Message(role="system", content=system_prompt))

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

        outputs = []
        if reasoning:
            outputs.append(f"[思考: {reasoning}]\n")
        if response:
            outputs.append(response)

        # 解析并执行标签，收集输出
        tool_outputs = list(self._parse_tools(response))
        outputs.extend(tool_outputs)

        # 检查是否需要切换到子Agent
        if self._pending_child_task:
            task = self._pending_child_task
            self._pending_child_task = None

            # 检查深度限制
            if self.depth >= self.max_depth:
                self._add_message("user", f"[深度限制] 请直接执行任务: {task}")
                outputs.append(f"\n[深度限制] 达到最大深度 {self.max_depth}，由当前Agent执行: {task[:40]}...\n")
                return StepResult(outputs=outputs, action=Action.CONTINUE)

            # 检查本地配额限制
            if self.total_sub_agents_created >= self.max_depth ** 2:
                outputs.append(f"\n[本地配额限制] 当前Agent已用完 {self.max_depth ** 2} 个子Agent配额\n")
                self._add_message("user", f"[本地配额限制] 请直接执行任务: {task}")
                return StepResult(outputs=outputs, action=Action.CONTINUE)

            # 检查全局配额限制（防止层级间循环）- 累加计数
            global_total = self.max_depth ** 2 * 2
            if self._global_subagent_count >= global_total:
                outputs.append(f"\n[全局配额限制] 整个任务已用完所有 {global_total} 个子Agent配额，请直接执行任务\n")
                self._add_message("user", f"[全局配额限制] 请直接执行任务: {task}")
                return StepResult(outputs=outputs, action=Action.CONTINUE)

            self.total_sub_agents_created += 1

            # 累加全局计数并传递给子Agent
            new_global_count = self._global_subagent_count + 1

            # 获取元数据用于显示
            context_used = self._estimate_context_tokens()
            context_total = self.config.max_output_tokens * 4

            outputs.append(f"\n{'='*60}\n")
            outputs.append(f"[子Agent #{self.total_sub_agents_created}] 深度 {self.depth + 1}/{self.max_depth}\n")
            outputs.append(f"任务: {task[:60]}...\n")
            outputs.append(f"上下文: {context_used}/{context_total} | 全局配额: {new_global_count}/{global_total}\n")
            outputs.append(f"{'='*60}\n")

            return StepResult(outputs=outputs, action=Action.SWITCH_TO_CHILD, data=(task, new_global_count))

        # 检查是否完成
        if self._is_completed(response):
            summary = self._extract_completion(response)
            return StepResult(outputs=outputs, action=Action.COMPLETE, data=summary)

        # 检查是否需要等待用户输入
        # 只有当没有标签、没有工具输出、也没有 reasoning 时才等待
        if not self._has_action_tags(response) and not tool_outputs and not reasoning:
            outputs.append("\n[等待用户输入]\n")
            return StepResult(outputs=outputs, action=Action.WAIT)

        return StepResult(outputs=outputs, action=Action.CONTINUE)

    def on_child_completed(self, summary: str, global_count: int):
        """子Agent完成时的回调

        Args:
            summary: 子Agent的完成摘要
            global_count: 同步全局子Agent计数
        """
        if summary:
            self._add_message("user", summary)
        # 同步全局计数
        self._global_subagent_count = global_count

    def _add_message(self, role: str, content: str):
        """添加消息到历史记录"""
        self.history.append(Message(role=role, content=content))

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

        content = ""
        reasoning = ""

        try:
            for chunk in client.chat(messages, self.config.max_output_tokens):
                content += chunk.content
                reasoning += chunk.reasoning

            # 调试输出
            if reasoning:
                print(f"\n--- LLM REASONING ---\n{reasoning}\n--- END ---\n", file=sys.stderr)
            print(f"\n--- LLM CONTENT ---\n{content}\n--- END ---\n", file=sys.stderr)

            return content, reasoning
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

    def _parse_tools(self, response: str) -> Generator[str, None, None]:
        """解析工具标签"""
        # 执行PowerShell命令
        for match in re.finditer(r'<ps_call>\s*(.+?)\s*</ps_call>', response, re.DOTALL):
            command = match.group(1).strip()
            self.total_commands_executed += 1  # 解析时分配编号

            yield "<confirm_required>\n"
            yield f"[待执行命令 #{self.total_commands_executed}]\n"
            yield f"命令: {command}\n"
            yield "<confirm_command_end>\n"

        # 创建子Agent（只设置第一个，等待切换）
        for match in re.finditer(r'<create_agent>\s*(.+?)\s*</create_agent>', response, re.DOTALL):
            if not self._pending_child_task:  # 只处理第一个
                self._pending_child_task = match.group(1).strip()
                break

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

    def run(self, task: str) -> Generator[str, None, None]:
        """运行任务

        Args:
            task: 用户任务描述

        Yields:
            str: 输出片段
        """
        # 创建顶级Agent
        if not self.current_agent:
            self.current_agent = SimpleAgent(
                config=self.config,
                depth=0,
                max_depth=self.max_depth,
                global_subagent_count=self._global_subagent_count
            )
            self.current_agent.start(task)

        self._is_running = True
        yield from self._execute_loop()

    def resume(self, user_input: str) -> Generator[str, None, None]:
        """恢复执行（用户输入后）

        Args:
            user_input: 用户输入内容

        Yields:
            str: 输出片段
        """
        if not self.current_agent:
            return

        self.current_agent._add_message("user", user_input)
        self._is_running = True
        yield from self._execute_loop()

    def _execute_loop(self) -> Generator[str, None, None]:
        """执行循环（内部方法）

        Yields:
            str: 输出片段
        """
        while self.current_agent and self._is_running:
            result = self.current_agent.step()

            for output in result.outputs:
                yield output

            if result.action == Action.SWITCH_TO_CHILD:
                child_task, new_global_count = result.data
                self.context_stack.append(self.current_agent)
                self.current_agent = SimpleAgent(
                    config=self.config,
                    depth=self.current_agent.depth + 1,
                    max_depth=self.max_depth,
                    global_subagent_count=new_global_count
                )
                self.current_agent.start(child_task)

            elif result.action == Action.COMPLETE:
                if self.context_stack:
                    parent = self.context_stack.pop()
                    # 需要获取当前 agent 的 global_count 传递给父 agent
                    parent_global_count = self.current_agent._global_subagent_count
                    parent.on_child_completed(result.data or "", parent_global_count)
                    self.current_agent = parent
                else:
                    yield "\n" + "="*60 + "\n"
                    yield "[最终结果]\n"
                    yield result.data + "\n"
                    agent_summary = self.current_agent.get_summary()
                    yield f"执行命令: {agent_summary['commands']}\n"
                    yield f"创建子Agent: {agent_summary['sub_agents']}\n"
                    yield "="*60 + "\n"
                    self._is_running = False
                    break

            elif result.action == Action.WAIT:
                self._is_running = False
                break

            # CONTINUE 继续循环
