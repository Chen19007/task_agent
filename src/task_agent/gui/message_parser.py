"""消息解析器

解析 assistant 消息中的 tool tags，生成可折叠的消息块。
"""

import re
from dataclasses import dataclass
from typing import List


@dataclass
class MessageBlock:
    """消息块

    Attributes:
        block_type: 块类型 ('text', 'ps_call', 'create_agent', 'think', 'ps_call_result', 'return')
        content: 块内容
        collapsible: 是否可折叠（tool tags=True, text=False）
    """
    block_type: str
    content: str
    collapsible: bool


class MessageParser:
    """消息解析器

    将 assistant 消息解析为可折叠的消息块。
    所有 tool tags 都折叠，只有普通文本不折叠。
    """

    # 所有 tool tags 都识别为可折叠块
    # 使用 [\s\S]*? 匹配包括换行在内的任意字符（非贪婪）
    # 注意：think 不在这里解析，而是通过 reasoning 字段单独处理
    TOOL_TAG_PATTERNS = {
        "return": (r"<return>\s*([\s\S]*?)\s*</return>", True),
        "ps_call": (r"<ps_call\b[^>]*>\s*([\s\S]*?)\s*</ps_call>", True),
        "builtin": (r"<builtin\b[^>]*>\s*([\s\S]*?)\s*</builtin>", True),
        "create_agent": (r"<create_agent(?:\s+name=(\S+?))?\s*([\s\S]*?)</create_agent>", True),
        "ps_call_result": (r"<ps_call_result[^>]*>\s*([\s\S]*?)\s*</ps_call_result>", True),
    }

    def __init__(self):
        """编译正则表达式以提高性能"""
        self._compiled_patterns = {
            name: (re.compile(pattern, re.DOTALL), collapsible)
            for name, (pattern, collapsible) in self.TOOL_TAG_PATTERNS.items()
        }

    def parse(self, content: str) -> List[MessageBlock]:
        """解析消息内容，返回消息块列表

        Args:
            content: 消息内容

        Returns:
            消息块列表，按原始顺序排列
        """
        if not content:
            return [MessageBlock(block_type="text", content="", collapsible=False)]

        blocks = []
        remaining = content
        last_end = 0

        # 查找所有标签位置
        all_matches = []
        for tag_name, (pattern, collapsible) in self._compiled_patterns.items():
            for match in pattern.finditer(content):
                all_matches.append((match.start(), match.end(), tag_name, match, collapsible))

        # 按起始位置排序
        all_matches.sort(key=lambda x: x[0])

        # 处理文本和标签
        for start, end, tag_name, match, collapsible in all_matches:
            # 添加标签前的普通文本
            if start > last_end:
                text_content = content[last_end:start].strip()
                if text_content:
                    blocks.append(MessageBlock(
                        block_type="text",
                        content=text_content,
                        collapsible=False
                    ))

            # 添加标签内容
            # 对于有 name 属性的标签（如 create_agent），需要特殊处理
            groups = match.groups()
            if tag_name == "create_agent" and len(groups) >= 2:
                # groups[0] 是 name，groups[1] 是任务内容
                agent_name = groups[0] or "unnamed"
                task_content = groups[1]
                tag_content = f"[{agent_name}] {task_content}"
            else:
                # 其他标签直接使用第一个捕获组
                tag_content = groups[0] if groups else match.group(0)

            blocks.append(MessageBlock(
                block_type=tag_name,
                content=tag_content.strip(),
                collapsible=collapsible
            ))

            last_end = end

        # 添加最后剩余的普通文本
        if last_end < len(content):
            text_content = content[last_end:].strip()
            if text_content:
                blocks.append(MessageBlock(
                    block_type="text",
                    content=text_content,
                    collapsible=False
                ))

        # 如果没有找到任何标签，整个内容作为普通文本
        if not blocks and content.strip():
            blocks.append(MessageBlock(
                block_type="text",
                content=content.strip(),
                collapsible=False
            ))

        return blocks
