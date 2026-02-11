"""
Webhook 适配器

复用 Executor 适配器模式，提供 Webhook 场景的接口
"""

import logging
import os
from typing import Generator, Optional

from ..agent import Executor, StepResult
from ..config import Config
from ..output_handler import OutputHandler
from ..session import SessionManager
from .platforms.base import Platform

logger = logging.getLogger(__name__)


class WebhookAdapter:
    """
    Webhook 适配器

    封装 Executor，提供 Webhook 场景的接口
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        platform: Optional[Platform] = None,
        chat_id: Optional[str] = None,
    ):
        """
        初始化适配器

        Args:
            config: 配置对象
            platform: 平台实例（可选，用于创建输出处理器）
            chat_id: 会话 ID
        """
        self.config = config or Config.from_env()
        self.session_manager = SessionManager()
        self.platform = platform
        self.chat_id = chat_id

        # 创建输出处理器
        self.output_handler: Optional[OutputHandler] = None
        if platform and chat_id:
            from .output import WebhookOutput

            self.output_handler = WebhookOutput(platform, chat_id)

        # 创建执行器
        self.executor = Executor(
            self.config,
            max_depth=2,  # webhook 场景限制深度，便于在移动端控制
            session_manager=self.session_manager,
            output_handler=self.output_handler,
            runtime_scene="webhook",
            workspace_dir=os.getcwd(),
        )
        self._ensure_session_id()

    def _ensure_session_id(self) -> None:
        """确保会话管理器已有 session_id，避免快照丢失。"""
        if self.session_manager.current_session_id is None:
            self.session_manager.current_session_id = self.session_manager.get_next_session_id()

    def execute_task(self, task: str) -> Generator[tuple[list[str], StepResult], None, None]:
        """
        执行任务

        Args:
            task: 任务描述

        Yields:
            (输出列表, StepResult)
        """
        self._ensure_session_id()
        yield from self.executor.run(task)

    def set_output_handler(self, output_handler: Optional[OutputHandler]) -> None:
        """设置并同步输出处理器到 Executor/Agent。"""
        self.output_handler = output_handler
        if output_handler is None:
            return

        # Executor 持有自己的 _output_handler，需要同步更新
        self.executor._output_handler = output_handler

        # 已创建的 Agent 也持有独立处理器引用，需要一并更新
        if self.executor.current_agent is not None:
            self.executor.current_agent._output_handler = output_handler
        for agent in self.executor.context_stack:
            agent._output_handler = output_handler

    def resume(self, user_input: str) -> Generator[tuple[list[str], StepResult], None, None]:
        """
        恢复执行

        Args:
            user_input: 用户输入

        Yields:
            (输出列表, StepResult)
        """
        self._ensure_session_id()
        yield from self.executor.resume(user_input)

    def get_current_session_id(self) -> Optional[int]:
        """获取当前会话 ID"""
        return self.session_manager.current_session_id

    def list_sessions(self) -> list:
        """列出所有会话"""
        return self.session_manager.list_sessions()

    def load_session(self, session_id: int) -> bool:
        """加载会话"""
        new_executor = self.session_manager.load_session(
            session_id, self.config, runtime_scene="webhook", output_handler=self.output_handler
        )
        if new_executor:
            self.executor = new_executor
        return new_executor is not None

    def create_new_session(self) -> int:
        """创建新会话"""
        new_id, new_executor = self.session_manager.create_new_session(self.executor)
        self.executor = new_executor
        return new_id

    def send_output_to_platform(self) -> None:
        """将缓存的输出发送到平台"""
        if self.output_handler and hasattr(self.output_handler, "flush"):
            from .output import WebhookOutput

            assert isinstance(self.output_handler, WebhookOutput)
            contents = self.output_handler.flush()

            # 添加调试日志
            logger.info(f"[DEBUG] flush() 返回 {len(contents)} 条输出")
            if contents:
                logger.info(f"[DEBUG] 内容预览: {contents[0][:100]}...")

            # 合并内容发送
            if contents and self.platform and self.chat_id:
                combined = "\n".join(contents)
                result = self.platform.send_message(combined, self.chat_id)

                # 检查发送结果
                if result:
                    logger.info(f"✓ 飞书消息发送成功: {result}")
                else:
                    logger.error("✗ 飞书消息发送失败（返回空）")
            else:
                if not contents:
                    logger.warning("跳过发送：flush() 返回空列表")
                if not self.platform:
                    logger.error("跳过发送：platform 为 None")
                if not self.chat_id:
                    logger.error("跳过发送：chat_id 为 None")
