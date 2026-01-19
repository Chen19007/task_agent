"""极简 Agent 核心模块 - 统一逻辑，无父子区别"""

import re
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
        
        # 子Agent管理
        self.children: dict[str, dict] = {}
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
**你的工具：**

**1. PowerShell - 执行命令：**
<ps_call id="唯一ID"> powershell "命令" </ps_call>

**2. 子Agent - 任务拆分：**
<create_agent id="唯一ID"> 子任务描述 </create_agent>

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
3. 如果没有输出任何工具标签，系统会自动附加"continue"
4. 达到最大深度或配额时，直接执行任务，不得创建子Agent
5. 超过16个子Agent说明任务拆分不合理
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
            "stream": False,
            "options": {"num_predict": self.config.max_output_tokens},
        }
        
        url = f"{self.config.ollama_host}/api/chat"
        
        try:
            response = requests.post(url, json=payload, timeout=self.config.timeout)
            data = response.json()
            return data.get("message", {}).get("content", "")
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
        # 执行PowerShell命令
        for match in re.finditer(r'<ps_call\s+id="([^"]+)">\s*(.+?)\s*</ps_call>', response, re.DOTALL):
            call_id, command = match.group(1), match.group(2).strip()
            result = self._execute_command(command)
            self.total_commands_executed += 1
            
            output = f"\n[命令 #{self.total_commands_executed}]\n"
            output += f"命令: {command}\n"
            output += f"结果: {result.stdout if result.stdout else result.stderr}\n"
            yield output
            
            self._add_message("user", f'<ps_call_result id="{call_id}">\n{output}\n</ps_call_result>')

        # 创建并执行子Agent
        for match in re.finditer(r'<create_agent\s+id="([^"]+)">\s*(.+?)\s*</create_agent>', response, re.DOTALL):
            agent_id, task = match.group(1), match.group(2).strip()
            
            # 检查限制
            if self.depth >= self.max_depth:
                yield f"\n[深度限制] 已达最大深度 {self.max_depth}，直接执行任务\n"
                continue
            
            if self.total_sub_agents_created >= self.max_depth ** 2:
                yield f"\n[配额限制] 已用完 {self.max_depth ** 2} 个子Agent配额\n"
                continue
            
            # 创建子Agent（逻辑统一，无父子区别）
            sub_agent = SimpleAgent(
                config=self.config,
                depth=self.depth + 1,
                max_depth=self.max_depth
            )
            
            self.children[sub_agent.agent_id] = {"task": task, "status": "running"}
            self.total_sub_agents_created += 1
            
            # 执行子Agent
            yield f"\n{'='*60}\n"
            yield f"[子Agent {sub_agent.agent_id}] 深度 {sub_agent.depth}/{sub_agent.max_depth}\n"
            yield f"任务: {task[:60]}...\n"
            yield f"{'='*60}\n"
            
            # 子Agent完成后，聚合结果到父上下文
            sub_outputs = []
            for output in sub_agent.run(task, is_root=False):
                sub_outputs.append(output)
                yield output
            
            # 聚合子Agent结果
            sub_summary = self._extract_agent_summary(sub_outputs)
            self._add_message("user", f"[子Agent {sub_agent.agent_id} 完成]\n{sub_summary}")
            
            yield f"\n[子Agent {sub_agent.agent_id} 结果已聚合]\n"
            
            if sub_agent.agent_id in self.children:
                self.children[sub_agent.agent_id]["status"] = "completed"

    def _extract_agent_summary(self, outputs: list) -> str:
        """提取Agent输出摘要"""
        full = "".join(outputs)
        
        # 提取completion内容
        comp = re.search(r'<completion>\s*(.+?)\s*</completion>', full, re.DOTALL)
        completion = comp.group(1) if comp else "任务完成"
        
        # 提取命令
        cmds = re.findall(r'<ps_call[^>]*>\s*(.+?)\s*</ps_call>', full, re.DOTALL)
        
        return f"完成: {completion}\n命令数: {len(cmds)}"

    def _execute_command(self, command: str) -> "CommandResult":
        """执行命令"""
        try:
            import subprocess
            
            # PowerShell命令处理
            if command.startswith("powershell") or command.startswith("pwsh"):
                cmd = command.replace('powershell', '').replace('pwsh', '').strip().strip('"').strip("'")
                full_cmd = f'powershell -NoProfile -Command "{cmd}"'
            else:
                full_cmd = command
            
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
