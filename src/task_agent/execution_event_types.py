"""执行事件类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any


@dataclass(slots=True)
class ExecutionEvent:
    """统一执行事件。"""

    event_type: str
    payload: dict[str, Any]
    event_id: str = ""
    session_key: str = ""
    agent_depth: int = 0
    ts: float = field(default_factory=time)

