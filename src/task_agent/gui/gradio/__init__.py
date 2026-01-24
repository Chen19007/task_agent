"""Gradio GUI 模块

提供 Gradio Web 界面的输出处理和执行器。
"""

from .gradio_output import GradioOutput
from .gradio_executor import GradioExecutor

__all__ = ["GradioOutput", "GradioExecutor"]
