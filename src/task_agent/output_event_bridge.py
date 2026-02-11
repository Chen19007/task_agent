"""OutputHandler 到事件总线桥接。"""

from __future__ import annotations

from typing import Callable

from .execution_event_bus import ExecutionEventBus
from .execution_event_types import ExecutionEvent
from .output_handler import OutputHandler


class EventBusOutputHandler(OutputHandler):
    """发布事件后再委托给原始输出处理器。"""

    def __init__(
        self,
        delegate: OutputHandler,
        event_bus: ExecutionEventBus,
        session_key_getter: Callable[[], str] | None = None,
    ) -> None:
        self._delegate = delegate
        self._event_bus = event_bus
        self._session_key_getter = session_key_getter or (lambda: "")

    def _publish(self, event_type: str, payload: dict, agent_depth: int = 0) -> None:
        self._event_bus.publish(
            ExecutionEvent(
                event_type=event_type,
                payload=payload,
                session_key=self._session_key_getter(),
                agent_depth=agent_depth,
            )
        )

    def on_think(self, content: str) -> None:
        self._publish("think", {"content": content})
        self._delegate.on_think(content)

    def on_content(self, content: str) -> None:
        self._publish("content", {"content": content})
        self._delegate.on_content(content)

    def on_ps_call(self, command: str, index: int, depth_prefix: str) -> None:
        self._publish("ps_call", {"command": command, "index": index, "depth_prefix": depth_prefix})
        self._delegate.on_ps_call(command, index, depth_prefix)

    def on_ps_call_result(self, result: str, status: str) -> None:
        self._publish("ps_call_result", {"result": result, "status": status})
        self._delegate.on_ps_call_result(result, status)

    def on_create_agent(self, task: str, depth: int, agent_name: str, context_info: dict) -> None:
        self._publish(
            "create_agent",
            {"task": task, "depth": depth, "agent_name": agent_name, "context_info": context_info},
            agent_depth=depth,
        )
        self._delegate.on_create_agent(task, depth, agent_name, context_info)

    def on_agent_complete(self, summary: str, stats: dict) -> None:
        self._publish("agent_complete", {"summary": summary, "stats": stats})
        self._delegate.on_agent_complete(summary, stats)

    def on_depth_limit(self) -> None:
        self._publish("depth_limit", {})
        self._delegate.on_depth_limit()

    def on_quota_limit(self, limit_type: str) -> None:
        self._publish("quota_limit", {"limit_type": limit_type})
        self._delegate.on_quota_limit(limit_type)

    def on_wait_input(self) -> None:
        self._publish("wait_input", {})
        self._delegate.on_wait_input()

