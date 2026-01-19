"""极简 Agent 核心模块 - 统一逻辑，无父子区别"""

import json
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Generator, Optional, Tuple, Any

import requests

from .config import Config


@dataclass
class Message:
    """消息记录"""
    role: str  # system, user, assistant
    content: str
    timestamp: float = field(default_factory=time.time)


class SimpleAgent:
    """极简任务执行 Agent - 统一逻辑，无父子区别
    
    每个Agent的逻辑统一：
    1. 创建子Agent → 聚合结果 → 继续执行或接收 <completion> 结束
    2. 完成后把自己的结果聚合到父上下文
    3. 切换到父上下文，根据当前上下文继续执行
    4. 如果没有父（顶级Agent）就输出最终结果
    """

    def __init__(self, config: Optional[Config] = None,
                 depth: int = 0, max_depth: int = 4):
        """初始化 Agent
        
        Args:
            config: 配置对象
            depth: 当前深度（0=顶级）
            max_depth: 最大允许深度（默认4层）
        """
        self.config = config or Config.from_env()
        self.history: list[Message] = []
        self.start_time = 0.0
        
        # Agent标识
        self.agent_id = str(uuid.uuid4())[:8]
        self.depth = depth
        self.max_depth = max_depth

        # 子Agent管理（追踪状态，因为一次可能创建多个）
        self.children: list[dict] = []  # 每个元素: {"task": str, "status": "running|completed"}
        self.total_sub_agents_created = 0
        self.total_commands_executed = 0
        
        # 初始化系统消息
        self._init_system_prompt()

    def _init_system_prompt(self):
        """初始化系统提示词"""
        max_agents = self.max_depth ** 2
        remaining = max_agents - self.total_sub_agents_created
        
        tree_info = f"""
**当前状态：**
- Agent ID: {self.agent_id}
- 当前深度: {self.depth}
- 最大深度: {self.max_depth}
- 已创建子Agent: {len(self.children)}
- 可用配额: {remaining}（最多{max_agents}个）
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

**2. 子Agent - 任务拆分（推荐）：**
<create_agent> 子任务描述 </create_agent>

**3. 任务完成：**
<completion>
# 完成的工作
- 列出完成的任务

# 产出物
- 列出生成的文件或结果
</completion>

**规则：**
1. 深度优先：先完成一个分支的所有子任务
2. 顺序执行：必须等待子Agent完全完成才能创建下一个
3. 每个子Agent有独立4k上下文，大胆拆分任务
4. 达到最大深度或配额时，直接执行任务
5. 命令执行后会收到结果反馈，根据结果决定下一步
6. 完成所有工作后必须使用 <completion> 标记
7. 用户可以随时输入任务或调整方向
"""
        
        self.history.append(Message(role="system", content=system_prompt))

    def run(self, task: str, is_root: bool = True) -> Generator[str, None, None]:
        """运行任务
        
        Args:
            task: 用户任务描述
            is_root: 是否是顶级Agent（决定最终输出格式）
            
        Yields:
            str: 输出片段
        """
        self.start_time = time.time()
        self._add_message("user", task)

        # 主循环：直到遇到 <completion>
        while True:
            # 调用LLM
            response = self._call_llm()
            yield response
            self._add_message("assistant", response)

            # 解析并执行标签
            has_output = False
            for output in self._parse_and_execute_tools(response):
                has_output = True
                yield output

            # 检查是否完成
            if self._is_completed(response):
                # 顶级Agent输出最终结果
                if is_root:
                    yield "\n" + "="*60 + "\n"
                    yield "[最终结果]\n"
                    summary = self._extract_completion(response)
                    yield summary + "\n"
                    yield f"执行命令: {self.total_commands_executed}\n"
                    yield f"创建子Agent: {self.total_sub_agents_created}\n"
                    yield "="*60 + "\n"
                break

            # 如果没有操作标签也没有输出，等待用户输入
            if not self._has_action_tags(response) and not has_output:
                yield "\n[等待用户输入]\n"
                break

    def _add_message(self, role: str, content: str):
        """添加消息到历史记录"""
        self.history.append(Message(role=role, content=content))

    def _call_llm(self) -> str:
        """调用LLM"""
        messages = [{"role": msg.role, "content": msg.content} for msg in self.history]

        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,  # qwen3 需要流式
            "options": {"num_predict": self.config.max_output_tokens},
        }

        url = f"{self.config.ollama_host}/api/chat"

        try:
            # qwen3 使用流式响应，需要拼接
            reasoning_content = ""
            content = ""

            with requests.post(url, json=payload, timeout=self.config.timeout, stream=True) as response:
                for line in response.iter_lines():
                    if line:
                        data = json.loads(line.decode('utf-8'))
                        # qwen3 格式: message.thinking 或 message.content
                        message = data.get("message", {})
                        reasoning = message.get("thinking", "")
                        content_part = message.get("content", "")
                        if reasoning:
                            reasoning_content += reasoning
                        if content_part:
                            content += content_part

            # qwen3 把思考过程放在 reasoning_content，输出在 content
            # 注意：如果没有 content 就返回空，不应该用 thinking 填补
            result = content if content else ""
            # 调试输出完整响应
            if reasoning_content:
                print(f"\n--- LLM REASONING ---\n{reasoning_content}\n--- END ---\n", file=sys.stderr)
            print(f"\n--- LLM CONTENT ---\n{result}\n--- END ---\n", file=sys.stderr)
            return result
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

    def _parse_and_execute_tools(self, response: str) -> Generator[str, None, None]:
        """解析并执行标签"""
        # 执行PowerShell命令 - 串行执行，一个一个来
        for match in re.finditer(r'<ps_call>\s*(.+?)\s*</ps_call>', response, re.DOTALL):
            command = match.group(1).strip()

            # 预分配编号（执行后确认）
            pending_id = self.total_commands_executed + 1

            # 输出命令，等待用户确认
            yield "<confirm_required>\n"
            yield f"[待执行命令 #{pending_id}]\n"
            yield f"命令: {command}\n"
            yield "<confirm_command_end>\n"

        # 创建并执行子Agent - 自动编号
        for match in re.finditer(r'<create_agent>\s*(.+?)\s*</create_agent>', response, re.DOTALL):
            task = match.group(1).strip()

            # 检查限制
            if self.depth >= self.max_depth:
                # 深度限制：将子任务作为用户消息，让当前Agent自己执行
                self._add_message("user", f"[深度限制] 请直接执行任务: {task}")
                yield f"\n[深度限制] 达到最大深度 {self.max_depth}，由当前Agent执行: {task[:40]}...\n"
                continue  # 重新循环，LLM会响应新的用户消息

            if self.total_sub_agents_created >= self.max_depth ** 2:
                yield f"\n[配额限制] 已用完 {self.max_depth ** 2} 个子Agent配额\n"
                self._add_message("user", f"[配额限制] 请直接执行任务: {task}")
                continue  # 重新循环，LLM会响应新的用户消息
            
            # 创建子Agent（逻辑统一，无父子区别）
            sub_agent = SimpleAgent(
                config=self.config,
                depth=self.depth + 1,
                max_depth=self.max_depth
            )

            self.total_sub_agents_created += 1

            # 追踪子Agent状态
            child_info = {"task": task, "status": "running", "agent_id": sub_agent.agent_id}
            self.children.append(child_info)

            # 执行子Agent
            yield f"\n{'='*60}\n"
            yield f"[子Agent #{self.total_sub_agents_created}] 深度 {sub_agent.depth}/{sub_agent.max_depth}\n"
            yield f"任务: {task[:60]}...\n"
            yield f"{'='*60}\n"

            # 子Agent完成后，聚合结果到父上下文
            sub_outputs = []
            for output in sub_agent.run(task, is_root=False):
                sub_outputs.append(output)
                yield output

            # 聚合子Agent结果 - 只有completion内容才加入父上下文
            sub_summary = self._extract_agent_summary(sub_outputs)
            if sub_summary:  # 只有有内容时才聚合
                self._add_message("user", sub_summary)

            # 更新状态
            child_info["status"] = "completed"
            yield f"\n[子Agent #{self.total_sub_agents_created} 结果已聚合]\n"

    def _extract_agent_summary(self, outputs: list) -> str:
        """提取Agent输出摘要 - 只提取 <completion> 内容"""
        full = "".join(outputs)

        # 只提取completion内容，不包含命令等中间过程
        comp = re.search(r'<completion>\s*(.+?)\s*</completion>', full, re.DOTALL)
        if comp:
            return comp.group(1).strip()
        return ""  # 没有completion就不聚合任何内容

    def _execute_command(self, command: str) -> "CommandResult":
        """执行命令 - 直接用 PowerShell 执行"""
        try:
            import subprocess

            # 直接用 PowerShell 执行，无需前缀
            full_cmd = f'powershell -NoProfile -Command "{command}"'

            process = subprocess.run(
                full_cmd, shell=True, capture_output=True, text=True,
                timeout=self.config.timeout
            )

            return CommandResult(
                command=command,
                stdout=process.stdout,
                stderr=process.stderr,
                returncode=process.returncode
            )
        except subprocess.TimeoutExpired:
            return CommandResult(command=command, stdout="", stderr="超时", returncode=-1)
        except Exception as e:
            return CommandResult(command=command, stdout="", stderr=str(e), returncode=-1)

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
