"""输出处理接口 - Agent 层调用，UI 层实现

定义 Agent 输出事件的结构化回调接口，实现显示逻辑与业务逻辑分离。
"""

from abc import ABC, abstractmethod
from typing import Optional


class OutputHandler(ABC):
    """输出处理接口 - Agent 层调用，UI 层实现

    Agent 层通过调用此接口的方法来输出不同类型的内容，
    UI 层（CLI/GUI）实现具体显示逻辑。
    """

    @abstractmethod
    def on_think(self, content: str) -> None:
        """LLM 推理内容（思考过程）

        Args:
            content: LLM 返回的 reasoning 内容
        """
        pass

    @abstractmethod
    def on_content(self, content: str) -> None:
        """普通文本内容

        Args:
            content: LLM 返回的 content 内容
        """
        pass

    @abstractmethod
    def on_ps_call(self, command: str, index: int, depth_prefix: str) -> None:
        """PowerShell 命令请求

        Args:
            command: 命令内容
            index: 命令编号（全局递增）
            depth_prefix: 深度前缀 (如 "++ " 表示深度为2)
        """
        pass

    @abstractmethod
    def on_ps_call_result(self, result: str, status: str) -> None:
        """命令执行结果

        Args:
            result: 结果内容
            status: executed/rejected
        """
        pass

    @abstractmethod
    def on_create_agent(self, task: str, depth: int, agent_name: str,
                       context_info: dict) -> None:
        """创建子 Agent

        Args:
            task: 任务描述
            depth: 新深度
            agent_name: 预定义 agent 名称
            context_info: 上下文信息 {
                context_used: 已使用上下文,
                context_total: 总上下文,
                global_count: 全局子 agent 计数,
                global_total: 全局配额,
                max_depth: 最大深度
            }
        """
        pass

    @abstractmethod
    def on_agent_complete(self, summary: str, stats: dict) -> None:
        """Agent 完成

        Args:
            summary: 完成摘要
            stats: 统计信息 {
                commands: 执行命令数,
                sub_agents: 创建子 agent 数,
                duration: 耗时（秒）
            }
        """
        pass

    @abstractmethod
    def on_depth_limit(self) -> None:
        """达到深度限制"""
        pass

    @abstractmethod
    def on_quota_limit(self, limit_type: str) -> None:
        """配额限制

        Args:
            limit_type: "local"（本地配额）或 "global"（全局配额）
        """
        pass

    @abstractmethod
    def on_wait_input(self) -> None:
        """等待用户输入"""
        pass


class NullOutputHandler(OutputHandler):
    """空输出处理器 - 用于测试或不需输出的场景

    所有方法都是空实现，避免到处判断 None。
    """

    def on_think(self, content: str) -> None:
        pass

    def on_content(self, content: str) -> None:
        pass

    def on_ps_call(self, command: str, index: int, depth_prefix: str) -> None:
        pass

    def on_ps_call_result(self, result: str, status: str) -> None:
        pass

    def on_create_agent(self, task: str, depth: int, agent_name: str,
                       context_info: dict) -> None:
        pass

    def on_agent_complete(self, summary: str, stats: dict) -> None:
        pass

    def on_depth_limit(self) -> None:
        pass

    def on_quota_limit(self, limit_type: str) -> None:
        pass

    def on_wait_input(self) -> None:
        pass
