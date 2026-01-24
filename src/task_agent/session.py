from .agent import Executor, SimpleAgent, Message
from .config import Config
from pathlib import Path
import json
from datetime import datetime
from typing import Optional
import os
import shutil
import hashlib

class SessionManager:
    def __init__(self):
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        self.session_dir = Path(project_root) / "sessions"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.fs_snapshot_root = self.session_dir / "fs_snapshots"
        self.fs_snapshot_root.mkdir(parents=True, exist_ok=True)
        self.current_session_id: Optional[int] = None
        self._pending_executor: Optional[Executor] = None  # 待切换的 executor
        self.current_snapshot_index: dict[int, int] = {}  # session_id -> snapshot_index
        self._session_workspace: dict[int, Path] = {}


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
                "workspace_root": str(self._get_workspace_root(session_id)),
            }
            with open(snapshot_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.current_session_id = session_id
            self.current_snapshot_index[session_id] = snapshot_index
            # 保存工作目录快照（用于回滚）
            try:
                self._save_filesystem_snapshot(session_id, snapshot_index)
            except Exception as e:
                print(f"[warning]保存目录快照失败: {e}[/warning]")
            return True
        except Exception as e:
            print(f"[error]保存快照失败: {e}[/error]")
            return False

    def save_filesystem_snapshot_only(self, session_id: int, snapshot_index: int) -> bool:
        """仅保存工作目录快照（用于命令执行后的补充快照）"""
        snapshot_path = self.get_snapshot_path(session_id, snapshot_index)
        if not snapshot_path.exists():
            return False
        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            workspace_root = data.get("workspace_root")
            if workspace_root:
                self._session_workspace[session_id] = self._normalize_path(workspace_root)
        except Exception:
            pass
        try:
            return self._save_filesystem_snapshot(session_id, snapshot_index)
        except Exception as e:
            print(f"[warning]保存目录快照失败: {e}[/warning]")
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
            workspace_root = data.get("workspace_root")
            if workspace_root and not self._ensure_workspace_match(workspace_root):
                print(f"[error]当前目录与会话工作目录不一致，禁止恢复: {workspace_root}[/error]")
                return None
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
            if workspace_root:
                self._session_workspace[session_id] = self._normalize_path(workspace_root)
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

    def list_session_snapshots(self, session_id: int) -> list[dict]:
        """列出会话的快照点（用于二级回滚选择）"""
        snapshots: list[dict] = []
        pattern = f"{session_id}.*.json"
        for snapshot_file in self.session_dir.glob(pattern):
            try:
                parts = snapshot_file.stem.split(".")
                if len(parts) != 2:
                    continue
                snapshot_index = int(parts[1])
            except (ValueError, IndexError):
                continue

            try:
                with open(snapshot_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}

            last_message = ""
            current = data.get("current_agent") or {}
            history = current.get("history", [])
            if history:
                last_message = history[-1].get("content", "").replace("\n", " ").strip()

            snapshots.append({
                "session_id": session_id,
                "snapshot_index": snapshot_index,
                "created_at": data.get("created_at", "Unknown"),
                "last_message": last_message,
            })

        snapshots.sort(key=lambda x: x["snapshot_index"])
        return snapshots

    def rollback_to_snapshot(self, session_id: int, snapshot_index: int,
                             confirm_callback: Optional[callable] = None) -> bool:
        """回滚到会话的指定快照点（需要确认）"""
        if confirm_callback is None:
            print("[warning]回滚需要确认回调，请在调用方提供确认逻辑[/warning]")
            return False

        prompt = f"即将清空当前工作目录并回滚到会话 {session_id} 的快照 {snapshot_index}，是否继续？"
        if not confirm_callback(prompt):
            return False

        snapshot_path = self.get_snapshot_path(session_id, snapshot_index)
        if not snapshot_path.exists():
            print(f"[error]快照不存在: {snapshot_index}[/error]")
            return False

        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                snapshot_data = json.load(f)
        except Exception:
            snapshot_data = {}

        workspace_root = snapshot_data.get("workspace_root")
        if workspace_root and not self._ensure_workspace_match(workspace_root):
            print(f"[error]当前目录与会话工作目录不一致，禁止回滚: {workspace_root}[/error]")
            return False

        baseline_dir = self._get_baseline_dir(session_id)
        if not baseline_dir.exists():
            print("[error]baseline 不存在，无法回滚[/error]")
            return False

        snapshots_root = self._get_snapshots_root(session_id)
        snapshot_dirs = {}
        for path in snapshots_root.glob("snapshot_*"):
            try:
                index = int(path.name.split("_", 1)[1]) - 1
            except (ValueError, IndexError):
                continue
            snapshot_dirs[index] = path

        if snapshot_index not in snapshot_dirs:
            print(f"[error]快照不存在: {snapshot_index}[/error]")
            return False

        missing = [i for i in range(0, snapshot_index + 1) if i not in snapshot_dirs]
        if missing:
            print(f"[error]回滚失败，缺少快照: {missing}[/error]")
            return False

        workspace_root = self._get_workspace_root(session_id)
        try:
            self._clear_workspace(workspace_root, exclude_roots=[self.session_dir])
            self._copy_workspace(baseline_dir, workspace_root)
            for idx in range(0, snapshot_index + 1):
                self._apply_snapshot_dir(snapshot_dirs[idx], workspace_root)
            self._purge_session_records(session_id)
            return True
        except Exception as e:
            print(f"[error]回滚失败: {e}[/error]")
            return False

    def _purge_session_records(self, session_id: int) -> None:
        for snapshot_file in self.session_dir.glob(f"{session_id}.*.json"):
            try:
                snapshot_file.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                pass
        fs_root = self._get_session_fs_root(session_id)
        if fs_root.exists():
            shutil.rmtree(fs_root, ignore_errors=True)
        self.current_snapshot_index.pop(session_id, None)
        self._session_workspace.pop(session_id, None)

    def _clear_workspace(self, workspace_root: Path, exclude_roots: list[Path]) -> None:
        for dirpath, dirnames, filenames in os.walk(workspace_root, topdown=False):
            current_dir = Path(dirpath)
            if self._is_excluded_dir(current_dir, exclude_roots):
                continue
            for filename in filenames:
                file_path = current_dir / filename
                try:
                    file_path.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    shutil.rmtree(file_path, ignore_errors=True)
            for dirname in dirnames:
                dir_path = current_dir / dirname
                if self._is_excluded_dir(dir_path, exclude_roots):
                    continue
                shutil.rmtree(dir_path, ignore_errors=True)

    def _strip_delete_suffix(self, rel_path: Path) -> Path:
        suffix = ".___deleted___"
        name = rel_path.name
        if not name.endswith(suffix):
            return rel_path
        stripped = name[:-len(suffix)]
        return rel_path.with_name(stripped)

    def _apply_snapshot_dir(self, snapshot_dir: Path, workspace_root: Path) -> None:
        for abs_path, rel_path in self._iter_files(snapshot_dir):
            if rel_path.name.endswith(".___deleted___"):
                target_rel = self._strip_delete_suffix(rel_path)
                target_path = workspace_root / target_rel
                if target_path.exists():
                    if target_path.is_dir():
                        shutil.rmtree(target_path, ignore_errors=True)
                    else:
                        try:
                            target_path.unlink()
                        except FileNotFoundError:
                            pass
                continue
            dest_path = workspace_root / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(abs_path, dest_path)

    def _get_session_fs_root(self, session_id: int) -> Path:
        return self.fs_snapshot_root / f"session_{session_id}"

    def _get_baseline_dir(self, session_id: int) -> Path:
        return self._get_session_fs_root(session_id) / "baseline"

    def _get_snapshots_root(self, session_id: int) -> Path:
        return self._get_session_fs_root(session_id) / "snapshots"

    def _get_snapshot_dir(self, session_id: int, snapshot_index: int) -> Path:
        # 使用 1-based 命名，符合 snapshot_001 风格
        return self._get_snapshots_root(session_id) / f"snapshot_{snapshot_index + 1:03d}"

    def _get_workspace_root(self, session_id: int) -> Path:
        if session_id not in self._session_workspace:
            self._session_workspace[session_id] = Path(os.getcwd()).resolve()
        return self._session_workspace[session_id]

    def _normalize_path(self, path_str: str) -> Path:
        return Path(path_str).resolve()

    def _ensure_workspace_match(self, workspace_root: str) -> bool:
        expected = self._normalize_path(workspace_root)
        current = Path(os.getcwd()).resolve()
        return expected == current

    def _is_excluded_dir(self, path: Path, exclude_roots: list[Path]) -> bool:
        resolved = path.resolve()
        for root in exclude_roots:
            try:
                root_resolved = root.resolve()
            except FileNotFoundError:
                continue
            if resolved == root_resolved or root_resolved in resolved.parents:
                return True
        return False

    def _iter_files(self, root: Path, exclude_roots: Optional[list[Path]] = None) -> list[tuple[Path, Path]]:
        exclude_roots = exclude_roots or []
        results: list[tuple[Path, Path]] = []
        for dirpath, dirnames, filenames in os.walk(root):
            current_dir = Path(dirpath)
            if self._is_excluded_dir(current_dir, exclude_roots):
                dirnames[:] = []
                continue
            # 剔除需要跳过的子目录
            filtered = []
            for name in dirnames:
                child = current_dir / name
                if not self._is_excluded_dir(child, exclude_roots):
                    filtered.append(name)
            dirnames[:] = filtered

            for filename in filenames:
                abs_path = current_dir / filename
                rel_path = abs_path.relative_to(root)
                results.append((abs_path, rel_path))
        return results

    def _copy_workspace(self, workspace_root: Path, baseline_dir: Path) -> None:
        exclude_roots = [self.session_dir]
        for dirpath, dirnames, filenames in os.walk(workspace_root):
            current_dir = Path(dirpath)
            if self._is_excluded_dir(current_dir, exclude_roots):
                dirnames[:] = []
                continue
            filtered = []
            for name in dirnames:
                child = current_dir / name
                if not self._is_excluded_dir(child, exclude_roots):
                    filtered.append(name)
            dirnames[:] = filtered

            rel_dir = current_dir.relative_to(workspace_root)
            dest_dir = baseline_dir / rel_dir
            dest_dir.mkdir(parents=True, exist_ok=True)

            for filename in filenames:
                src_file = current_dir / filename
                dest_file = dest_dir / filename
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)

    def _ensure_baseline(self, session_id: int) -> Optional[Path]:
        baseline_dir = self._get_baseline_dir(session_id)
        if baseline_dir.exists():
            return baseline_dir
        try:
            baseline_dir.mkdir(parents=True, exist_ok=True)
            workspace_root = self._get_workspace_root(session_id)
            self._copy_workspace(workspace_root, baseline_dir)
            return baseline_dir
        except Exception as e:
            print(f"[error]创建 baseline 失败: {e}[/error]")
            return None

    def _file_hash(self, path: Path) -> str:
        sha256 = hashlib.sha256()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(8192)
                if not chunk:
                    break
                sha256.update(chunk)
        return sha256.hexdigest()

    def _files_are_equal(self, baseline_path: Path, current_path: Path) -> bool:
        try:
            base_stat = baseline_path.stat()
            curr_stat = current_path.stat()
        except OSError:
            return False
        if base_stat.st_size == curr_stat.st_size and int(base_stat.st_mtime) == int(curr_stat.st_mtime):
            return True
        return self._file_hash(baseline_path) == self._file_hash(current_path)

    def _save_filesystem_snapshot(self, session_id: int, snapshot_index: int) -> bool:
        baseline_dir = self._ensure_baseline(session_id)
        if not baseline_dir:
            return False

        workspace_root = self._get_workspace_root(session_id)
        snapshot_dir = self._get_snapshot_dir(session_id, snapshot_index)
        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        baseline_files = {
            rel: abs_path for abs_path, rel in self._iter_files(baseline_dir)
        }
        current_files = {
            rel: abs_path for abs_path, rel in self._iter_files(workspace_root, exclude_roots=[self.session_dir])
        }

        # 新增或修改文件
        for rel_path, current_path in current_files.items():
            baseline_path = baseline_files.get(rel_path)
            if baseline_path is None or not self._files_are_equal(baseline_path, current_path):
                dest_path = snapshot_dir / rel_path
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(current_path, dest_path)

        # 删除标记文件
        for rel_path in baseline_files:
            if rel_path not in current_files:
                marker_path = snapshot_dir / f"{rel_path}.___deleted___"
                marker_path.parent.mkdir(parents=True, exist_ok=True)
                with open(marker_path, "w", encoding="utf-8"):
                    pass

        return True



