from .agent import Executor, SimpleAgent, Message
from .config import Config
from pathlib import Path
import json
from datetime import datetime
from typing import Optional
import os

class SessionManager:
    def __init__(self):
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        self.session_dir = Path(project_root) / "sessions"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.current_session_id: Optional[int] = None
        self._pending_executor: Optional[Executor] = None  # 待切换的 executor


    def get_session_path(self, session_id: int) -> Path:
        return self.session_dir / f"{session_id}.json"

    def set_pending_executor(self, executor: Executor):
        """设置待切换的executor（用于在等待输入循环中切换会话）"""
        self._pending_executor = executor

    def get_pending_executor(self) -> Optional[Executor]:
        """获取并清空待切换的executor"""
        executor = self._pending_executor
        self._pending_executor = None
        return executor
    
    def get_next_session_id(self) -> int:
        used_ids = set()
        for f in self.session_dir.glob("*.json"):
            try:
                used_ids.add(int(f.stem))
            except ValueError:
                continue
        for i in range(1, 1001):
            if i not in used_ids:
                return i
        return 1


    def _serialize_agent(self, agent: SimpleAgent) -> dict:
        return {
            "agent_id": agent.agent_id,
            "depth": agent.depth,
            "history": [
                {"role": msg.role, "content": msg.content, "timestamp": msg.timestamp}
                for msg in agent.history
            ]
        }
    
    def save_session(self, executor: Executor, session_id: int) -> bool:
        try:
            session_path = self.get_session_path(session_id)
            stack_data = []
            for agent in executor.context_stack:
                stack_data.append(self._serialize_agent(agent))
            current_data = None
            if executor.current_agent:
                current_data = self._serialize_agent(executor.current_agent)
                # 去重系统提示词：只保留最后一条（最新的）
                if "history" in current_data:
                    filtered_history = []
                    last_system_msg = None
                    for msg in reversed(current_data["history"]):
                        if msg["role"] == "system" and msg["content"].startswith("你是一个任务执行agent"):
                            # 只保留最后一条系统提示词
                            if last_system_msg is None:
                                last_system_msg = msg
                            continue
                        filtered_history.append(msg)
                    if last_system_msg:
                        filtered_history.append(last_system_msg)
                    current_data["history"] = list(reversed(filtered_history))
            data = {
                "session_id": session_id,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "global_subagent_count": executor._global_subagent_count,
                "context_stack": stack_data,
                "current_agent": current_data,
            }
            with open(session_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.current_session_id = session_id
            return True
        except Exception as e:
            print(f"[error]保存会话失败: {e}[/error]")
            return False


    def _deserialize_agent(self, agent_data: dict, config: Config, global_count: int) -> SimpleAgent:
        agent = SimpleAgent(
            config=config,
            depth=agent_data["depth"],
            global_subagent_count=global_count
        )
        agent.agent_id = agent_data["agent_id"]
        for msg_data in agent_data["history"]:
            msg = Message(
                role=msg_data["role"],
                content=msg_data["content"],
                timestamp=msg_data.get("timestamp", 0.0)
            )
            agent.history.append(msg)
        return agent


    def load_session(self, session_id: int, config: Config) -> Optional[Executor]:
        try:
            session_path = self.get_session_path(session_id)
            if not session_path.exists():
                print(f"[error]会话不存在: {session_id}[/error]")
                return None
            with open(session_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            executor = Executor(config)
            executor._global_subagent_count = data.get("global_subagent_count", 0)
            executor.context_stack = []
            for agent_data in data.get("context_stack", []):
                agent = self._deserialize_agent(agent_data, config, executor._global_subagent_count)
                executor.context_stack.append(agent)
            if data.get("current_agent"):
                executor.current_agent = self._deserialize_agent(
                    data["current_agent"], config, executor._global_subagent_count
                )
            self.current_session_id = session_id
            return executor
        except Exception as e:
            print(f"[error]加载会话失败: {e}[/error]")
            return None


    def list_sessions(self) -> list:
        sessions = []
        for session_file in self.session_dir.glob("*.json"):
            try:
                with open(session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                current = data.get("current_agent", {})
                history = current.get("history", [])
                context_stack = data.get("context_stack", [])

                # 提取首条用户消息作为标题
                # 优先从 context_stack 中提取（如果有的话），因为那是顶层任务
                first_message = ""
                if context_stack:
                    # 从顶层 agent 的 history 中查找第一条用户消息
                    stack_history = context_stack[0].get("history", [])
                    for msg in stack_history:
                        if msg.get("role") == "user":
                            content = msg.get("content", "")
                            # 清理换行符并截断
                            first_message = content.replace("\n", " ").strip()[:50]
                            if len(content) > 50:
                                first_message += "..."
                            break

                # 如果 context_stack 中没找到，从 current_agent 中查找
                if not first_message:
                    for msg in history:
                        if msg.get("role") == "user":
                            content = msg.get("content", "")
                            # 清理换行符并截断
                            first_message = content.replace("\n", " ").strip()[:50]
                            if len(content) > 50:
                                first_message += "..."
                            break

                summary = {
                    "session_id": data.get("session_id", 0),
                    "created_at": data.get("created_at", "Unknown"),
                    "updated_at": data.get("updated_at", "Unknown"),
                    "message_count": len(history),
                    # 深度：如果有 context_stack，则为当前深度；否则为 0
                    "depth": current.get("depth", 0) if current else 0,
                    "first_message": first_message,
                }
                sessions.append(summary)
            except Exception:
                continue
        sessions.sort(key=lambda x: x["session_id"])
        return sessions


    def create_new_session(self, executor: Executor, save_old: bool = True, temp: bool = False) -> tuple[int, Executor]:
        """创建新会话

        Args:
            executor: 当前的执行器
            save_old: 是否保存旧会话（默认 True）
            temp: 是否为临时会话（默认 False，临时会话不分配 ID，直到用户执行任务）

        Returns:
            tuple[int, Executor]: (新会话ID, 新的执行器)
                                 临时会话时返回 (0, 新的执行器)
        """
        # 保存当前会话（使用旧的 session_id）
        if save_old and self.current_session_id:
            self.save_session(executor, self.current_session_id)

        # 创建新 executor
        config = executor.config
        new_executor = Executor(config)

        if temp:
            # 临时会话：不分配 ID，等用户执行任务后再分配
            self.current_session_id = None
            return 0, new_executor
        else:
            # 立即分配会话 ID
            new_id = self.get_next_session_id()
            self.current_session_id = new_id
            return new_id, new_executor



