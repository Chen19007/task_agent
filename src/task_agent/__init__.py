"""任务执行 Agent - 通用任务自动化工具"""

__version__ = "0.1.0"
__author__ = "MiniMax Agent"

from .agent import Action, Executor, SimpleAgent, StepResult
from .command_spec import CommandSpec
from .config import Config

__all__ = ["SimpleAgent", "Executor", "Action", "StepResult", "CommandSpec", "Config"]
