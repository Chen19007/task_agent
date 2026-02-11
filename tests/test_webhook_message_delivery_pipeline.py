from __future__ import annotations

from task_agent.webhook.message_delivery_pipeline import MessageDeliveryPipeline


def test_split_and_send_in_order():
    pipeline = MessageDeliveryPipeline(max_chars=10, max_attempts=1, retry_delay=0)
    sent = []

    def send_func(text: str) -> str:
        sent.append(text)
        return f"id-{len(sent)}"

    ids = pipeline.send_text(send_func, "12345\n67890\nabcde")

    assert ids == ["id-1", "id-2", "id-3"]
    assert "".join(sent).replace("\n", "") == "1234567890abcde"


def test_retry_once_then_success():
    pipeline = MessageDeliveryPipeline(max_chars=100, max_attempts=2, retry_delay=0)
    state = {"n": 0}

    def send_func(text: str) -> str:  # noqa: ARG001
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("temporary")
        return "ok-id"

    ids = pipeline.send_text(send_func, "hello")

    assert ids == ["ok-id"]
    assert state["n"] == 2
