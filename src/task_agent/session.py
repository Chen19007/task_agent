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
        self.current_snapshot_index: dict[int, int] = {}  # session_id -> snapshot_index


    def get_snapshot_path(self, session_id: int, snapshot_index: int) -> Path:
        """获取快照文件路径"""
        return self.session_dir / f"{session_id}.{snapshot_index}.json"

    def get_session_path(self, session_id: int) -> Path:
        """获取会话路径（兼容旧接口，返回最新快照）"""
        # 查找该会话的最大 snapshot_index
        max_index = self._get_max_snapshot_index(session_id)
        if max_index is None:
            # 如果没有快照，返回一个默认路径（不会实际使用）
            return self.session_dir / f"{session_id}.0.json"
        return self.get_snapshot_path(session_id, max_index)

    def _get_max_snapshot_index(self, session_id: int) -> Optional[int]:
        """获取指定会话的最大快照索引"""
        pattern = f"{session_id}.*.json"
        snapshots = list(self.session_dir.glob(pattern))
        if not snapshots:
            return None
        max_index = 0
        for f in snapshots:
            try:
                # 文件名格式: session_id.snapshot_index.json
                parts = f.stem.split(".")
                if len(parts) == 2:
                    index = int(parts[1])
                    max_index = max(max_index, index)
            except (ValueError, IndexError):
                continue
        return max_index

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
                # 支持快照格式: session_id.snapshot_index.json
                parts = f.stem.split(".")
                if len(parts) == 2:
                    # 快照文件，提取 session_id
                    used_ids.add(int(parts[0]))
                else:
                    # 旧格式: session_id.json
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
            "last_think": agent.last_think,
            "history": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                    "think": msg.think,
                }
                for msg in agent.history
            ]
        }
    
    def save_snapshot(self, executor: Executor, session_id: int, snapshot_index: int) -> bool:
        """保存会话快照

        Args:
            executor: 执行器
            session_id: 会话ID
            snapshot_index: 快照索引（第几个LLM调用）

        Returns:
            bool: 是否保存成功
        """
        try:
            snapshot_path = self.get_snapshot_path(session_id, snapshot_index)
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
                "snapshot_index": snapshot_index,
                "created_at": datetime.now().isoformat(),
                "global_subagent_count": executor._global_subagent_count,
                "context_stack": stack_data,
                "current_agent": current_data,
                "auto_approve": getattr(executor, 'auto_approve', False),
            }
            with open(snapshot_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.current_session_id = session_id
            self.current_snapshot_index[session_id] = snapshot_index
            return True
        except Exception as e:
            print(f"[error]保存快照失败: {e}[/error]")
            return False

    def _deserialize_agent(self, agent_data: dict, config: Config, global_count: int,
                           output_handler: Optional['OutputHandler'] = None) -> SimpleAgent:
        agent = SimpleAgent(
            config=config,
            depth=agent_data["depth"],
            global_subagent_count=global_count,
            output_handler=output_handler
        )
        agent.agent_id = agent_data["agent_id"]
        agent.last_think = agent_data.get("last_think", "")
        for msg_data in agent_data["history"]:
            msg = Message(
                role=msg_data["role"],
                content=msg_data["content"],
                timestamp=msg_data.get("timestamp", 0.0),
                think=msg_data.get("think", "")
            )
            agent.history.append(msg)
        return agent


    def load_session(self, session_id: int, config: Config,
                     output_handler: Optional['OutputHandler'] = None) -> Optional[Executor]:
        try:
            # 查找该会话的最大快照索引
            max_index = self._get_max_snapshot_index(session_id)
            if max_index is None:
                print(f"[error]会话不存在: {session_id}[/error]")
                return None

            snapshot_path = self.get_snapshot_path(session_id, max_index)
            with open(snapshot_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            executor = Executor(config, session_manager=self, output_handler=output_handler)
            executor._global_subagent_count = data.get("global_subagent_count", 0)
            executor.auto_approve = data.get("auto_approve", False)
            executor._snapshot_index = data.get("snapshot_index", max_index) + 1  # 下一个快照索引
            executor.context_stack = []
            for agent_data in data.get("context_stack", []):
                agent = self._deserialize_agent(
                    agent_data,
                    config,
                    executor._global_subagent_count,
                    output_handler=output_handler
                )
                # 恢复双回调
                agent.set_before_llm_callback(executor._before_llm_snapshot_callback)
                agent.set_after_llm_callback(executor._after_llm_snapshot_callback)
                executor.context_stack.append(agent)
            if data.get("current_agent"):
                executor.current_agent = self._deserialize_agent(
                    data["current_agent"],
                    config,
                    executor._global_subagent_count,
                    output_handler=output_handler
                )
                # 恢复双回调
                executor.current_agent.set_before_llm_callback(executor._before_llm_snapshot_callback)
                executor.current_agent.set_after_llm_callback(executor._after_llm_snapshot_callback)
            self.current_session_id = session_id
            self.current_snapshot_index[session_id] = data.get("snapshot_index", max_index)
            return executor
        except Exception as e:
            print(f"[error]加载会话失败: {e}[/error]")
            return None


    def list_sessions(self) -> list:
        """列出所有会话（每个会话只显示最新快照）"""
        # 收集所有会话的最新快照
        session_latest_snapshots = {}  # session_id -> (snapshot_file, snapshot_index)

        for session_file in self.session_dir.glob("*.json"):
            try:
                parts = session_file.stem.split(".")
                if len(parts) == 2:
                    # 快照格式: session_id.snapshot_index.json
                    session_id = int(parts[0])
                    snapshot_index = int(parts[1])
                    # 只保留最大索引
                    if session_id not in session_latest_snapshots or snapshot_index > session_latest_snapshots[session_id][1]:
                        session_latest_snapshots[session_id] = (session_file, snapshot_index)
                else:
                    # 旧格式: session_id.json（兼容）
                    session_id = int(parts[0])
                    if session_id not in session_latest_snapshots:
                        session_latest_snapshots[session_id] = (session_file, 0)
            except (ValueError, IndexError):
                continue

        # 读取最新快照并构建会话列表
        sessions = []
        for session_id, (snapshot_file, snapshot_index) in session_latest_snapshots.items():
            try:
                with open(snapshot_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                current = data.get("current_agent", {})
                history = current.get("history", [])
                context_stack = data.get("context_stack", [])

                # 提取首条用户消息作为标题
                first_message = ""
                if context_stack:
                    stack_history = context_stack[0].get("history", [])
                    for msg in stack_history:
                        if msg.get("role") == "user":
                            content = msg.get("content", "")
                            first_message = content.replace("\n", " ").strip()[:50]
                            if len(content) > 50:
                                first_message += "..."
                            break

                if not first_message:
                    for msg in history:
                        if msg.get("role") == "user":
                            content = msg.get("content", "")
                            first_message = content.replace("\n", " ").strip()[:50]
                            if len(content) > 50:
                                first_message += "..."
                            break

                summary = {
                    "session_id": session_id,
                    "snapshot_index": snapshot_index,
                    "created_at": data.get("created_at", "Unknown"),
                    "message_count": len(history),
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
            save_old: 是否保存旧会话（默认 True，但双快照机制已保存，此参数保留以兼容接口）
            temp: 是否为临时会话（默认 False，临时会话不分配 ID，直到用户执行任务）

        Returns:
            tuple[int, Executor]: (新会话ID, 新的执行器)
                                 临时会话时返回 (0, 新的执行器)
        """
        # 双快照机制已自动保存所有状态，无需额外保存
        # 保留 save_old 参数以兼容接口

        # 创建新 executor（传递 session_manager 以支持快照保存）
        config = executor.config
        new_executor = Executor(config, session_manager=self, output_handler=executor._output_handler)

        if temp:
            # 临时会话：不分配 ID，等用户执行任务后再分配
            self.current_session_id = None
            return 0, new_executor
        else:
            # 立即分配会话 ID
            new_id = self.get_next_session_id()
            self.current_session_id = new_id
            return new_id, new_executor



