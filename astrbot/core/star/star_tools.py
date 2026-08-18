"""Star 工具（对齐 Python 原版 v4.27.3 astrbot/core/star/star_tools.py）。

Go 宿主兼容运行时：经 StarTools.initialize 注入的 Context（模块级共享引用）
转发宿主能力；LLM 工具类方法直接转发 Context 对应方法。宿主无平台实例体系，
create_event 降级为 None + 日志。数据目录走宿主约定（get_data_dir）。
"""

from __future__ import annotations

import inspect
import logging
import os
import uuid
from pathlib import Path
from typing import ClassVar

from astrbot.core.message.components import BaseMessageComponent
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_session import MessageSesion
from astrbot.core.platform.message_type import MessageType
from astrbot.core.star.context import Context
from astrbot.core.star.star import star_map
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.core.utils.deprecation import deprecated
from astrbot.core.utils.io import ensure_dir

logger = logging.getLogger("astrbot")


class StarTools:
    """插件便捷工具（对齐 Python 原版签名，经 Context 转发宿主能力）。"""

    _context: ClassVar[Context | None] = None

    @classmethod
    def initialize(cls, context: Context) -> None:
        """保存 context 引用（模块级共享），供后续方法转发宿主能力。

        Args:
            context: 注入给插件的 Context（Go 宿主桥）。
        """
        cls._context = context

    @classmethod
    async def send_message(cls, session, message_chain: MessageChain) -> bool:
        """根据 session(unified_msg_origin) 主动发送消息。

        Args:
            session: 消息会话对象或 unified_msg_origin 字符串。
            message_chain: 要发送的消息链。

        Returns:
            是否发送成功。

        Raises:
            ValueError: StarTools 未初始化时抛出（对齐原版行为）。
        """
        if cls._context is None:
            raise ValueError("StarTools not initialized")
        return await cls._context.send_message(session, message_chain)

    @classmethod
    async def create_message(
        cls,
        type: str,
        self_id: str,
        session_id: str,
        sender: MessageMember,
        message: list,
        message_str: str,
        message_id: str = "",
        raw_message: object = None,
        group_id: str = "",
    ) -> AstrBotMessage:
        """创建 AstrBotMessage 消息对象（对齐原版签名）。

        注意：Python 原版返回的是 AstrBotMessage（含 message 组件链），
        而非 MessageChain；SDK 保持对齐。

        Args:
            type: 消息类型，如 "GroupMessage"、"FriendMessage"、"OtherMessage"。
            self_id: 机器人自身 ID。
            session_id: 会话 ID，通常是用户 ID 或群 ID。
            sender: 发送者信息，如 MessageMember(user_id="123456", nickname="昵称")。
            message: 消息组件列表。
            message_str: 发送给 LLM 的纯文本消息。
            message_id: 消息 ID；留空则自动生成。
            raw_message: 原始消息对象。
            group_id: 群 ID；私聊为空。

        Returns:
            组装好的 AstrBotMessage 消息对象。
        """
        abm = AstrBotMessage()
        abm.type = MessageType(type)
        abm.self_id = self_id
        abm.session_id = session_id
        if message_id == "":
            message_id = uuid.uuid4().hex
        abm.message_id = message_id
        abm.sender = sender
        abm.message = message
        abm.message_str = message_str
        abm.raw_message = raw_message
        abm.group_id = group_id
        return abm

    @classmethod
    async def create_event(
        cls,
        abm: AstrBotMessage,
        platform: str = "aiocqhttp",
        is_wake: bool = True,
    ) -> None:
        """创建并提交事件到目标平台（对齐原版签名 create_event(abm, platform, is_wake)）。

        Go 宿主兼容运行时：宿主无平台实例体系（平台实例在 Go 侧），无法构造
        AstrMessageEvent 并提交——**降级：返回 None 并日志**，不再抛异常。

        Args:
            abm: 待提交的消息对象（可先用 create_message 创建）。
            platform: 平台 ID 或适配器名（原版默认 aiocqhttp，SDK 仅占位）。
            is_wake: 是否为唤醒事件（SDK 仅占位）。
        """
        logger.warning(
            "create_event 未实现：Go 宿主无平台实例体系，事件未提交"
            "（platform=%s, is_wake=%s）",
            platform,
            is_wake,
        )
        return None

    @classmethod
    def activate_llm_tool(cls, name: str) -> bool:
        """激活一个已注册的函数调用工具（同步，转发 Context）。

        Args:
            name: 工具名。

        Returns:
            是否激活成功。

        Raises:
            ValueError: StarTools 未初始化时抛出（对齐原版行为）。
        """
        if cls._context is None:
            raise ValueError("StarTools not initialized")
        return cls._context.activate_llm_tool(name)

    @classmethod
    async def activate_llm_tool_async(cls, name: str) -> bool:
        """激活一个已注册的函数调用工具（异步，转发 Context）。"""
        if cls._context is None:
            raise ValueError("StarTools not initialized")
        return await cls._context.activate_llm_tool_async(name)

    @classmethod
    def deactivate_llm_tool(cls, name: str) -> bool:
        """停用一个已注册的函数调用工具（同步，转发 Context）。"""
        if cls._context is None:
            raise ValueError("StarTools not initialized")
        return cls._context.deactivate_llm_tool(name)

    @classmethod
    async def deactivate_llm_tool_async(cls, name: str) -> bool:
        """停用一个已注册的函数调用工具（异步，转发 Context）。"""
        if cls._context is None:
            raise ValueError("StarTools not initialized")
        return await cls._context.deactivate_llm_tool_async(name)

    @classmethod
    def register_llm_tool(cls, name: str, func_args: list, desc: str, handler) -> None:
        """注册一个函数调用工具（转发 Context）。

        Args:
            name: 工具名。
            func_args: 函数参数定义。
            desc: 工具描述。
            handler: 处理函数（须为异步）。
        """
        if cls._context is None:
            raise ValueError("StarTools not initialized")
        cls._context.register_llm_tool(name, func_args, desc, handler)

    @classmethod
    def unregister_llm_tool(cls, name: str) -> None:
        """注销一个函数调用工具（转发 Context）。

        Args:
            name: 工具名。
        """
        if cls._context is None:
            raise ValueError("StarTools not initialized")
        cls._context.unregister_llm_tool(name)

    @classmethod
    def get_data_dir(cls, plugin_name: str | None = None) -> Path:
        """返回插件数据目录（绝对路径，自动创建）。

        宿主约定：<data>/plugins_data/<plugin_name>（ASTRBOT_PLUGIN_DATA_DIR，
        与 Go 插件的统一数据根一致，卸载时可整体清理）。plugin_name 缺省时
        从调用栈解析调用者插件名（对齐 Python 本体语义）。
        """
        if not plugin_name:
            frame = inspect.currentframe()
            module = None
            if frame:
                frame = frame.f_back
                module = inspect.getmodule(frame)
            if module is not None:
                metadata = star_map.get(module.__name__)
                if metadata is not None:
                    plugin_name = metadata.name
        if not plugin_name:
            raise ValueError("无法解析插件名")

        base = os.environ.get("ASTRBOT_PLUGIN_DATA_DIR")
        if not base:
            base = os.path.join(os.environ.get("ASTRBOT_DATA_PATH", "data"), "plugins_data")
        data_dir = Path(base) / plugin_name
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise RuntimeError(f"创建插件数据目录失败: {e}") from e
        return data_dir
