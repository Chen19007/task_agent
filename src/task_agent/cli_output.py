"""CLI 输出实现 - 保持现有显示效果

将 Agent 层的结构化输出转换为 Rich Console 显示。
"""

from rich.console import Console
from .output_handler import OutputHandler


class CLIOutput(OutputHandler):
    """CLI 输出实现 - 保持现有显示效果"""

    def __init__(self, console: Console):
        """初始化 CLI 输出

        Args:
            console: Rich Console 实例
        """
        self.console = console

    def on_think(self, content: str) -> None:
        """显示思考过程（带边框）"""
        self.console.print("\n** 思考过程：\n")
        self.console.print("━" * 50)
        self.console.print(content)
        self.console.print("━" * 50 + "\n")

    def on_content(self, content: str) -> None:
        """显示普通内容"""
        self.console.print(content)

    def on_ps_call(self, command: str, index: int, depth_prefix: str) -> None:
        """显示命令框 - CLI 返回字符串供确认逻辑使用"""
        block = f"\n>> [待执行命令 #{index}]\n命令: {command}\n{'━'*50}\n\n"
        if depth_prefix:
            lines = block.split('\n')
            prefixed = [depth_prefix + line if line.strip() else line for line in lines]
            block = '\n'.join(prefixed)
        self.console.print(block, end="")

    def on_ps_call_result(self, result: str, status: str) -> None:
        """显示命令结果"""
        if status == "executed":
            self.console.print(f"\n[info]{result}[/info]\n")
        else:  # rejected
            self.console.print(f"[info]{result}[/info]\n")

    def on_create_agent(self, task: str, depth: int, agent_name: str,
                       context_info: dict) -> None:
        """显示子 Agent 创建"""
        agent_info = f" [{agent_name}]" if agent_name else ""
        self.console.print(f"\n{'+'*60}")
        self.console.print(f"深度: {depth}/{context_info.get('max_depth', 4)}{agent_info} | 任务: {task}")
        self.console.print(f"上下文: {context_info.get('context_used', 0)}/{context_info.get('context_total', 0)} | 配额: {context_info.get('global_count', 0)}/{context_info.get('global_total', 0)}")
        self.console.print(f"{'+'*60}\n")

    def on_agent_complete(self, summary: str, stats: dict) -> None:
        """显示完成信息"""
        self.console.print(f"\n{'='*50}")
        self.console.print("[任务完成]")
        self.console.print(f"{'='*50}")
        self.console.print(summary)
        self.console.print(f"执行命令: {stats['commands']} | 创建子Agent: {stats['sub_agents']}\n")

    def on_depth_limit(self) -> None:
        """深度限制"""
        self.console.print(f"\n!! [深度限制]\n已达到最大深度，由当前Agent执行\n{'═'*50}\n")

    def on_quota_limit(self, limit_type: str) -> None:
        """配额限制"""
        if limit_type == "local":
            self.console.print(f"\n!! [本地配额限制]\n当前Agent已用完子Agent配额\n{'═'*50}\n")
        else:
            self.console.print(f"\n!! [全局配额限制]\n整个任务已用完所有子Agent配额\n{'═'*50}\n")

    def on_wait_input(self) -> None:
        """等待输入"""
        self.console.print(f"\n?? 等待用户输入...\n[等待用户输入]\n")
