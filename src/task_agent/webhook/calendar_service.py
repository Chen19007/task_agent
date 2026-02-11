"""飞书日程服务封装。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass


@dataclass
class CalendarCreateResult:
    """创建日程结果。"""

    ok: bool
    message: str
    event_id: str = ""
    raw_data: dict | None = None
    warning: str = ""


def create_feishu_calendar_event(
    app_id: str,
    app_secret: str,
    calendar_id: str,
    summary: str,
    start_timestamp: int,
    end_timestamp: int,
    timezone: str = "Asia/Shanghai",
    description: str = "",
    need_notification: bool = False,
    user_id_type: str = "open_id",
    attendee_open_ids: list[str] | None = None,
    attendee_need_notification: bool = True,
) -> CalendarCreateResult:
    """
    创建飞书日程。

    Args:
        app_id: 飞书应用 ID
        app_secret: 飞书应用密钥
        calendar_id: 日历 ID
        summary: 日程标题
        start_timestamp: 开始时间（秒级时间戳）
        end_timestamp: 结束时间（秒级时间戳）
        timezone: 时区
        description: 日程描述
        need_notification: 是否通知参与人
    """
    if not app_id or not app_secret:
        return CalendarCreateResult(False, "缺少飞书应用配置：WEBHOOK_APP_ID / WEBHOOK_APP_SECRET")
    if not calendar_id:
        return CalendarCreateResult(False, "缺少默认日历配置：WEBHOOK_CALENDAR_ID")
    if not summary.strip():
        return CalendarCreateResult(False, "summary 不能为空")
    if end_timestamp <= start_timestamp:
        return CalendarCreateResult(False, "结束时间必须晚于开始时间")

    try:
        import lark_oapi as lark
        from lark_oapi.api.calendar.v4 import CalendarEvent, CreateCalendarEventRequest, TimeInfo
    except Exception as exc:
        return CalendarCreateResult(False, f"加载飞书 SDK 失败: {exc}")

    try:
        client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .build()
        )

        request_builder = (
            CreateCalendarEventRequest.builder()
            .calendar_id(calendar_id)
            .idempotency_key(str(uuid.uuid4()))
            .request_body(
                CalendarEvent.builder()
                .summary(summary.strip())
                .description(description.strip())
                .need_notification(bool(need_notification))
                .start_time(
                    TimeInfo.builder()
                    .timestamp(str(start_timestamp))
                    .timezone(timezone)
                    .build()
                )
                .end_time(
                    TimeInfo.builder()
                    .timestamp(str(end_timestamp))
                    .timezone(timezone)
                    .build()
                )
                .build()
            )
        )
        if user_id_type:
            request_builder = request_builder.user_id_type(user_id_type)
        request = request_builder.build()

        response = client.calendar.v4.calendar_event.create(request)
        if not response.success():
            detail = ""
            if getattr(response, "raw", None) and getattr(response.raw, "content", None):
                try:
                    detail = json.dumps(
                        json.loads(response.raw.content), ensure_ascii=False
                    )
                except Exception:
                    detail = str(response.raw.content)
            msg = (
                f"创建日程失败: code={response.code}, msg={response.msg}, "
                f"log_id={response.get_log_id()}"
            )
            if detail:
                msg = f"{msg}, detail={detail}"
            return CalendarCreateResult(False, msg)

        data_dict: dict = {}
        try:
            data_dict = json.loads(lark.JSON.marshal(response.data))
        except Exception:
            data_dict = {}

        event_id = (
            data_dict.get("event", {}).get("event_id", "")
            if isinstance(data_dict, dict)
            else ""
        )
        warning = ""
        attendee_open_ids = [x.strip() for x in (attendee_open_ids or []) if x and x.strip()]
        if event_id and attendee_open_ids:
            attendee_ok, attendee_msg = _create_feishu_event_attendees(
                client=client,
                calendar_id=calendar_id,
                event_id=event_id,
                user_id_type=user_id_type or "open_id",
                attendee_open_ids=attendee_open_ids,
                need_notification=attendee_need_notification,
            )
            if not attendee_ok:
                warning = attendee_msg

        return CalendarCreateResult(
            True,
            "创建日程成功",
            event_id=event_id,
            raw_data=data_dict,
            warning=warning,
        )
    except Exception as exc:
        return CalendarCreateResult(False, f"创建日程异常: {exc}")


def _create_feishu_event_attendees(
    client,
    calendar_id: str,
    event_id: str,
    user_id_type: str,
    attendee_open_ids: list[str],
    need_notification: bool,
) -> tuple[bool, str]:
    """为日程追加参与人。"""
    try:
        from lark_oapi.api.calendar.v4 import (
            CalendarEventAttendee,
            CreateCalendarEventAttendeeRequest,
            CreateCalendarEventAttendeeRequestBody,
        )

        attendees = [
            CalendarEventAttendee.builder()
            .type("user")
            .is_optional(False)
            .user_id(open_id)
            .operate_id(open_id)
            .build()
            for open_id in attendee_open_ids
        ]
        request = (
            CreateCalendarEventAttendeeRequest.builder()
            .calendar_id(calendar_id)
            .event_id(event_id)
            .user_id_type(user_id_type or "open_id")
            .request_body(
                CreateCalendarEventAttendeeRequestBody.builder()
                .attendees(attendees)
                .need_notification(bool(need_notification))
                .build()
            )
            .build()
        )
        response = client.calendar.v4.calendar_event_attendee.create(request)
        if not response.success():
            return (
                False,
                f"追加参与人失败: code={response.code}, msg={response.msg}, log_id={response.get_log_id()}",
            )
        return True, ""
    except Exception as exc:
        return False, f"追加参与人异常: {exc}"
