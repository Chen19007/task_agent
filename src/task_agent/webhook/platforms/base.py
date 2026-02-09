"""
Webhook 平台抽象接口

定义统一的平台接口，支持多种消息平台（飞书、钉钉、企业微信等）
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from enum import Enum


class MessageType(Enum):
    """消息类型枚举"""
    TEXT = "text"
    RICH_TEXT = "rich_text"
    INTERACTIVE = "interactive"
    MARKDOWN = "markdown"


class MessageAction:
    """消息交互动作（按钮等）"""

    def __init__(self, action_id: str, label: str, style: str = "default"):
        self.action_id = action_id
        self.label = label
        self.style = style  # default/primary/danger


class Platform(ABC):
    """
    平台抽象接口

    所有平台插件必须实现此接口，提供统一的消息处理能力
    """

    @abstractmethod
    def verify_signature(
        self, payload: bytes, signature: str, timestamp: str
    ) -> bool:
        """
        验证 webhook 签名

        Args:
            payload: 原始请求体
            signature: 签名
            timestamp: 时间戳

        Returns:
            验证是否通过
        """
        pass

    @abstractmethod
    def parse_incoming_message(self, data: dict) -> Optional[str]:
        """
        解析接收的消息，提取用户任务

        Args:
            data: 平台回调数据

        Returns:
            用户任务内容，如果无法解析返回 None
        """
        pass

    @abstractmethod
    def get_chat_id(self, data: dict) -> Optional[str]:
        """
        从回调数据中提取会话 ID

        Args:
            data: 平台回调数据

        Returns:
            会话 ID，用于后续发送消息
        """
        pass

    @abstractmethod
    def send_message(
        self,
        content: str,
        chat_id: str,
        msg_type: MessageType = MessageType.TEXT,
    ) -> str:
        """
        发送消息到平台

        Args:
            content: 消息内容
            chat_id: 会话 ID
            msg_type: 消息类型

        Returns:
            message_id: 消息ID，用于后续更新
        """
        pass

    @abstractmethod
    def format_output(self, content: str, output_type: str) -> str:
        """
        格式化不同类型的输出

        Args:
            content: 内容
            output_type: 输出类型 (think/content/ps_call/...)

        Returns:
            格式化后的内容
        """
        pass
