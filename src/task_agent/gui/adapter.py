"""Executor 适配器

将现有 Executor 封装为 GUI 可用形式，不修改核心代码。
"""

from typing import Generator, Optional

from ..agent import Executor, StepResult
from ..config import Config
from ..output_handler import OutputHandler
from ..session import SessionManager


class ExecutorAdapter:
    """Executor 适配器

    封装现有 Executor，提供 GUI 友好的接口。
    不修改核心代码，通过适配层对接。
    """

    def __init__(self, config: Optional[Config] = None, output_handler: Optional[OutputHandler] = None):
        """初始化适配器

        Args:
            config: 配置对象，如果为 None 则从环境变量加载
            output_handler: 输出处理器（可选）
        """
        self.config = config or Config.from_env()
        self.session_manager = SessionManager()
        self.output_handler = output_handler
        self.executor = Executor(
            self.config,
            session_manager=self.session_manager,
            output_handler=output_handler
        )

    def execute_task(self, task: str) -> Generator[tuple[list[str], StepResult], None, None]:
        """执行任务

        Args:
            task: 任务描述

        Yields:
            (输出列表, StepResult): 与 Executor.run() 相同的格式
        """
        yield from self.executor.run(task)

    def resume(self, user_input: str) -> Generator[tuple[list[str], StepResult], None, None]:
        """恢复执行（用户输入后）

        Args:
            user_input: 用户输入内容

        Yields:
            (输出列表, StepResult): 与 Executor.resume() 相同的格式
        """
        yield from self.executor.resume(user_input)

    def get_current_session_id(self) -> Optional[int]:
        """获取当前会话 ID"""
        return self.session_manager.current_session_id

    def list_sessions(self) -> list:
        """列出所有会话"""
        return self.session_manager.list_sessions()

    def load_session(self, session_id: int):
        """加载会话"""
        new_executor = self.session_manager.load_session(
            session_id,
            self.config,
            output_handler=self.output_handler
        )
        if new_executor:
            self.executor = new_executor
        return new_executor is not None

    def create_new_session(self):
        """创建新会话"""
        new_id, new_executor = self.session_manager.create_new_session(self.executor)
        self.executor = new_executor
        return new_id

    def get_current_agent_history(self) -> list:
        """获取当前 agent 的历史消息"""
        if self.executor.current_agent:
            return [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp
                }
                for msg in self.executor.current_agent.history
            ]
        return []
