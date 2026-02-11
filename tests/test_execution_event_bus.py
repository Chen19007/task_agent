from __future__ import annotations

from task_agent.execution_event_bus import ExecutionEventBus
from task_agent.execution_event_types import ExecutionEvent


def test_publish_order_and_unsubscribe():
    bus = ExecutionEventBus()
    got = []

    def h1(event: ExecutionEvent):
        got.append(("h1", event.event_type))

    def h2(event: ExecutionEvent):
        got.append(("h2", event.event_type))

    bus.subscribe(h1)
    bus.subscribe(h2)
    bus.publish(ExecutionEvent(event_type="content", payload={"x": 1}))
    bus.unsubscribe(h1)
    bus.publish(ExecutionEvent(event_type="think", payload={"x": 2}))

    assert got == [("h1", "content"), ("h2", "content"), ("h2", "think")]


def test_handler_exception_isolated():
    bus = ExecutionEventBus()
    got = []

    def bad(event: ExecutionEvent):  # noqa: ARG001
        raise RuntimeError("boom")

    def good(event: ExecutionEvent):
        got.append(event.event_type)

    bus.subscribe(bad)
    bus.subscribe(good)
    bus.publish(ExecutionEvent(event_type="ps_call", payload={}))

    assert got == ["ps_call"]

