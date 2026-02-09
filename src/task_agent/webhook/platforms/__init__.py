"""
平台模块

导出所有平台实现
"""

from .base import Platform, MessageType, MessageAction
from .feishu import FeishuPlatform

__all__ = ["Platform", "MessageType", "MessageAction", "FeishuPlatform"]
