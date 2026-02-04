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

    def get_after_snapshot_path(self, session_id: int, snapshot_index: int) -> Path:
        """获取调用后快照文件路径（仅用于调试）"""
        return self.session_dir / f"{session_id}.{snapshot_index}.after.json"

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

            fs_snapshot_status = "skipped"
            try:
                ok, status = self._save_filesystem_snapshot(session_id, snapshot_index)
                if not ok:
                    fs_snapshot_status = "failed"
                else:
                    fs_snapshot_status = status
            except Exception as e:
                fs_snapshot_status = "failed"
                print(f"[warning]保存目录快照失败: {e}[/warning]")

            data = {
                "session_id": session_id,
                "snapshot_index": snapshot_index,
                "created_at": datetime.now().isoformat(),
                "global_subagent_count": executor._global_subagent_count,
                "context_stack": stack_data,
                "current_agent": current_data,
                "auto_approve": getattr(executor, 'auto_approve', False),
                "workspace_root": str(self._get_workspace_root(session_id)),
                "fs_snapshot_status": fs_snapshot_status,
            }
            with open(snapshot_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.current_session_id = session_id
            self.current_snapshot_index[session_id] = snapshot_index
            return True
        except Exception as e:
            print(f"[error]保存快照失败: {e}[/error]")
            return False

    def save_after_snapshot(self, executor: Executor, session_id: int, snapshot_index: int) -> bool:
        """保存调用后快照（仅用于调试，不保存文件快照）"""
        try:
            snapshot_path = self.get_after_snapshot_path(session_id, snapshot_index)
            stack_data = []
            for agent in executor.context_stack:
                stack_data.append(self._serialize_agent(agent))
            current_data = None
            if executor.current_agent:
                current_data = self._serialize_agent(executor.current_agent)
            data = {
                "session_id": session_id,
                "snapshot_index": snapshot_index,
                "snapshot_type": "after",
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
            return True
        except Exception as e:
            print(f"[error]保存调用后快照失败: {e}[/error]")
            return False

    def _deserialize_agent(self, agent_data: dict, config: Config, global_count: int,
                           output_handler: Optional['OutputHandler'] = None) -> SimpleAgent:
        agent = SimpleAgent(
            config=config,
            depth=agent_data["depth"],
            global_subagent_count=global_count,
            output_handler=output_handler,
            init_system_prompt=False
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
            save_old: 是否保存旧会话（默认 True，会话快照已保存，此参数保留以兼容接口）
            temp: 是否为临时会话（默认 False，临时会话不分配 ID，直到用户执行任务）

        Returns:
            tuple[int, Executor]: (新会话ID, 新的执行器)
                                 临时会话时返回 (0, 新的执行器)
        """
        # 会话快照已自动保存所有状态，无需额外保存
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

        if snapshot_index < 0:
            print(f"[error]快照索引非法: {snapshot_index}[/error]")
            return False

        workspace_root = self._get_workspace_root(session_id)
        try:
            self._clear_workspace(workspace_root, exclude_roots=[self.session_dir])
            # baseline 目录位于 sessions 下，_copy_workspace 会排除 session_dir，
            # 这里改用 _apply_snapshot_dir 以确保 baseline 能正确恢复到工作区
            latest_index, latest_dir = self._get_latest_saved_snapshot_dir(session_id, snapshot_index)
            print(f"回滚信息：工作目录={workspace_root} | baseline={baseline_dir} | 快照目录={latest_dir or '无'}")
            self._apply_snapshot_dir(baseline_dir, workspace_root)
            if latest_dir is not None:
                self._apply_snapshot_dir(latest_dir, workspace_root)
            self._trim_session_records(session_id, snapshot_index)
            return True
        except Exception as e:
            print(f"[error]回滚失败: {e}[/error]")
            return False

    def _trim_session_records(self, session_id: int, snapshot_index: int) -> None:
        snapshot_files_to_delete: list[Path] = []
        for snapshot_file in self.session_dir.glob(f"{session_id}.*.json"):
            try:
                parts = snapshot_file.stem.split(".")
                if len(parts) < 2:
                    continue
                index = int(parts[1])
                if index > snapshot_index or (index == snapshot_index and "after" in parts[2:]):
                    snapshot_files_to_delete.append(snapshot_file)
            except (ValueError, OSError):
                continue

        fs_root = self._get_session_fs_root(session_id)
        snapshots_root = self._get_snapshots_root(session_id)
        fs_snapshots_to_delete: list[Path] = []
        if snapshots_root.exists():
            for path in snapshots_root.glob("snapshot_*"):
                try:
                    index = int(path.name.split("_", 1)[1])
                except (ValueError, IndexError):
                    continue
                if index > snapshot_index:
                    fs_snapshots_to_delete.append(path)

        if snapshot_files_to_delete or fs_snapshots_to_delete:
            print(f"回滚清理：将清理快照索引 > {snapshot_index} 的记录")
            if snapshot_files_to_delete:
                snapshot_files_to_delete.sort(
                    key=lambda p: int(p.name.split(".")[1]) if len(p.name.split(".")) >= 3 else 0
                )
                min_snapshot = snapshot_files_to_delete[0].name if snapshot_files_to_delete else ""
                min_snapshot_index = int(min_snapshot.split(".")[1]) if min_snapshot else None
                print(f"  会话快照文件（将删除）：{len(snapshot_files_to_delete)}")
                if min_snapshot:
                    print(f"    - 最小索引={min_snapshot_index}（文件名={min_snapshot}）")
                for path in snapshot_files_to_delete[:3]:
                    print(f"    - {path.name}")
                if len(snapshot_files_to_delete) > 3:
                    print(f"    ... 省略 {len(snapshot_files_to_delete) - 3} 个")
            if fs_snapshots_to_delete:
                fs_snapshots_to_delete.sort(
                    key=lambda p: int(p.name.split("_", 1)[1]) if "_" in p.name else 0
                )
                min_fs_snapshot = fs_snapshots_to_delete[0].name if fs_snapshots_to_delete else ""
                min_fs_index = int(min_fs_snapshot.split("_", 1)[1]) if min_fs_snapshot else None
                print(f"  文件系统快照目录（将删除）：{len(fs_snapshots_to_delete)}")
                if min_fs_snapshot:
                    print(f"    - 最小索引={min_fs_index}（目录名={min_fs_snapshot}）")
                for path in fs_snapshots_to_delete[:3]:
                    print(f"    - {path.name}")
                if len(fs_snapshots_to_delete) > 3:
                    print(f"    ... 省略 {len(fs_snapshots_to_delete) - 3} 个")

        for snapshot_file in snapshot_files_to_delete:
            try:
                snapshot_file.unlink()
            except OSError:
                continue

        for path in fs_snapshots_to_delete:
            shutil.rmtree(path, ignore_errors=True)

        if fs_root.exists():
            # 保留 baseline，仅清理超出回滚点的快照目录
            pass
        self.current_snapshot_index[session_id] = snapshot_index

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
        # 使用 0-based 命名，snapshot_000 对应 index=0
        return self._get_snapshots_root(session_id) / f"snapshot_{snapshot_index:03d}"

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

    def _is_reserved_device_name(self, name: str) -> bool:
        reserved = {
            "con", "prn", "aux", "nul",
            "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
            "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
        }
        normalized = name.rstrip(" .")
        base = normalized.split(".")[0] if normalized else ""
        return base.lower() in reserved

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
                if self._is_reserved_device_name(filename):
                    continue
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
                if self._is_reserved_device_name(filename):
                    continue
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

    def _save_filesystem_snapshot(self, session_id: int, snapshot_index: int) -> tuple[bool, str]:
        baseline_dir = self._ensure_baseline(session_id)
        if not baseline_dir:
            return False, "failed"

        workspace_root = self._get_workspace_root(session_id)

        baseline_files = {
            rel: abs_path for abs_path, rel in self._iter_files(baseline_dir)
        }
        current_files = {
            rel: abs_path for abs_path, rel in self._iter_files(workspace_root, exclude_roots=[self.session_dir])
        }

        changed_files: list[tuple[Path, Path]] = []
        for rel_path, current_path in current_files.items():
            baseline_path = baseline_files.get(rel_path)
            if baseline_path is None or not self._files_are_equal(baseline_path, current_path):
                changed_files.append((rel_path, current_path))

        deleted_files: list[Path] = []
        for rel_path in baseline_files:
            if rel_path not in current_files:
                deleted_files.append(rel_path)

        current_signature = self._build_snapshot_signature_from_changes(changed_files, deleted_files)
        prev_signature = self._get_previous_snapshot_signature(session_id, snapshot_index)
        if prev_signature is not None and current_signature == prev_signature:
            return True, "skipped"
        if prev_signature is None and not current_signature:
            return True, "skipped"

        snapshot_dir = self._get_snapshot_dir(session_id, snapshot_index)
        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        for rel_path, current_path in changed_files:
            dest_path = snapshot_dir / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(current_path, dest_path)

        for rel_path in deleted_files:
            marker_path = snapshot_dir / f"{rel_path}.___deleted___"
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            with open(marker_path, "w", encoding="utf-8"):
                pass

        return True, "saved"

    def _get_latest_saved_snapshot_dir(self, session_id: int, snapshot_index: int) -> tuple[Optional[int], Optional[Path]]:
        snapshots_root = self._get_snapshots_root(session_id)
        latest_index = None
        latest_dir = None
        for path in snapshots_root.glob("snapshot_*"):
            try:
                index = int(path.name.split("_", 1)[1])
            except (ValueError, IndexError):
                continue
            if index > snapshot_index:
                continue
            if latest_index is None or index > latest_index:
                latest_index = index
                latest_dir = path
        return latest_index, latest_dir

    def _build_snapshot_signature_from_changes(
        self,
        changed_files: list[tuple[Path, Path]],
        deleted_files: list[Path],
    ) -> dict[str, str]:
        signature: dict[str, str] = {}
        for rel_path, current_path in changed_files:
            signature[str(rel_path)] = self._file_hash(current_path)
        for rel_path in deleted_files:
            signature[f"{rel_path}.___deleted___"] = "DELETED"
        return signature

    def _build_snapshot_signature_from_dir(self, snapshot_dir: Path) -> dict[str, str]:
        signature: dict[str, str] = {}
        for abs_path, rel_path in self._iter_files(snapshot_dir):
            if rel_path.name.endswith(".___deleted___"):
                signature[str(rel_path)] = "DELETED"
            else:
                signature[str(rel_path)] = self._file_hash(abs_path)
        return signature

    def _get_previous_snapshot_signature(self, session_id: int, snapshot_index: int) -> Optional[dict[str, str]]:
        if snapshot_index <= 0:
            return None
        prev_index, prev_dir = self._get_latest_saved_snapshot_dir(session_id, snapshot_index - 1)
        if prev_dir is None:
            return None
        return self._build_snapshot_signature_from_dir(prev_dir)
