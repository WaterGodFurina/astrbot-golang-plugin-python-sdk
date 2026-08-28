"""AstrMessageEvent：Go 宿主兼容运行时的事件对象。

API 与 Python 本体 `astrbot.core.platform.AstrMessageEvent` 对齐。
"""
from __future__ import annotations

import abc
import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncGenerator
from time import time
from typing import Any

from astrbot.core.conversation_mgr import Conversation
from astrbot.core.message.components import (
    At,
    AtAll,
    BaseMessageComponent,
    Face,
    Forward,
    Image,
    Plain,
    Reply,
    Unknown,
)
from astrbot.core.message.message_event_result import (
    MessageChain,
    MessageEventResult,
)
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.message_session import MessageSession, MessageSesion  # noqa: F401
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.utils.deprecation import deprecated
from astrbot.core.utils.metrics import Metric
from astrbot.core.utils.trace import TraceSpan

from .astrbot_message import AstrBotMessage, Group, MessageMember
from .platform_metadata import PlatformMetadata

logger = logging.getLogger("astrbot")

# 兼容旧 SDK 的占位类名：统一指向 utils.trace.TraceSpan 简化实现
TraceSpanPlaceholder = TraceSpan


class AstrMessageEvent(abc.ABC):
    def __init__(
        self,
        message_str: str,
        message_obj: AstrBotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
    ) -> None:
        self.message_str = message_str
        self.message_obj = message_obj
        self.platform_meta = platform_meta
        self.role = "member"
        self.is_wake = False
        self.is_at_or_wake_command = False
        self._extras: dict[str, Any] = {}
        self._force_stopped: bool = False

        message_type = getattr(message_obj, "type", None)
        if not isinstance(message_type, MessageType):
            try:
                message_type = MessageType(str(message_type))
            except (ValueError, TypeError, AttributeError):
                logger.warning(
                    f"Failed to convert message type {message_obj.type!r} to MessageType. "
                    f"Falling back to FRIEND_MESSAGE."
                )
                message_type = MessageType.FRIEND_MESSAGE
        self.session = MessageSession(
            platform_name=platform_meta.id,
            message_type=message_type,
            session_id=session_id,
        )
        self._result: MessageEventResult | None = None
        self.created_at = time()
        self.trace = TraceSpan(
            name="AstrMessageEvent",
            umo=self.unified_msg_origin,
            sender_name=self.get_sender_name(),
            message_outline=self.get_message_outline(),
        )
        """用于记录事件处理的 TraceSpan（SDK 为轻量实现，不真正上报）"""
        self.span = self.trace
        """事件级 TraceSpan（别名: span）"""
        self._temporary_local_files: list[str] = []
        """本次事件期间创建的临时本地文件列表，事件结束时安全删除"""
        self._has_send_oper = False
        self.call_llm = False
        self.plugins_name: list[str] | None = None
        self.platform = platform_meta

    @property
    def unified_msg_origin(self) -> str:
        return str(self.session)

    @unified_msg_origin.setter
    def unified_msg_origin(self, value: str) -> None:
        if isinstance(value, str):
            try:
                self.session = MessageSession.from_str(value)
            except Exception as e:
                logger.warning(f"unified_msg_origin 解析失败: {e}")

    @property
    def session_id(self) -> str:
        return self.session.session_id

    @session_id.setter
    def session_id(self, value: str) -> None:
        self.session.session_id = value

    def get_platform_name(self):
        return self.platform_meta.name

    def get_platform_id(self):
        return self.platform_meta.id

    def get_message_str(self) -> str:
        return self.message_str

    def _outline_chain(self, chain: list[BaseMessageComponent] | None) -> str:
        if not chain:
            return ""

        parts = []
        for i in chain:
            if isinstance(i, Plain):
                parts.append(i.text)
            elif isinstance(i, Image):
                parts.append("[图片]")
            elif isinstance(i, Face):
                parts.append(f"[表情:{i.id}]")
            elif isinstance(i, At):
                parts.append(f"[At:{i.qq}]")
            elif isinstance(i, AtAll):
                parts.append("[At:全体成员]")
            elif isinstance(i, Forward):
                # 转发消息
                parts.append("[转发消息]")
            elif isinstance(i, Reply):
                # 引用回复
                if i.message_str:
                    parts.append(f"[引用消息({i.sender_nickname}: {i.message_str})]")
                else:
                    parts.append("[引用消息]")
            else:
                parts.append(f"[{i.type}]")
        return " ".join(parts)

    def get_message_outline(self) -> str:
        return self._outline_chain(self.get_messages())

    def get_messages(self) -> list[BaseMessageComponent]:
        if self.message_obj:
            return self.message_obj.message
        return []

    def get_message_type(self) -> MessageType:
        return self.session.message_type

    def get_session_id(self) -> str:
        return self.session.session_id

    def get_group_id(self) -> str:
        if self.message_obj and self.message_obj.group:
            return self.message_obj.group.group_id
        return ""

    def get_self_id(self) -> str:
        return self.message_obj.self_id if self.message_obj else ""

    def get_sender_id(self) -> str:
        if self.message_obj and self.message_obj.sender:
            return self.message_obj.sender.user_id
        return ""

    def get_sender_name(self) -> str:
        if self.message_obj and self.message_obj.sender:
            return self.message_obj.sender.nickname or ""
        return ""

    def set_extra(self, key, value) -> None:
        self._extras[key] = value

    def get_extra(self, key: str | None = None, default=None) -> Any:
        if key is None:
            return self._extras
        return self._extras.get(key, default)

    def clear_extra(self) -> None:
        self._extras.clear()

    def track_temporary_local_file(self, path: str) -> None:
        """登记一个临时本地文件，事件结束后由 cleanup_temporary_local_files
        统一清理（去重登记）。"""
        if path and path not in self._temporary_local_files:
            self._temporary_local_files.append(path)

    def cleanup_temporary_local_files(self) -> None:
        """删除本次事件登记的全部临时本地文件（存在才删，忽略删除错误）。"""
        paths = list(self._temporary_local_files)
        self._temporary_local_files.clear()
        for path in paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as e:
                logger.warning(
                    "Failed to remove temporary local file %s: %s",
                    path,
                    e,
                )

    def is_private_chat(self) -> bool:
        return self.session.message_type == MessageType.FRIEND_MESSAGE

    def is_wake_up(self) -> bool:
        return self.is_wake

    def is_admin(self) -> bool:
        return self.role == "admin"

    async def process_buffer(self, buffer: str, pattern: re.Pattern) -> str:
        """将消息缓冲区中的文本按指定正则表达式分割后逐段发送至消息平台，
        作为不支持流式输出平台的 Fallback（对齐本体：间隔 1.5s 限速）。
        返回剩余未匹配的缓冲区文本。
        """
        while True:
            match = re.search(pattern, buffer)
            if not match:
                break
            matched_text = match.group().strip()
            if matched_text:
                await self.send(MessageChain([Plain(matched_text)]))
                await asyncio.sleep(1.5)  # 限速
            buffer = buffer[match.end() :]
        return buffer

    async def send_streaming(
        self,
        generator: AsyncGenerator[MessageChain, None],
        use_fallback: bool = False,
    ) -> None:
        """发送流式消息到消息平台（基类实现），使用异步生成器。

        - use_fallback=False：遍历生成器，逐段调用 send 发送。
        - use_fallback=True：不支持原生流式输入的平台——聚合缓冲，借
          process_buffer 按标点切分发送。

        对齐本体 280-292 行语义：发送操作标记 _has_send_oper（阻止管线
        继续走默认 LLM）。aiocqhttp 等平台子类可覆盖本方法提供原生流式
        输入。
        """
        self._has_send_oper = True
        asyncio.create_task(
            Metric.upload(msg_event_tick=1, adapter_name=self.platform_meta.name),
        )
        if not use_fallback:
            async for chain in generator:
                await self.send(chain)
            return

        buffer = ""
        pattern = re.compile(r"[^。？！~…]+[。？！~…]+")

        async for chain in generator:
            if isinstance(chain, MessageChain):
                for comp in chain.chain:
                    if isinstance(comp, Plain):
                        buffer += comp.text
                        if any(p in buffer for p in "。？！~…"):
                            buffer = await self.process_buffer(buffer, pattern)
                    else:
                        await self.send(MessageChain(chain=[comp]))
                        await asyncio.sleep(1.5)  # 限速

        buffer = buffer.strip()
        if buffer:
            await self.send(MessageChain([Plain(buffer)]))

    async def send_typing(self) -> None:
        """发送输入中状态。

        默认实现为空，由具体平台按需重写（对齐本体基类 no-op）。
        """

    async def stop_typing(self) -> None:
        """停止输入中状态。

        默认实现为空，由具体平台按需重写（对齐本体基类 no-op）。
        """

    # 已废弃（v3.5.18 起消息调度器不再调用这两个钩子，保留空实现兼容）。
    @deprecated(version="3.5.18", reason="No longer invoked by the message scheduler.")
    async def _pre_send(self) -> None:
        """调度器会在执行 send() 前调用该方法（已废弃，保留空实现兼容）。"""

    @deprecated(version="3.5.18", reason="No longer invoked by the message scheduler.")
    async def _post_send(self) -> None:
        """调度器会在执行 send() 后调用该方法（已废弃，保留空实现兼容）。"""

    def set_result(self, result: MessageEventResult | str) -> None:
        if isinstance(result, str):
            result = MessageEventResult().message(result)
        # 兼容外部插件或调用方传入的 chain=None 的情况，确保为可迭代列表
        if isinstance(result, MessageEventResult) and result.chain is None:
            result.chain = []
        self._result = result

    def stop_event(self) -> None:
        """停止事件处理（对齐 Python 本体语义：强制停止标记 + 已有 result 置 STOP）。

        注意：MessageEventResult 没有 STOP 属性（STOP 在 EventResultType 枚举上），
        必须走 result.stop_event()。
        """
        self._force_stopped = True
        if self._result:
            self._result.stop_event()

    def continue_event(self) -> None:
        self._force_stopped = False
        if self._result:
            self._result.continue_event()

    def is_stopped(self) -> bool:
        if self._force_stopped:
            return True
        if self._result:
            return self._result.is_stopped()
        return False

    def should_call_llm(self, call_llm: bool) -> None:
        self.call_llm = call_llm

    def get_result(self) -> MessageEventResult | None:
        return self._result

    def clear_result(self) -> None:
        self._result = None

    def make_result(self) -> MessageEventResult:
        """创建一个空的消息事件结果（对齐本体：不写入 self._result，
        只返回新实例）。"""
        return MessageEventResult()

    def plain_result(self, text: str) -> MessageEventResult:
        return MessageEventResult().message(text)

    def image_result(self, url_or_path: str) -> MessageEventResult:
        from astrbot.core.message.components import Image

        if url_or_path.startswith("http"):
            return MessageEventResult().url_image(url_or_path)
        return MessageEventResult().file_image(url_or_path)

    def chain_result(self, chain: list[BaseMessageComponent]) -> MessageEventResult:
        return MessageEventResult(chain)

    def request_llm(
        self,
        prompt: str,
        func_tool_manager=None,
        tool_set=None,
        session_id: str = "",
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        contexts: list | None = None,
        system_prompt: str = "",
        conversation: Conversation | None = None,
    ):
        """创建 LLM 请求。Go 宿主桥：返回的 ProviderRequest 会以
        llm_continue 方式反馈宿主继续走默认 LLM（提示词为本消息原文）。"""
        if image_urls is None:
            image_urls = []
        if audio_urls is None:
            audio_urls = []
        if contexts is None:
            contexts = []
        return ProviderRequest(
            prompt=prompt,
            session_id=session_id,
            image_urls=image_urls,
            audio_urls=audio_urls,
            func_tool=tool_set,
            contexts=contexts,
            system_prompt=system_prompt,
            conversation=conversation,
        )

    async def send(self, message: MessageChain) -> None:
        """发送消息到消息平台（经宿主 HostService.SendMessage）。

        对齐 Python 本体语义：发送成功才标记 _has_send_oper——管线的 LLM 决策
        （process_stage）依据该标记：handler/钩子主动发送过的事件不再继续
        走 LLM（否则 box 这类"主动发图回复"的插件命令会再生成一遍 LLM
        回复）。
        """
        from astrbot.core.star.context import get_host_bridge

        bridge = get_host_bridge()
        if bridge is None:
            logger.warning("send(): 宿主桥未就绪，消息未发送")
            return
        # send_message 是同步 RPC（grpc-python 阻塞调用），经 host.py 的
        # send_message_async（asyncio.to_thread 包装）移出事件循环，避免
        # 数百 ms 的 gRPC 往返冻结所有 async handler。
        ok = await bridge.send_message_async(self.session, message)
        if not ok:
            # 发送失败不标记"已发送"，管线 LLM 兜底才能给用户回复
            logger.warning("send(): 宿主发送消息失败，未标记已发送")
            return
        self._has_send_oper = True
        asyncio.create_task(
            Metric.upload(msg_event_tick=1, adapter_name=self.platform_meta.name),
        )

    async def react(self, emoji: str) -> None:
        """对当前消息添加表情回应（宿主 PlatformManager.React，平台不支持时
        返回失败并告警）。"""
        from astrbot.core.star.context import get_host_bridge

        bridge = get_host_bridge()
        if bridge is None:
            logger.warning("react(): 宿主桥未就绪")
            return
        try:
            ok = await bridge.react_message(self, emoji)
            if not ok:
                logger.warning(f"react(): 平台不支持表情回应或消息不存在（emoji={emoji}）")
        except Exception as e:
            logger.warning(f"react() 失败: {e}")

    async def get_group(self, group_id: str | None = None, **kwargs) -> Group | None:
        """获取群信息（经宿主 CallAction；平台不支持时返回 None）。"""
        from astrbot.core.star.context import get_host_bridge

        bridge = get_host_bridge()
        if bridge is None:
            return None
        gid = group_id or self.get_group_id()
        if not gid:
            return None
        try:
            info = await bridge.call_action_async(
                self.session.platform_id, "get_group_info", {"group_id": int(gid) if str(gid).isdigit() else gid}
            )
            if not info:
                return None
            return Group(
                group_id=str(info.get("group_id", gid)),
                group_name=info.get("group_name") or info.get("name") or "",
            )
        except Exception as e:
            logger.warning(f"get_group({gid}) 失败: {e}")
            return None

    @classmethod
    def from_event_json(cls, data: dict) -> "AstrMessageEvent":
        """从宿主 sdk.Event JSON 重建事件对象。"""
        from astrbot.core.message.components import ComponentType
        from astrbot._bridge.serialize import component_from_json

        platform = data.get("platform", "")
        # 三段式 unified_msg_origin 的第一段优先取平台实例 ID（platform_id，
        # 宿主 sdk.Event 已补齐），缺失时回退平台类型名（platform）保持兼容。
        platform_id = data.get("platform_id") or platform
        self_id = data.get("self_id", "")
        sender_id = data.get("sender_id", "")
        sender_name = data.get("sender_name", "")
        conv_id = data.get("conv_id", "")
        is_group = data.get("is_group", False)
        is_at_bot = data.get("is_at_bot", False)
        is_admin = data.get("is_admin", False)
        message_str = data.get("message_str", "")
        plain_text = data.get("plain_text", "")
        raw_message = data.get("raw_message")
        message_id = data.get("message_id", "")
        timestamp = data.get("timestamp", 0)
        metadata = data.get("metadata") or {}

        # 消息类型优先取宿主传的驼峰值（GroupMessage/FriendMessage/
        # OtherMessage），为空时按 is_group 判定。
        msg_type_str = data.get("message_type", "")
        if msg_type_str:
            try:
                msg_type = MessageType(msg_type_str)
            except ValueError:
                msg_type = MessageType.GROUP_MESSAGE if is_group else MessageType.FRIEND_MESSAGE
        else:
            msg_type = MessageType.GROUP_MESSAGE if is_group else MessageType.FRIEND_MESSAGE
        # 单个组件反序列化失败不应拖垮整条消息链：逐个 try/except，失败
        # 组件用 Unknown(text="") 兜底并告警。
        chain = []
        for c in (data.get("chain") or []):
            try:
                chain.append(component_from_json(c))
            except Exception as e:
                logger.warning(f"from_event_json: 组件反序列化失败，已用 Unknown 兜底: {e}")
                chain.append(Unknown(text=""))

        obj = AstrBotMessage()
        obj.type = msg_type
        obj.self_id = self_id
        obj.session_id = conv_id
        obj.message_id = message_id
        obj.sender = MessageMember(user_id=sender_id, nickname=sender_name)
        obj.message = chain
        obj.message_str = message_str
        obj.raw_message = raw_message
        # 对齐 Python 本体：raw_message 应为 OneBot 原始事件 dict（插件常
        # event.message_obj.raw_message.get("notice_type") 等直接当 dict 用，
        # 如 qqadmin 入群检测）。宿主经 gRPC 传的是 JSON 字符串，需解析；
        # 解析失败保留原值（插件侧 isinstance(dict) 兜底）。
        if isinstance(obj.raw_message, str) and obj.raw_message:
            try:
                parsed = json.loads(obj.raw_message)
                if isinstance(parsed, dict):
                    obj.raw_message = parsed
            except (ValueError, TypeError):
                pass
        obj.timestamp = timestamp or int(time())
        if is_group:
            obj.group = Group(group_id=conv_id)

        meta = PlatformMetadata(
            name=platform,
            description="",
            # id 作为 MessageSession 的 platform_id（三段式第一段），取实例
            # ID（platform_id）；仅当 platform_id 缺失时回退平台类型名。
            id=platform_id,
        )
        # 按平台还原对应的事件子类：aiocqhttp 插件（box 等）依赖
        # event.bot（CQHttp 实例，经 HostBridge.call_action 转发宿主）。
        # 若统一构造 AstrMessageEvent（无 bot 属性），插件的
        # event.bot.get_stranger_info(...) 会 AttributeError → 插件捕获
        # 后静默失败 → 命令落空走 LLM。
        # 注意：此处仍用 platform（平台类型名）判断，不要改为 platform_id。
        if platform == "aiocqhttp":
            from aiocqhttp import CQHttp
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                AiocqhttpMessageEvent,
            )

            # 复用单例 CQHttp（get_default_bot）：每事件 new 一个会让
            # aiocqhttp 注册表无限增长（内存泄漏），且 dispatch 会把事件
            # 分发给所有历史实例，handler 被重复调用 N 次。
            event = AiocqhttpMessageEvent(
                message_str or plain_text, obj, meta, conv_id, bot=CQHttp.get_default_bot()
            )
        else:
            event = cls(message_str or plain_text, obj, meta, conv_id)
        event.is_at_or_wake_command = metadata.get("is_at_or_wake_command", False)
        event.is_wake = metadata.get("is_wake", False)
        event.call_llm = bool(metadata.get("call_llm", False))
        # 宿主事件 JSON 有扩展角色字段（owner/群管理员等）时优先读取，否则
        # 二值化为 admin/member（缺失 owner 维度会导致 @filter.permission_type
        # (OWNER/GROUP_ADMIN) 权限位恒不匹配）。
        raw_role = (metadata or {}).get("role") or data.get("sender_role")
        if raw_role in ("admin", "owner", "member"):
            event.role = raw_role
        else:
            event.role = "admin" if is_admin else "member"
        event.is_at_bot = is_at_bot
        return event

    @classmethod
    def from_proto(cls, event_proto) -> "AstrMessageEvent":
        """P1：从 proto SDKEvent 直接重建事件（0 JSON wire）。

        固定字段走 protobuf 原生，components 走 proto Component，仅动态
        metadata 走 metadata_json（一次 JSON）。
        """
        import json as _json

        from astrbot._bridge.serialize import proto_to_component_list

        platform = event_proto.platform or ""
        platform_id = event_proto.platform_id or platform
        self_id = event_proto.self_id or ""
        sender_id = event_proto.sender_id or ""
        sender_name = event_proto.sender_name or ""
        conv_id = event_proto.conv_id or ""
        is_group = event_proto.is_group
        is_at_bot = event_proto.is_at_bot
        is_admin = event_proto.is_admin
        message_str = event_proto.message_str or ""
        plain_text = event_proto.plain_text or ""
        raw_message = event_proto.raw_message or ""
        message_id = event_proto.message_id or ""
        timestamp = event_proto.timestamp or 0
        metadata = {}
        if event_proto.metadata_json:
            try:
                metadata = _json.loads(bytes(event_proto.metadata_json).decode("utf-8", "replace"))
                if not isinstance(metadata, dict):
                    metadata = {}
            except (ValueError, TypeError):
                metadata = {}

        msg_type_str = event_proto.message_type or ""
        if msg_type_str:
            try:
                msg_type = MessageType(msg_type_str)
            except ValueError:
                msg_type = MessageType.GROUP_MESSAGE if is_group else MessageType.FRIEND_MESSAGE
        else:
            msg_type = MessageType.GROUP_MESSAGE if is_group else MessageType.FRIEND_MESSAGE

        chain = proto_to_component_list(event_proto.components)

        obj = AstrBotMessage()
        obj.type = msg_type
        obj.self_id = self_id
        obj.session_id = conv_id
        obj.message_id = message_id
        obj.sender = MessageMember(user_id=sender_id, nickname=sender_name)
        obj.message = chain
        obj.message_str = message_str
        obj.raw_message = raw_message
        if isinstance(obj.raw_message, str) and obj.raw_message:
            try:
                parsed = _json.loads(obj.raw_message)
                if isinstance(parsed, dict):
                    obj.raw_message = parsed
            except (ValueError, TypeError):
                pass
        obj.timestamp = timestamp or int(time())
        if is_group:
            obj.group = Group(group_id=conv_id)

        meta = PlatformMetadata(
            name=platform,
            description="",
            id=platform_id,
        )
        if platform == "aiocqhttp":
            from aiocqhttp import CQHttp
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                AiocqhttpMessageEvent,
            )

            event = AiocqhttpMessageEvent(
                message_str or plain_text, obj, meta, conv_id, bot=CQHttp.get_default_bot()
            )
        else:
            event = cls(message_str or plain_text, obj, meta, conv_id)
        event.is_at_or_wake_command = metadata.get("is_at_or_wake_command", False)
        event.is_wake = metadata.get("is_wake", False)
        event.call_llm = bool(metadata.get("call_llm", False))
        raw_role = (metadata or {}).get("role")
        if raw_role in ("admin", "owner", "member"):
            event.role = raw_role
        else:
            event.role = "admin" if is_admin else "member"
        event.is_at_bot = is_at_bot
        return event
