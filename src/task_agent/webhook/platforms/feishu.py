"""
飞书平台实现

使用飞书官方 SDK lark-oapi 实现消息收发
"""

import json
import logging
import time
from typing import Optional

from .base import Platform, MessageType

logger = logging.getLogger(__name__)


class FeishuPlatform(Platform):
    """
    飞书平台实现

    使用 lark-oapi SDK 处理：
    - access_token 自动获取和刷新
    - 消息发送
    - 事件解析
    """

    def __init__(self, app_id: str, app_secret: str):
        """
        初始化飞书平台

        Args:
            app_id: 飞书应用 ID
            app_secret: 飞书应用密钥
        """
        self.app_id = app_id
        self.app_secret = app_secret
        self._client = None
        self._chat_id: Optional[str] = None
        # 授权卡片模板：默认使用你提供的模板 ID；版本为空时由飞书使用最新版本
        self.auth_card_template_id = "AAq2kN1jyTHdr"
        self.auth_card_template_version_name = ""
        # 工作目录切换卡片模板
        self.workspace_card_template_id = "AAq23eC4R3QlX"
        self.workspace_card_template_version_name = ""

    @property
    def client(self):
        """延迟加载 SDK 客户端"""
        if self._client is None:
            try:
                import lark_oapi as lark

                self._client = (
                    lark.Client.builder()
                    .app_id(self.app_id)
                    .app_secret(self.app_secret)
                    .build()
                )
                logger.info("飞书 SDK 客户端初始化成功")
            except ImportError:
                logger.error("未安装 lark-oapi SDK，请运行: pip install lark-oapi")
                raise
        return self._client

    def verify_signature(
        self, payload: bytes, signature: str, timestamp: str
    ) -> bool:
        """
        验证飞书签名

        非加密模式下可跳过验证，SDK 内置处理
        """
        # SDK 会自动处理签名验证，这里返回 True
        return True

    def parse_incoming_message(self, data: dict) -> Optional[str]:
        """
        解析飞书消息事件

        Args:
            data: 飞书事件回调数据

        Returns:
            用户消息内容
        """
        try:
            # 飞书事件结构
            event = data.get("event", {})

            # 消息接收事件
            if event.get("type") == "message":
                content_json = event.get("content", "{}")
                if isinstance(content_json, str):
                    content = json.loads(content_json)
                else:
                    content = content_json

                text = content.get("text", "")
                # 保存 chat_id
                self._chat_id = event.get("message", {}).get("chat_id")

                # 去除 @机器人 提及（飞书格式：<at user_id="xxx">xxx</at>）
                # 简单处理：去除 <at> 标签
                import re

                text = re.sub(r'<at[^>]*>.*?</at>', "", text).strip()

                if text.startswith("/"):
                    return text[1:].strip()  # 去掉斜杠命令
                return text

            return None
        except Exception as e:
            logger.error(f"解析飞书消息失败: {e}")
            return None

    def get_chat_id(self, data: dict) -> Optional[str]:
        """
        从回调数据中提取会话 ID

        Args:
            data: 飞书事件回调数据

        Returns:
            chat_id: 会话 ID
        """
        if self._chat_id:
            return self._chat_id

        event = data.get("event", {})
        return event.get("message", {}).get("chat_id")

    def send_message(
        self,
        content: str,
        chat_id: str,
        chat_type: str = "p2p",
        message_id: str = "",
        msg_type: MessageType = MessageType.TEXT,
    ) -> str:
        """
        发送消息到飞书

        Args:
            content: 消息内容
            chat_id: 会话 ID
            chat_type: 会话类型 (p2p=私聊, group=群聊)
            message_id: 消息 ID (群聊回复时需要)
            msg_type: 消息类型

        Returns:
            message_id: 消息 ID
        """
        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
            from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

            if msg_type == MessageType.INTERACTIVE:
                # interactive 内容要求是卡片 JSON 字符串
                content_json = content
                msg_type_value = "interactive"
            else:
                content_json = json.dumps({"text": content})
                msg_type_value = "text"

            # 私聊用 create，群聊用 reply
            if chat_type == "p2p":
                # 私聊：使用 create API
                logger.info(
                    f"[DEBUG] 发送消息到私聊: chat_id={chat_id}, chars={len(content)}, content={content[:50]}..."
                )

                request = CreateMessageRequest.builder() \
                    .receive_id_type("chat_id") \
                    .request_body(CreateMessageRequestBody.builder()
                        .receive_id(chat_id)
                        .msg_type(msg_type_value)
                        .content(content_json)
                        .build()) \
                    .build()

                response = None
                last_error = None
                max_attempts = 1 if msg_type == MessageType.INTERACTIVE else 2
                for attempt in range(max_attempts):
                    try:
                        response = self.client.im.v1.message.create(request)
                        break
                    except Exception as e:
                        last_error = e
                        if attempt < max_attempts - 1:
                            logger.warning(f"[DEBUG] 飞书私聊发送失败，准备重试: {e}")
                            time.sleep(0.3)
                            continue
                        raise

                if response is None and last_error is not None:
                    raise last_error

                if not response.success():
                    logger.error(f"✗ 飞书消息发送失败: code={response.code}, msg={response.msg}")
                    return ""

                message_id_result = response.data.message_id
                logger.info(f"✓ 飞书私聊消息发送成功: message_id={message_id_result}")
                return message_id_result

            else:
                # 群聊：使用 reply API
                logger.info(
                    f"[DEBUG] 回复群聊消息: message_id={message_id}, chars={len(content)}, content={content[:50]}..."
                )

                if not message_id:
                    logger.error("✗ 群聊回复失败：缺少 message_id")
                    return ""

                request = ReplyMessageRequest.builder() \
                    .message_id(message_id) \
                    .request_body(ReplyMessageRequestBody.builder()
                        .content(content_json)
                        .msg_type(msg_type_value)
                        .build()) \
                    .build()

                response = None
                last_error = None
                max_attempts = 1 if msg_type == MessageType.INTERACTIVE else 2
                for attempt in range(max_attempts):
                    try:
                        response = self.client.im.v1.message.reply(request)
                        break
                    except Exception as e:
                        last_error = e
                        if attempt < max_attempts - 1:
                            logger.warning(f"[DEBUG] 飞书群聊回复失败，准备重试: {e}")
                            time.sleep(0.3)
                            continue
                        raise

                if response is None and last_error is not None:
                    raise last_error

                if not response.success():
                    logger.error(f"✗ 飞书群聊回复失败: code={response.code}, msg={response.msg}")
                    return ""

                message_id_result = response.data.message_id
                logger.info(f"✓ 飞书群聊回复成功: message_id={message_id_result}")
                return message_id_result

        except Exception as e:
            logger.error(f"✗ 发送飞书消息异常: {e}")
            import traceback
            traceback.print_exc()
            return ""

    def update_authorization_card_result(
        self,
        message_id: str,
        command_content: str,
        result_text: str,
    ) -> bool:
        """仅更新授权区域：保留命令展示区，按钮区域替换为结果文案。"""
        try:
            from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

            card = {
                "schema": "2.0",
                "config": {
                    "update_multi": True,
                    "style": {
                        "text_size": {
                            "normal_v2": {
                                "default": "normal",
                                "pc": "normal",
                                "mobile": "heading"
                            }
                        }
                    }
                },
                "body": {
                    "direction": "vertical",
                    "elements": [
                        {
                            "tag": "column_set",
                            "flex_mode": "stretch",
                            "background_style": "blue-50",
                            "horizontal_align": "left",
                            "columns": [
                                {
                                    "tag": "column",
                                    "width": "weighted",
                                    "elements": [
                                        {
                                            "tag": "markdown",
                                            "content": f"**待授权命令：**\n{command_content}",
                                            "text_align": "left",
                                            "text_size": "normal_v2"
                                        }
                                    ],
                                    "vertical_spacing": "8px",
                                    "horizontal_align": "left",
                                    "vertical_align": "top",
                                    "weight": 1
                                }
                            ],
                            "margin": "0px 0px 0px 0px"
                        },
                        {
                            "tag": "column_set",
                            "flex_mode": "stretch",
                            "horizontal_spacing": "8px",
                            "horizontal_align": "left",
                            "columns": [
                                {
                                    "tag": "column",
                                    "width": "weighted",
                                    "elements": [
                                        {
                                            "tag": "markdown",
                                            "content": result_text,
                                            "text_align": "left",
                                            "text_size": "normal_v2"
                                        }
                                    ],
                                    "vertical_spacing": "8px",
                                    "horizontal_align": "left",
                                    "vertical_align": "top",
                                    "weight": 1
                                }
                            ],
                            "margin": "0px 0px 0px 0px"
                        }
                    ]
                },
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": "应用授权请求"
                    },
                    "subtitle": {
                        "tag": "plain_text",
                        "content": ""
                    },
                    "template": "blue",
                    "padding": "12px 8px 12px 8px"
                }
            }

            content_json = json.dumps(card, ensure_ascii=False)
            request = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(content_json)
                    .build()
                )
                .build()
            )

            response = self.client.im.v1.message.patch(request)
            if not response.success():
                logger.error(
                    f"✗ 更新授权卡片失败: message_id={message_id}, code={response.code}, msg={response.msg}"
                )
                return False

            logger.info(f"✓ 更新授权卡片成功: message_id={message_id}")
            return True
        except Exception as e:
            logger.error(f"✗ 更新授权卡片异常: {e}")
            import traceback
            traceback.print_exc()
            return False

    def send_authorization_card(
        self,
        chat_id: str,
        chat_type: str = "p2p",
        message_id: str = "",
        command_content: str = "",
        input_content: str = "",
    ) -> str:
        """发送授权卡片消息（template 卡片）。"""
        data = {"template_id": self.auth_card_template_id}
        data["template_variable"] = {
            "content": command_content,
            "input_content": input_content,
        }
        if self.auth_card_template_version_name:
            data["template_version_name"] = self.auth_card_template_version_name

        card_payload = {"type": "template", "data": data}
        content = json.dumps(card_payload, ensure_ascii=False)
        logger.info(
            f"[DEBUG] 发送授权卡片: template_id={self.auth_card_template_id}, "
            f"template_version_name={self.auth_card_template_version_name or 'latest'}, "
            f"content={command_content[:100]}, input_content={input_content[:80]}"
        )
        return self.send_message(
            content=content,
            chat_id=chat_id,
            chat_type=chat_type,
            message_id=message_id,
            msg_type=MessageType.INTERACTIVE,
        )

    def send_workspace_selection_card(
        self,
        chat_id: str,
        chat_type: str = "p2p",
        message_id: str = "",
        dir_list: Optional[list] = None,
    ) -> str:
        """发送切换目录卡片（template 卡片）。"""
        data = {"template_id": self.workspace_card_template_id}
        data["template_variable"] = {
            "dir_list": dir_list or [],
        }
        if self.workspace_card_template_version_name:
            data["template_version_name"] = self.workspace_card_template_version_name

        card_payload = {"type": "template", "data": data}
        content = json.dumps(card_payload, ensure_ascii=False)
        logger.info(
            f"[DEBUG] 发送切换目录卡片: template_id={self.workspace_card_template_id}, "
            f"template_version_name={self.workspace_card_template_version_name or 'latest'}, "
            f"options={len(dir_list or [])}"
        )
        return self.send_message(
            content=content,
            chat_id=chat_id,
            chat_type=chat_type,
            message_id=message_id,
            msg_type=MessageType.INTERACTIVE,
        )

    def update_workspace_selection_card_result(
        self,
        message_id: str,
        result_text: str,
    ) -> bool:
        """将切换目录卡片更新为结果态，避免重复点击。"""
        try:
            from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

            card = {
                "schema": "2.0",
                "config": {"update_multi": True},
                "body": {
                    "direction": "vertical",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": result_text,
                            "text_align": "left",
                        }
                    ],
                },
                "header": {
                    "title": {"tag": "plain_text", "content": "切换目录"},
                    "subtitle": {"tag": "plain_text", "content": ""},
                    "template": "blue",
                },
            }

            content_json = json.dumps(card, ensure_ascii=False)
            request = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(content_json)
                    .build()
                )
                .build()
            )
            response = self.client.im.v1.message.patch(request)
            if not response.success():
                logger.error(
                    f"✗ 更新切换目录卡片失败: message_id={message_id}, code={response.code}, msg={response.msg}"
                )
                return False

            logger.info(f"✓ 更新切换目录卡片成功: message_id={message_id}")
            return True
        except Exception as e:
            logger.error(f"✗ 更新切换目录卡片异常: {e}")
            import traceback
            traceback.print_exc()
            return False

    def format_output(self, content: str, output_type: str) -> str:
        """
        格式化飞书输出

        Args:
            content: 内容
            output_type: 输出类型

        Returns:
            格式化后的内容
        """
        if output_type == "think":
            return f"💭 思考过程\n{content}"
        elif output_type == "ps_call":
            return f"🔧 执行命令\n```bash\n{content}\n```"
        elif output_type == "ps_call_result":
            return f"📤 命令结果\n```\n{content}\n```"
        elif output_type == "create_agent":
            return f"🤖 创建子 Agent\n{content}"
        elif output_type == "agent_complete":
            return f"✅ 任务完成\n{content}"
        else:
            return content

    def parse_callback_data(self, data: dict) -> Optional[dict]:
        """
        解析飞书交互卡片回调数据

        Args:
            data: 飞书事件回调数据

        Returns:
            解析后的回调数据
        """
        # TODO: 实现交互卡片回调解析
        return None
