"""插件上下文（Go 宿主兼容运行时）。

Context 的方法经宿主 HostService RPC 反向调用实现；宿主桥由
`astrbot._bridge.host` 注入。
"""
from __future__ import annotations

import logging
from typing import Any

from astrbot.core.config.astrbot_config import AstrBotConfig

logger = logging.getLogger("astrbot")

_host_bridge: Any = None


def set_host_bridge(bridge) -> None:
    global _host_bridge
    _host_bridge = bridge


def get_host_bridge():
    return _host_bridge


class Context:
    """插件上下文：宿主能力代理。"""

    def __init__(self) -> None:
        self._config: AstrBotConfig | None = None
        self._config_loaded: bool = False
        self.plugin_name: str = ""
        self.plugin_id: str = ""
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
        # register_task 跟踪的任务（terminate 时取消）
        self._tasks: list[Any] = []
        # register_web_api 注册的 Web API：[(route, handler, methods, desc)]
        self._web_apis: list[tuple] = []

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
            data = self._bridge().get_config(self.plugin_name)
            if isinstance(data, dict):
                self._config = AstrBotConfig(data)
                self._config_loaded = True
                return self._config
        except Exception as e:
            logger.warning(f"get_config() 拉取配置失败: {e}")
        return AstrBotConfig()

    # ── 插件（Star）管理 ───────────────────────────────────────────────────
    def get_all_stars(self) -> list:
        """获取当前宿主载入的所有插件元数据列表（同步）。

        插件侧同步遍历（for plugin in self.context.get_all_stars()），
        返回 StarInfo 对象（属性访问 plugin.name/plugin.author/...）。
        """
        from astrbot.core.star.star_manager import StarInfo

        try:
            raw = self._bridge().list_stars()
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
        if contexts:
            last = contexts[-1] if isinstance(contexts[-1], dict) else None
            if last and last.get("content"):
                prompt = str(last["content"]) + "\n" + prompt
        text = await self._bridge().chat_llm_async(prompt, system_prompt or "", image_urls or [])
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
        for idx, (r, _, m, _) in enumerate(self._web_apis):
            if r == route and m == methods:
                self._web_apis[idx] = (route, handler, methods, desc)
                return
        self._web_apis.append((route, handler, methods, desc))
        logger.info(f"register_web_api: {methods} {route} — {desc}")
