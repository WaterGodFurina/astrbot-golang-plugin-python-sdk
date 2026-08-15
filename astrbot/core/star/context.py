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
        self.plugin_name: str = ""
        self.plugin_id: str = ""

    def _bridge(self):
        if _host_bridge is None:
            raise RuntimeError("宿主桥未就绪（插件不在 Go 宿主子进程中运行？）")
        return _host_bridge

    def get_config(self, umo: str | None = None) -> AstrBotConfig:
        if umo:
            logger.warning("get_config(umo) 在 Go 宿主兼容运行时中忽略 umo，返回插件配置")
        # 缓存只在成功拿到非空配置后生效：插件启动早期 HostService 可能尚未
        # 预连接完成，此时返回空配置并丢弃，下次调用重试。
        if self._config is not None and len(self._config) > 0:
            return self._config
        try:
            data = self._bridge().get_config(self.plugin_name)
            if isinstance(data, dict) and len(data) > 0:
                self._config = AstrBotConfig(data)
                return self._config
        except Exception as e:
            logger.warning(f"get_config() 拉取配置失败: {e}")
        return AstrBotConfig()

    async def send_message(self, session, message_chain) -> bool:
        """根据 session(unified_msg_origin) 主动发送消息。"""
        from astrbot.core.platform.message_session import MessageSession

        if isinstance(session, str):
            try:
                session = MessageSession.from_str(session)
            except BaseException as e:
                raise ValueError("不合法的 session 字符串: " + str(e))
        try:
            await self._bridge().send_message(session, message_chain)
            return True
        except Exception as e:
            logger.warning(f"send_message 失败: {e}")
            return False

    async def chat_llm(self, prompt: str, system_prompt: str = "") -> str:
        return await self._bridge().chat_llm(prompt, system_prompt)

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
        """调用 LLM 生成回复（宿主 ChatLLM：默认 chat provider）。"""
        from astrbot.core.provider.entities import LLMResponse

        if prompt is None:
            prompt = ""
        if contexts:
            last = contexts[-1] if isinstance(contexts[-1], dict) else None
            if last and last.get("content"):
                prompt = str(last["content"]) + "\n" + prompt
        text = await self._bridge().chat_llm(prompt, system_prompt or "")
        resp = LLMResponse(role="assistant")
        from astrbot.core.message.message_event_result import MessageChain
        from astrbot.core.message.components import Plain

        resp.result_chain = MessageChain([Plain(text)])
        return resp

    def get_llm_tool_manager(self):
        from astrbot.core.provider.func_tool_manager import llm_tools

        return llm_tools

    def activate_llm_tool(self, name: str) -> bool:
        return True

    async def activate_llm_tool_async(self, name: str) -> bool:
        return True

    def deactivate_llm_tool(self, name: str) -> bool:
        return True

    async def deactivate_llm_tool_async(self, name: str) -> bool:
        return True

    def register_task(self, task, desc: str) -> None:
        logger.info(f"register_task: {desc}")

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

    def unregister_llm_tool(self, name: str) -> None:
        from astrbot.core.provider.func_tool_manager import llm_tools

        llm_tools.remove_func(name)

    def register_commands(self, command: dict) -> None:
        pass

    def register_web_api(self, route: str, handler) -> None:
        logger.warning("register_web_api 在 Go 宿主兼容运行时中不可用")
