"""轻量执行事件总线。"""

from __future__ import annotations

import threading
from typing import Callable

from .execution_event_types import ExecutionEvent

EventHandler = Callable[[ExecutionEvent], None]


class ExecutionEventBus:
    """线程安全的同步事件总线。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        with self._lock:
            self._handlers.append(handler)

    def unsubscribe(self, handler: EventHandler) -> None:
        with self._lock:
            self._handlers = [h for h in self._handlers if h is not handler]

    def publish(self, event: ExecutionEvent) -> None:
        with self._lock:
            handlers = list(self._handlers)
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                # 事件总线不应影响主流程
                continue

