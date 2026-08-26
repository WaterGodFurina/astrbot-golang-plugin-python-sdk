"""插件上下文（Go 宿主兼容运行时）。

Context 的方法经宿主 HostService RPC 反向调用实现；宿主桥由
`astrbot._bridge.host` 注入。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.message import Message
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import ToolSet
from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.conversation_mgr import ConversationManager
from astrbot.core.db import BaseDatabase
from astrbot.core.exceptions import ProviderNotFoundError
from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.persona_mgr import PersonaManager
from astrbot.core.platform import Platform
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.message_session import MessageSesion
from astrbot.core.platform.message_type import MessageType
from astrbot.core.provider.entities import LLMResponse, ProviderRequest, ProviderType
from astrbot.core.provider.func_tool_manager import FunctionTool, FunctionToolManager
from astrbot.core.provider.manager import ProviderManager
from astrbot.core.provider.provider import (
    EmbeddingProvider,
    Provider,
    RerankProvider,
    STTProvider,
    TTSProvider,
)
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.platform_adapter_type import (
    ADAPTER_NAME_2_TYPE,
    PlatformAdapterType,
)
from astrbot.core.star.filter.regex import RegexFilter
from astrbot.core.star.star import StarMetadata, star_map, star_registry
from astrbot.core.star.star_handler import (
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)
from astrbot.core.subagent_orchestrator import SubAgentOrchestrator
from astrbot.core.utils.astrbot_path import get_astrbot_system_tmp_path
from astrbot.core.utils.deprecation import deprecated
from astrbot.core.utils.message_history_manager import (
    PlatformMessageHistoryManager,
)

logger = logging.getLogger("astrbot")

# 类型别名（对齐原版 context.py：WebApiHandler 为异步视图函数类型，
# RegisteredWebApi 为 (route, handler, methods, desc) 四元组）
WebApiHandler = Callable[..., Awaitable[Any]]
RegisteredWebApi = tuple[str, WebApiHandler, list[str], str]

# 插件模块路径中标识插件根目录的段
_PLUGIN_MODULE_FLAGS = {"builtin_stars", "plugins"}

_host_bridge: Any = None


def set_host_bridge(bridge) -> None:
    global _host_bridge
    _host_bridge = bridge


def get_host_bridge():
    return _host_bridge


class PlatformManagerProtocol(Protocol):
    """平台管理器协议（对齐原版 context.py 的 Protocol 标注）。"""

    platform_insts: list[Platform]
    get_insts: Callable[[], list[Platform]]


# ── 插件模块路径解析工具（对齐原版 context.py 模块级函数）───────────────
def _split_module_path(module_path: Any) -> list[str]:
    """拆分 'a.b.c' → ['a', 'b', 'c']（非字符串/空串返回 []）。"""
    if not isinstance(module_path, str) or not module_path:
        return []
    return module_path.split(".")


def _plugin_root_from_module_parts(parts: list[str]) -> tuple[str, str] | None:
    """在模块段中找到插件根标记（builtin_stars/plugins）并返回 (flag, 根目录名)。"""
    for index, part in enumerate(parts):
        if part in _PLUGIN_MODULE_FLAGS and index + 1 < len(parts):
            return part, parts[index + 1]
    return None


def _plugin_root_from_metadata(metadata: StarMetadata) -> str | None:
    """从插件元数据解析插件根目录名（优先 root_dir_name）。"""
    if metadata.root_dir_name:
        return metadata.root_dir_name
    root_info = _plugin_root_from_module_parts(_split_module_path(metadata.module_path))
    return root_info[1] if root_info else None


def _registered_plugin_module_path(root_dir_name: str, flag: str | None) -> str | None:
    """按根目录名反查已注册插件的模块路径。"""
    for metadata in reversed(star_registry):
        if not metadata.module_path:
            continue
        if _plugin_root_from_metadata(metadata) != root_dir_name:
            continue
        if flag and flag not in _split_module_path(metadata.module_path):
            continue
        return metadata.module_path
    return None


def _legacy_plugin_module_path(parts: list[str]) -> str:
    """将 'a.plugins.b.c' 风格的模块段解析为 'a.plugins.b.main'。"""
    resolved_parts = []
    for index, part in enumerate(parts):
        resolved_parts.append(part)
        if part in _PLUGIN_MODULE_FLAGS and index + 1 < len(parts):
            resolved_parts.append(parts[index + 1])
            resolved_parts.append("main")
            break
    return ".".join(resolved_parts)


def _resolve_tool_handler_module_path(tool: FunctionTool) -> str:
    """解析 LLM 工具的宿主模块路径（对齐原版语义，SDK 简化实现）。"""
    module_path = getattr(tool, "__module__", None)
    module_parts = _split_module_path(module_path)
    if not module_parts:
        return module_path if isinstance(module_path, str) else ""
    root_info = _plugin_root_from_module_parts(module_parts)
    if root_info:
        flag, root_dir_name = root_info
        registered_module_path = _registered_plugin_module_path(root_dir_name, flag)
        return registered_module_path or _legacy_plugin_module_path(module_parts)
    registered_module_path = _registered_plugin_module_path(module_parts[0], "plugins")
    return registered_module_path or ".".join(module_parts)


class _PlatformBotProxy:
    """跨进程 bot 客户端代理（Go 宿主无 Python 平台对象）。

    提供插件 OneBot 适配器期望的 async call_action：转发宿主
    HostService.CallAction（按平台实例 ID/类型解析适配器）。
    """

    def __init__(self, platform_id: str) -> None:
        self._platform_id = platform_id

    async def call_action(self, api: str, **params) -> Any:
        bridge = get_host_bridge()
        if bridge is None or not bridge.ensure_connected():
            raise RuntimeError("宿主桥未就绪，无法调用平台 API")
        return await bridge.call_action_async(self._platform_id, api, params)


class _PlatformMetaStub:
    """平台元数据占位（对齐本体 PlatformMetadata 的 id/type/name 访问）。"""

    def __init__(self, meta: dict) -> None:
        self.id: str = str(meta.get("id") or "")
        self.type: str = str(meta.get("type") or "")
        self.name: str = str(meta.get("name") or meta.get("type") or "")
        self.adapter_display_name: str = str(meta.get("display_name") or "")
        self.description: str = str(meta.get("description") or "")
        self.support_streaming_message: bool = False
        self.support_proactive_message: bool = False

    @property
    def adapter_name(self) -> str:
        return self.name


class _PlatformStub:
    """宿主平台实例占位：metadata + config + bot 代理。

    群分析类插件经 context.platform_manager.get_insts() 遍历平台时读取
    metadata.id/type/name 与 bot（call_action 转发宿主），使"检测平台"
    不再恒为 0（对齐原版同进程平台实例在外观上的最小协议）。
    """

    def __init__(self, meta: dict) -> None:
        self._meta = _PlatformMetaStub(meta)
        self._bot = _PlatformBotProxy(self._meta.id)
        self.config: dict = {}
        cfg = meta.get("config")
        if isinstance(cfg, dict):
            self.config = cfg
        # 兼容属性：lark_api / get_client() / bot / client（原版 Platform 形态）。
        self.lark_api = None
        self.bot = self._bot
        self.client = self._bot

    def meta(self) -> _PlatformMetaStub:
        return self._meta

    @property
    def metadata(self) -> _PlatformMetaStub:
        return self._meta

    def get_client(self) -> _PlatformBotProxy:
        return self._bot

    def get_stats(self) -> dict:
        meta = self._meta
        return {
            "id": meta.id,
            "type": meta.type,
            "display_name": meta.adapter_display_name or meta.name,
            "status": "RUNNING",
            "started_at": None,
            "error_count": 0,
            "last_error": None,
        }


class _PlatformManagerStub:
    """PlatformManager 兼容封装（Go 宿主跨进程）。

    从宿主 HostService.ListPlatforms 拉取已加载平台元数据，构造平台占位
    实例（metadata + bot 代理，call_action 转发宿主）。首次访问时惰性
    拉取并缓存：群分析类插件 get_insts() 能列出真实平台（不再恒为 0）。
    """

    def __init__(self) -> None:
        self.platform_insts: list[Any] = []
        self.platform_insts_map: dict[str, Any] = {}
        self._refreshed = False

    def _refresh(self) -> None:
        if self._refreshed:
            return
        self._refreshed = True
        bridge = get_host_bridge()
        if bridge is None or not bridge.ensure_connected():
            return
        try:
            raw = bridge.list_platforms()
        except Exception:
            return
        insts: list[_PlatformStub] = []
        for meta in raw:
            if not isinstance(meta, dict):
                continue
            stub = _PlatformStub(meta)
            insts.append(stub)
            if stub._meta.id:
                self.platform_insts_map[stub._meta.id] = stub
        self.platform_insts = insts

    def get_insts(self) -> list:
        """获取平台实例列表（从宿主惰性拉取，非消息路径）。"""
        self._refresh()
        return self.platform_insts

    def get_platform(self, platform_id: str) -> Any | None:
        """按平台 ID 获取实例（null 时取第一个）。"""
        self._refresh()
        if platform_id:
            return self.platform_insts_map.get(platform_id)
        return self.platform_insts[0] if self.platform_insts else None


class Context:
    """插件上下文：宿主能力代理。"""

    def __init__(self) -> None:
        self._config: AstrBotConfig | None = None
        self._config_loaded: bool = False
        self.plugin_name: str = ""
        self.plugin_id: str = ""
        # 平台管理器占位（对齐本体 Context.platform_manager 属性）
        self.platform_manager = _PlatformManagerStub()
        # 管理器（对齐 Python 本体 Context：provider_manager / persona_manager
        # / conversation_manager / persona_mgr（别名）/ _star_manager）
        from astrbot.core.conversation_mgr import ConversationManager
        from astrbot.core.persona_mgr import PersonaManager
        from astrbot.core.provider.manager import ProviderManager
        from astrbot.core.star.star_manager import PluginManager

        self.provider_manager = ProviderManager(self._bridge)
        self.persona_manager = PersonaManager(self._bridge)
        # persona_mgr 保留为 persona_manager 别名（插件两种写法都兼容）
        self.persona_mgr = self.persona_manager
        self.conversation_manager = ConversationManager(self._bridge)
        self._star_manager = PluginManager(context=self, bridge=self._bridge)
        # 消息历史管理器（JSON 文件持久化，对齐本体 Context.message_history_manager）
        from astrbot.core.utils.message_history_manager import (
            PlatformMessageHistoryManager,
        )

        self.message_history_manager = PlatformMessageHistoryManager()
        # 定时任务管理器（暴露 apscheduler 风格 .scheduler，对齐本体
        # Context.cron_manager，插件通过 context.cron_manager.scheduler 调度）
        from astrbot.core.utils.cron_manager import CronJobManager

        self.cron_manager = CronJobManager()
        # register_task 跟踪的任务（terminate 时取消）
        self._tasks: list[Any] = []
        # register_web_api 注册的 Web API：[(route, handler, methods, desc)]
        self._web_apis: list[tuple] = []

    @property
    def registered_web_apis(self) -> list[tuple]:
        """已注册的 Web API 列表（对齐本体 Context.registered_web_apis）。

        插件可直接改写（如 `routes[:] = [...]` 清空重载）。
        """
        return self._web_apis

    @registered_web_apis.setter
    def registered_web_apis(self, value) -> None:
        """设置器：直接替换内部列表（兼容插件整表赋值）。"""
        self._web_apis = value

    def _bridge(self):
        if _host_bridge is None:
            raise RuntimeError("宿主桥未就绪（插件不在 Go 宿主子进程中运行？）")
        return _host_bridge

    def get_config(self, umo: str | None = None) -> AstrBotConfig:
        if umo:
            logger.warning("get_config(umo) 在 Go 宿主兼容运行时中忽略 umo，返回插件配置")
        # 区分"拉取成功（即使为空）"与"拉取失败"：成功即缓存（含空配置），
        # 失败不缓存，下次调用重试（插件启动早期 HostService 可能未就绪）。
        if self._config_loaded:
            return self._config or AstrBotConfig()
        try:
            bridge = self._bridge()
            data = bridge.get_config(self.plugin_name)
            if isinstance(data, dict):
                # 宿主返回的配置 dict 可能附带 __schema__（插件配置 JSON
                # Schema，宿主经 GetConfig hook 注入）。提取后传给
                # AstrBotConfig 使插件 self.config.schema 可访问。
                schema = data.pop("__schema__", None)
                self._config = AstrBotConfig(data, schema=schema)
                # 绑定宿主桥与插件名：config.save_config() 写回宿主需要
                self._config._bridge = bridge
                self._config._plugin_name = self.plugin_name
                self._config_loaded = True
                return self._config
        except Exception as e:
            logger.warning(f"get_config() 拉取配置失败: {e}")
        # 拉取失败也绑定宿主桥，保证 save_config 仍能写回宿主
        cfg = AstrBotConfig()
        try:
            cfg._bridge = self._bridge()
            cfg._plugin_name = self.plugin_name
        except Exception:
            pass
        return cfg

    # ── 插件（Star）管理 ───────────────────────────────────────────────────
    def get_all_stars(self) -> list:
        """获取当前宿主载入的所有插件元数据列表（同步）。

        插件侧同步遍历（for plugin in self.context.get_all_stars()），
        返回 StarInfo 对象（属性访问 plugin.name/plugin.author/...）。
        """
        from astrbot.core.star.star_manager import StarInfo

        try:
            raw = self._bridge().get_plugin_registry()
        except Exception as e:
            logger.warning(f"get_all_stars 失败: {e}")
            return []
        return [StarInfo(item) for item in raw if isinstance(item, dict)]

    def get_registered_star(self, star_name: str):
        """根据插件名获取插件元数据（同步；未找到返回 None）。"""
        from astrbot.core.star.star_manager import StarInfo

        try:
            data = self._bridge().get_star(star_name)
        except Exception as e:
            logger.warning(f"get_registered_star({star_name}) 失败: {e}")
            return None
        if not isinstance(data, dict) or not data:
            return None
        return StarInfo(data)

    # ── Provider 管理（同步接口：插件同步调用）──────────────────────────────
    def get_provider_by_id(self, provider_id: str):
        """通过 ID 获取对应的 Provider（同步；未找到返回 None）。"""
        try:
            return self.provider_manager._get_provider_by_id(provider_id)
        except Exception as e:
            logger.warning(f"get_provider_by_id({provider_id}) 失败: {e}")
            return None

    def get_all_providers(self) -> list:
        """获取所有用于文本生成任务的 LLM Provider（Chat_Completion 类型）。"""
        try:
            return self.provider_manager.provider_insts
        except Exception as e:
            logger.warning(f"get_all_providers 失败: {e}")
            return []

    def get_all_tts_providers(self) -> list:
        """获取所有用于 TTS 任务的 Provider。"""
        try:
            return self.provider_manager.tts_provider_insts
        except Exception as e:
            logger.warning(f"get_all_tts_providers 失败: {e}")
            return []

    def get_all_stt_providers(self) -> list:
        """获取所有用于 STT 任务的 Provider。"""
        try:
            return self.provider_manager.stt_provider_insts
        except Exception as e:
            logger.warning(f"get_all_stt_providers 失败: {e}")
            return []

    def get_all_embedding_providers(self) -> list:
        """获取所有用于 Embedding 任务的 Provider（同步，经宿主桥 list_providers）。"""
        try:
            return self.provider_manager.embedding_provider_insts
        except Exception as e:
            logger.warning(f"get_all_embedding_providers 失败: {e}")
            return []

    def get_using_provider(self, umo: str | None = None):
        """获取当前使用的 LLM Provider（同步；插件侧同步调用）。"""
        return self.provider_manager.get_using_provider(
            capability="chat_completion",
            umo=umo,
        )

    def get_using_tts_provider(self, umo: str | None = None):
        """获取当前使用的 TTS Provider（同步）。"""
        return self.provider_manager.get_using_provider(
            capability="text_to_speech",
            umo=umo,
        )

    def get_using_stt_provider(self, umo: str | None = None):
        """获取当前使用的 STT Provider（同步）。"""
        return self.provider_manager.get_using_provider(
            capability="speech_to_text",
            umo=umo,
        )

    async def get_using_provider_async(self, umo: str | None = None):
        """获取当前使用的 LLM Provider（异步版）。"""
        return await self.provider_manager.get_using_provider_async(
            capability="chat_completion",
            umo=umo,
        )

    async def get_using_tts_provider_async(self, umo: str | None = None):
        """获取当前使用的 TTS Provider（异步版）。"""
        return await self.provider_manager.get_using_provider_async(
            capability="text_to_speech",
            umo=umo,
        )

    async def get_using_stt_provider_async(self, umo: str | None = None):
        """获取当前使用的 STT Provider（异步版）。"""
        return await self.provider_manager.get_using_provider_async(
            capability="speech_to_text",
            umo=umo,
        )

    async def get_current_chat_provider_id(self, umo: str | None = None):
        """获取会话当前使用的 chat provider id（对齐本体同名方法）。

        Args:
            umo: unified_message_origin。消息会话来源 ID。

        Returns:
            指定会话当前使用的聊天 Provider 的 id；未找到返回 None
            （本体抛 ProviderNotFoundError，为兼容插件 try/except 返回 None）。
        """
        try:
            prov = await self.get_using_provider_async(umo)
        except Exception as e:
            logger.warning(f"get_current_chat_provider_id({umo}) 失败: {e}")
            return None
        if prov is None:
            return None
        return prov.meta().id

    async def send_message(self, session, message_chain) -> bool:
        """根据 session(unified_msg_origin) 主动发送消息。"""
        from astrbot.core.platform.message_session import MessageSession

        if isinstance(session, str):
            try:
                session = MessageSession.from_str(session)
            except BaseException as e:
                raise ValueError("不合法的 session 字符串: " + str(e))
        try:
            # 必须 await 真异步版本（host.send_message 是同步 RPC，直接
            # await 会抛 "object bool can't be used in 'await' expression"）
            await self._bridge().send_message_async(session, message_chain)
            return True
        except Exception as e:
            logger.warning(f"send_message 失败: {e}")
            return False

    async def chat_llm(
        self,
        prompt: str,
        system_prompt: str = "",
        image_urls: list[str] | None = None,
        session_id: str = "",
    ) -> str:
        return await self._bridge().chat_llm_async(prompt, system_prompt, image_urls, session_id)

    async def llm_generate(
        self,
        *,
        chat_provider_id: str,
        prompt: str | None = None,
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        tools=None,
        system_prompt: str | None = None,
        contexts: list | None = None,
        **kwargs,
    ) -> Any:
        """调用 LLM 生成回复（宿主 ChatLLM：默认 chat provider，支持图片）。"""
        from astrbot.core.provider.entities import LLMResponse

        if prompt is None:
            prompt = ""
        tools_json: str | None = None
        if tools:
            tool_defs = []
            for t in tools:
                name = getattr(t, "name", None)
                description = getattr(t, "description", None)
                parameters = getattr(t, "parameters", None)
                if name is None or description is None or parameters is None:
                    continue
                tool_defs.append(
                    {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": description,
                            "parameters": parameters,
                        },
                    }
                )
            if tool_defs:
                tools_json = json.dumps(tool_defs)
        contexts_json: str | None = None
        if contexts:
            contexts_json = json.dumps(contexts, ensure_ascii=False)
        text = await self._bridge().chat_llm_async(
            prompt,
            system_prompt or "",
            image_urls or [],
            session_id="",
            audio_urls=audio_urls or [],
            tools_json=tools_json,
            contexts_json=contexts_json,
            provider_id=chat_provider_id,
        )
        resp = LLMResponse(role="assistant")
        from astrbot.core.message.message_event_result import MessageChain
        from astrbot.core.message.components import Plain

        resp.result_chain = MessageChain([Plain(text)])
        return resp

    def get_llm_tool_manager(self):
        from astrbot.core.provider.func_tool_manager import llm_tools

        return llm_tools

    def activate_llm_tool(self, name: str) -> bool:
        from astrbot.core.provider.func_tool_manager import llm_tools

        return llm_tools.activate(name)

    async def activate_llm_tool_async(self, name: str) -> bool:
        return self.activate_llm_tool(name)

    def deactivate_llm_tool(self, name: str) -> bool:
        from astrbot.core.provider.func_tool_manager import llm_tools

        return llm_tools.deactivate(name)

    async def deactivate_llm_tool_async(self, name: str) -> bool:
        return self.deactivate_llm_tool(name)

    def register_task(self, task, desc: str) -> None:
        """登记插件任务（asyncio Task / awaitable），插件卸载（terminate）时取消。"""
        self._tasks.append(task)
        logger.info(f"register_task: {desc}")

    def cancel_all_tasks(self) -> None:
        """取消并清理全部登记任务（宿主卸载插件时调用）。"""
        import asyncio

        for task in self._tasks:
            if isinstance(task, asyncio.Task) and not task.done():
                task.cancel()
        self._tasks.clear()

    def get_event_queue(self):
        return None

    def get_platform(self, platform_type):
        return None

    def get_platform_inst(self, platform_id: str):
        return None

    def get_db(self):
        return None

    def register_provider(self, provider) -> None:
        pass

    def register_llm_tool(self, name: str, func_args: list, desc: str, handler) -> None:
        from astrbot.core.provider.func_tool_manager import llm_tools

        llm_tools.add_func(name, func_args, desc, handler)

    def add_llm_tools(self, *tools) -> None:
        """添加 LLM 工具（对齐 Python 原版 Context.add_llm_tools）。

        插件用 dataclass 子类化 FunctionTool 定义工具（name/description/
        parameters + async run(event, **kwargs)），例如 Bing 搜索插件：
            self.context.add_llm_tools(BingSearchTool(...), WebFetchTool(...))
        宿主管线（HandleTool）经 run 调用并把返回值转文本反馈给模型。
        """
        from astrbot.core.provider.func_tool_manager import FuncTool, llm_tools

        for tool in tools:
            name = str(getattr(tool, "name", "") or "")
            if not name:
                continue
            params = getattr(tool, "parameters", None) or {
                "type": "object",
                "properties": {},
            }
            desc = str(getattr(tool, "description", "") or "")
            handler = getattr(tool, "run", None)
            if handler is None:
                handler = getattr(tool, "call", None)
            if handler is None:
                handler = tool
            if not callable(handler):
                raise TypeError(f"LLM 工具 {name} 没有可调用的 run()/call() 实现")
            llm_tools.remove_func(name)
            llm_tools.func_list.append(
                FuncTool(name=name, parameters=params, description=desc, handler=handler)
            )
            logger.info(f"plugin added LLM tool: {name}")

    def unregister_llm_tool(self, name: str) -> None:
        from astrbot.core.provider.func_tool_manager import llm_tools

        llm_tools.remove_func(name)

    def register_commands(self, command: dict) -> None:
        pass

    def register_web_api(
        self,
        route: str,
        handler,
        methods: list | None = None,
        desc: str = "",
    ) -> None:
        """注册 Web API（宿主 /api/plug/<plugin>/<route> 网关转发到本插件）。

        route 支持动态段："/emoji/<category>"。handler 为 async 函数，路径参数
        按名解包调用；可用 astrbot.api.web.request 或 quart 全局 request。
        """
        route = route if route.startswith("/") else "/" + route
        methods = [m.upper() for m in (methods or ["GET"])]
        # 宿主经 ListWebApis RPC 实时拉取路由表（对齐 ListTools），Register
        # 之后注册的路由无需重启即可被宿主网关 /api/plug/<plugin>/<route> 转发。
        for idx, (r, _, m, _) in enumerate(self._web_apis):
            if r == route and m == methods:
                self._web_apis[idx] = (route, handler, methods, desc)
                return
        self._web_apis.append((route, handler, methods, desc))
        logger.info(f"register_web_api: {methods} {route} — {desc}")
