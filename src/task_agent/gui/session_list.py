"""会话列表组件

显示和管理会话列表，直接从快照文件读取。
"""

import json
from pathlib import Path
from typing import Callable, Optional

# Dear PyGui 将在使用时动态导入
try:
    import dearpygui.dearpygui as dpg
except ImportError:
    dpg = None


class SessionList:
    """会话列表组件"""

    def __init__(self, parent: int, sessions_dir: str, on_session_select: Optional[Callable] = None):
        """初始化会话列表

        Args:
            parent: 父容器 ID
            sessions_dir: 会话目录路径
            on_session_select: 会话选择回调 (session_id: int)
        """
        if dpg is None:
            raise ImportError("Dear PyGui is not installed. Run: pip install dearpygui")

        self.parent = parent
        self.sessions_dir = Path(sessions_dir)
        self.on_session_select = on_session_select
        self.list_id: Optional[int] = None
        self._sessions = []
        self._current_session_id: Optional[int] = None  # 当前选中的会话ID
        self._create_ui()

    def _create_ui(self):
        """创建 UI"""
        with dpg.group(parent=self.parent):
            dpg.add_text("会话列表", color=(150, 150, 150))
            dpg.add_separator()

            # 会话列表
            self.list_id = dpg.add_listbox(
                items=[],
                num_items=10,
                callback=self._on_select,
                width=-1
            )

            # 新建会话按钮
            dpg.add_spacer(height=10)
            dpg.add_button(
                label="+ 新建会话",
                width=-1,
                callback=self._on_new_session
            )

    def _on_select(self, sender, app_data, user_data=None):
        """会话选择回调"""
        # app_data 通常是选中的显示文本
        if isinstance(app_data, str):
            items = dpg.get_item_configuration(self.list_id).get("items", [])
            if app_data not in items:
                return
            index = items.index(app_data)
        else:
            try:
                index = int(app_data)
            except (ValueError, TypeError):
                return

        if 0 <= index < len(self._sessions):
            session_id = self._sessions[index].get("session_id")

            # 检查是否点击了当前会话
            if session_id == self._current_session_id:
                return  # 已经是当前会话，无需切换

            # 更新当前会话ID
            self._current_session_id = session_id

            # 添加选中标记
            self._update_display()

            if self.on_session_select:
                self.on_session_select(session_id)

    def _on_new_session(self, sender, app_data, user_data=None):
        """新建会话回调"""
        if self.on_new_session:
            self.on_new_session()

    def load_sessions_from_disk(self):
        """直接从磁盘加载会话列表"""
        sessions = []

        # 遍历所有快照文件
        for snapshot_file in self.sessions_dir.glob("*.json"):
            try:
                with open(snapshot_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                session_id = data.get("session_id")
                if session_id is None:
                    continue

                # 获取历史消息
                current_agent = data.get("current_agent", {})
                history = current_agent.get("history", [])

                # 提取首条用户消息
                first_message = ""
                for msg in history:
                    if msg.get("role") == "user":
                        content = msg.get("content", "")
                        first_message = content.replace("\n", " ").strip()[:50]
                        if len(content) > 50:
                            first_message += "..."
                        break

                sessions.append({
                    "session_id": session_id,
                    "created_at": data.get("created_at", ""),
                    "message_count": len(history),
                    "first_message": first_message,
                    "snapshot_file": snapshot_file
                })
            except Exception:
                continue

        # 去重（同一会话可能有多个快照）
        unique_sessions = {}
        for s in sessions:
            sid = s["session_id"]
            if sid not in unique_sessions or s["created_at"] > unique_sessions[sid]["created_at"]:
                unique_sessions[sid] = s

        self._sessions = list(unique_sessions.values())
        self._sessions.sort(key=lambda x: x["session_id"])
        self._update_display()

    def _update_display(self):
        """更新显示"""
        display_items = []
        for s in self._sessions:
            session_id = s.get("session_id", 0)
            created_at = s.get("created_at", "")[:19]
            msg_preview = s.get("first_message", "")

            # 添加当前会话标记
            prefix = "* " if session_id == self._current_session_id else "  "

            if msg_preview:
                text = f"{prefix}#{session_id} | {created_at} | {msg_preview}"
            else:
                text = f"{prefix}#{session_id} | {created_at}"

            display_items.append(text)

        dpg.configure_item(self.list_id, items=display_items)

    def load_session_messages(self, session_id: int) -> list:
        """加载指定会话的消息

        Returns:
            消息列表，每项包含 role, content, timestamp
        """
        # 查找该会话的最新快照
        snapshot_files = list(self.sessions_dir.glob(f"{session_id}.*.json"))
        if not snapshot_files:
            return []

        # 找到索引最大的快照
        snapshot_files.sort(key=lambda f: int(f.stem.split(".")[1]) if "." in f.stem else 0)
        latest_snapshot = snapshot_files[-1]

        try:
            with open(latest_snapshot, "r", encoding="utf-8") as f:
                data = json.load(f)

            current_agent = data.get("current_agent", {})
            history = current_agent.get("history", [])

            # 过滤掉 system 消息
            return [
                {
                    "role": msg["role"],
                    "content": msg["content"],
                    "timestamp": msg["timestamp"],
                    "think": msg.get("think", "")
                }
                for msg in history
                if msg["role"] != "system"
            ]
        except Exception:
            return []

    def set_on_new_session(self, callback: Callable):
        """设置新建会话回调"""
        self.on_new_session = callback
