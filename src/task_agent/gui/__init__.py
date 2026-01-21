"""Task-Agent GUI 模块

使用 Dear PyGui 实现的图形界面，提供类似 Cursor/Cline 的对话体验。
"""

__version__ = "0.1.0"

from .adapter import ExecutorAdapter
from .app import main
from .async_executor import AsyncExecutor
from .chat_panel import ChatPanel
from .gui_output import GUIOutput
from .session_list import SessionList
from .themes import ThemeColors

__all__ = [
    "main",
    "ExecutorAdapter",
    "AsyncExecutor",
    "ChatPanel",
    "SessionList",
    "ThemeColors",
    "GUIOutput",
]
