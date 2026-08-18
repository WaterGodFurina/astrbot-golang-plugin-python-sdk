"""HostService 客户端 + 宿主桥（插件 → 宿主反向调用）。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading

import grpc

from astrbot._bridge import loop
from astrbot._bridge.broker import get_broker
from astrbot._bridge.gen import plugin_pb2, plugin_pb2_grpc
from astrbot._bridge.serialize import component_to_json

logger = logging.getLogger("astrbot.host")

HOST_SERVICE_APP_ID = 9000

# 宿主 accept 9000 后，ConnInfo 只在约 5s 窗口内有效（go-plugin timeoutWait）；
# 与 Go SDK 一致，serve 后立即循环预连接并缓存。
PRECONNECT_ATTEMPTS = 20
PRECONNECT_INTERVAL = 0.25


class HostBridge:
    """宿主能力代理（经 broker Dial 9000 连接宿主 HostService）。"""

    def __init__(self) -> None:
        self._channel: grpc.Channel | None = None
        self._stub: plugin_pb2_grpc.HostServiceStub | None = None
        self._lock = threading.Lock()
        self.plugin_name: str = ""
        self.plugin_id: str = ""
        # 宿主能力集：宿主启动/重载平台时经 ASTRBOT_HOST_CAPABILITIES 环境
        # 变量注入（逗号分隔）。能力命名 = 已注册平台适配器 ID（如
        # aiocqhttp/qq_official/telegram/slack）+ 固定能力（llm/send_message/
        # recall_message/react/t2i/config/web）。旧宿主不注入该变量 → 空集
        # 容错（has() 恒 False），插件按"能力未知"降级处理。用法示例：
        #   bridge = get_bridge()
        #   if bridge.has("aiocqhttp"): ...   # 当前宿主是否接入了该平台
        #   if bridge.has("llm"): ...         # 宿主是否提供 ChatLLM
        raw = os.environ.get("ASTRBOT_HOST_CAPABILITIES", "")
        self.capabilities: set[str] = {c.strip() for c in raw.split(",") if c.strip()}

    def has(self, name: str) -> bool:
        """报告宿主是否公开了名为 name 的能力（平台 ID 或固定能力）。"""
        return name in self.capabilities

    def connect(self, service_id: int = HOST_SERVICE_APP_ID) -> bool:
        try:
            channel = get_broker().dial(service_id)
            stub = plugin_pb2_grpc.HostServiceStub(channel)
            with self._lock:
                self._channel = channel
                self._stub = stub
            return True
        except Exception as e:
            logger.debug(f"HostService connect 失败: {e}")
            return False

    def ensure_connected(self) -> bool:
        """确保宿主桥已连接；未连接时同步 Dial（宿主 accept 可能晚于插件
        启动，最多重试 PRECONNECT_ATTEMPTS 次）。插件加载期间
        （get_config 等）会调用本方法，不能一次失败就放弃。"""
        with self._lock:
            if self._stub is not None:
                return True
        for i in range(PRECONNECT_ATTEMPTS):
            if self.connect():
                return True
            import time

            time.sleep(PRECONNECT_INTERVAL)
        return False

    def preconnect(self) -> None:
        for _ in range(PRECONNECT_ATTEMPTS):
            if self.connect():
                logger.info("HostService 已连接（预连接）")
                return
            import time

            time.sleep(PRECONNECT_INTERVAL)
        logger.warning("HostService 预连接失败（5s 窗口内未收到 ConnInfo），插件将失去反向调用能力")

    def get_config(self, plugin_name: str) -> dict:
        # 身份绑定（Register 完成）前的 PermissionDenied / 未就绪错误会重试：
        # 插件 __init__ 里的 get_config 早于宿主 Register 完成，此时宿主侧
        # 连接身份还是 manifest id（未绑注册名），GetConfig 会被拒绝。
        name = plugin_name or self.plugin_name
        last_err: Exception | None = None
        for attempt in range(PRECONNECT_ATTEMPTS):
            if not self.ensure_connected():
                last_err = RuntimeError("宿主桥未连接")
            else:
                try:
                    resp = self._stub.GetConfig(
                        plugin_pb2.GetConfigRequest(plugin_name=name),
                        timeout=30,
                    )
                    if not resp.config_json:
                        return {}
                    data = json.loads(resp.config_json)
                    return data if isinstance(data, dict) else {}
                except grpc.RpcError as e:
                    last_err = e
                    # 身份未绑定（Register 未完成）→ 短暂等待后重试
                    if e.code() in (grpc.StatusCode.PERMISSION_DENIED, grpc.StatusCode.UNAVAILABLE):
                        import time

                        time.sleep(0.25)
                        continue
                    raise
            import time

            time.sleep(PRECONNECT_INTERVAL)
        logger.warning(f"get_config({name}) 重试失败: {last_err}")
        return {}

    def set_config(self, plugin_name: str, cfg: dict) -> bool:
        if not self.ensure_connected():
            return False
        self._stub.SetConfig(
            plugin_pb2.SetConfigRequest(
                plugin_name=plugin_name or self.plugin_name,
                config_json=json.dumps(cfg).encode(),
            ),
            timeout=30,
        )
        return True

    def chat_llm(
        self,
        prompt: str,
        system_prompt: str = "",
        image_urls: list[str] | None = None,
        session_id: str = "",
    ) -> str:
        if not self.ensure_connected():
            raise RuntimeError("宿主桥未就绪（ChatLLM 不可用）")
        resp = self._stub.ChatLLM(
            plugin_pb2.ChatLLMRequest(
                prompt=prompt,
                system_prompt=system_prompt,
                image_urls=image_urls or [],
                session_id=session_id,
            ),
            timeout=180,
        )
        return resp.text

    def react(self, platform: str, session_id: str, message_id: str, emoji: str) -> bool:
        if not self.ensure_connected():
            return False
        self._stub.React(
            plugin_pb2.ReactRequest(
                platform=platform,
                session_id=session_id,
                message_id=message_id,
                emoji=emoji,
            ),
            timeout=30,
        )
        return True

    def text_to_image(self, text: str, template_name: str = "") -> bytes:
        """宿主 t2i 渲染文本为图片，返回 PNG 字节。"""
        if not self.ensure_connected():
            raise RuntimeError("宿主桥未就绪（TextToImage 不可用）")
        resp = self._stub.TextToImage(
            plugin_pb2.TextToImageRequest(text=text, template_name=template_name),
            timeout=120,
        )
        import base64

        return base64.b64decode(resp.image_base64)

    def send_message(self, session, chain) -> bool:
        """发送消息链。session 为 MessageSession 或 MessageChain。"""
        from astrbot.core.platform.message_session import MessageSession

        if not self.ensure_connected():
            raise RuntimeError("宿主桥未就绪（SendMessage 不可用）")
        if isinstance(session, str):
            session = MessageSession.from_str(session)
        chain_json = [component_to_json(c) for c in chain.chain]
        self._stub.SendMessage(
            plugin_pb2.SendMessageRequest(
                platform=session.platform_id,
                session_id=session.session_id,
                chain_json=json.dumps(chain_json).encode(),
            ),
            timeout=30,
        )
        return True

    def call_action(self, platform: str, api: str, params: dict) -> dict:
        if not self.ensure_connected():
            raise RuntimeError("宿主桥未就绪（CallAction 不可用）")
        resp = self._stub.CallAction(
            plugin_pb2.CallActionRequest(
                platform=platform,
                api=api,
                params_json=json.dumps(params or {}).encode(),
            ),
            timeout=30,
        )
        if not resp.result_json:
            return {}
        data = json.loads(resp.result_json)
        return data if isinstance(data, dict) else {"data": data}

    async def call_action_async(self, platform: str, api: str, params: dict) -> dict:
        """真异步 call_action：阻塞的同步 RPC 经 asyncio.to_thread 移出插件
        常驻事件循环，避免一次慢调用冻结所有 async handler。"""
        return await asyncio.to_thread(self.call_action, platform, api, params)

    async def send_message_async(self, session, chain) -> bool:
        """真异步 send_message（asyncio.to_thread 包装）。"""
        return await asyncio.to_thread(self.send_message, session, chain)

    async def chat_llm_async(
        self,
        prompt: str,
        system_prompt: str = "",
        image_urls: list[str] | None = None,
        session_id: str = "",
    ) -> str:
        """真异步 chat_llm（LLM 响应可能长达数十秒，绝不能阻塞常驻 loop）。"""
        return await asyncio.to_thread(
            self.chat_llm, prompt, system_prompt, image_urls, session_id
        )

    async def text_to_image_async(self, text: str, template_name: str = "") -> bytes:
        """真异步 text_to_image。"""
        return await asyncio.to_thread(self.text_to_image, text, template_name)

    async def get_config_async(self, plugin_name: str) -> dict:
        return await asyncio.to_thread(self.get_config, plugin_name)

    async def set_config_async(self, plugin_name: str, cfg: dict) -> bool:
        return await asyncio.to_thread(self.set_config, plugin_name, cfg)

    def recall_message(self, platform: str, message_id: str) -> bool:
        if not self.ensure_connected():
            return False
        self._stub.RecallMessage(
            plugin_pb2.RecallMessageRequest(platform=platform, message_id=message_id),
            timeout=30,
        )
        return True

    async def react_message(self, event, emoji: str) -> bool:
        """给事件消息添加表情回应（宿主 React RPC）。"""
        from astrbot.core.platform.message_type import MessageType

        msg_id = getattr(event.message_obj, "message_id", "") if event.message_obj else ""
        if not msg_id:
            return False
        session = event.session
        return await asyncio.to_thread(self.react, session.platform_id, session.session_id, msg_id, emoji)

    # ── 会话管理 ────────────────────────────────────────────────────────────
    def get_curr_conversation_id(self, umo: str) -> str:
        if not self.ensure_connected():
            return ""
        try:
            resp = self._stub.GetCurrConversationID(
                plugin_pb2.ConversationIDRequest(unified_msg_origin=umo),
                timeout=30,
            )
            return resp.cid
        except Exception:
            return ""

    def new_conversation(self, umo: str, platform_id: str = "", persona_id: str = "") -> str:
        if not self.ensure_connected():
            return ""
        try:
            resp = self._stub.NewConversation(
                plugin_pb2.NewConversationRequest(
                    unified_msg_origin=umo,
                    platform_id=platform_id,
                    persona_id=persona_id,
                ),
                timeout=30,
            )
            return resp.cid
        except Exception:
            return ""

    def get_conversation(
        self, umo: str, conversation_id: str = "", create_if_not_exists: bool = False
    ) -> dict | None:
        if not self.ensure_connected():
            return None
        try:
            resp = self._stub.GetConversation(
                plugin_pb2.GetConversationRequest(
                    unified_msg_origin=umo,
                    conversation_id=conversation_id,
                    create_if_not_exists=create_if_not_exists,
                ),
                timeout=30,
            )
            if not resp.conversation_json:
                return None
            data = json.loads(resp.conversation_json)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def get_conversations(self, umo: str = "") -> list[dict]:
        if not self.ensure_connected():
            return []
        try:
            resp = self._stub.GetConversations(
                plugin_pb2.GetConversationsRequest(unified_msg_origin=umo),
                timeout=30,
            )
            out: list[dict] = []
            for raw in resp.conversations_json:
                data = json.loads(raw)
                if isinstance(data, dict):
                    out.append(data)
            return out
        except Exception:
            return []

    def delete_conversation(self, umo: str, conversation_id: str = "") -> bool:
        if not self.ensure_connected():
            return False
        try:
            self._stub.DeleteConversation(
                plugin_pb2.DeleteConversationRequest(
                    unified_msg_origin=umo,
                    conversation_id=conversation_id,
                ),
                timeout=30,
            )
            return True
        except Exception:
            return False

    def switch_conversation(self, umo: str, conversation_id: str) -> bool:
        if not self.ensure_connected():
            return False
        try:
            self._stub.SwitchConversation(
                plugin_pb2.SwitchConversationRequest(
                    unified_msg_origin=umo,
                    conversation_id=conversation_id,
                ),
                timeout=30,
            )
            return True
        except Exception:
            return False

    def update_conversation_title(self, umo: str, title: str, conversation_id: str = "") -> bool:
        if not self.ensure_connected():
            return False
        try:
            self._stub.UpdateConversationTitle(
                plugin_pb2.UpdateConversationTitleRequest(
                    unified_msg_origin=umo,
                    conversation_id=conversation_id,
                    title=title,
                ),
                timeout=30,
            )
            return True
        except Exception:
            return False

    def update_conversation_persona_id(
        self, umo: str, persona_id: str, conversation_id: str = ""
    ) -> bool:
        if not self.ensure_connected():
            return False
        try:
            self._stub.UpdateConversationPersonaID(
                plugin_pb2.UpdateConversationPersonaRequest(
                    unified_msg_origin=umo,
                    conversation_id=conversation_id,
                    persona_id=persona_id,
                ),
                timeout=30,
            )
            return True
        except Exception:
            return False

    # ── 人格管理 ────────────────────────────────────────────────────────────
    def get_personas(self) -> list[dict]:
        if not self.ensure_connected():
            return []
        try:
            resp = self._stub.GetPersonas(plugin_pb2.Empty(), timeout=30)
            out: list[dict] = []
            for raw in resp.personas_json:
                data = json.loads(raw)
                if isinstance(data, dict):
                    out.append(data)
            return out
        except Exception:
            return []

    def get_default_persona(self, umo: str = "") -> dict | None:
        if not self.ensure_connected():
            return None
        try:
            resp = self._stub.GetDefaultPersona(
                plugin_pb2.GetDefaultPersonaRequest(umo=umo),
                timeout=30,
            )
            if not resp.persona_json:
                return None
            data = json.loads(resp.persona_json)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def get_persona_tree(self) -> tuple[list[dict], list[dict]]:
        """返回 (folders, personas)：文件夹树（嵌套结构）+ 全部人格。"""
        if not self.ensure_connected():
            return [], []
        try:
            resp = self._stub.GetPersonaTree(plugin_pb2.Empty(), timeout=30)
            folders: list[dict] = []
            personas: list[dict] = []
            for raw in resp.folders_json:
                data = json.loads(raw)
                if isinstance(data, dict):
                    folders.append(data)
            for raw in resp.personas_json:
                data = json.loads(raw)
                if isinstance(data, dict):
                    personas.append(data)
            return folders, personas
        except Exception:
            return [], []

    def resolve_selected_persona(
        self,
        umo: str,
        conversation_persona_id: str = "",
        platform_name: str = "",
        provider_settings: dict | None = None,
    ) -> dict:
        """解析当前生效人格，返回含 persona_id/persona_name/persona_prompt/
        force_applied_persona_id/is_default 的 dict。"""
        if not self.ensure_connected():
            return {}
        try:
            resp = self._stub.ResolveSelectedPersona(
                plugin_pb2.ResolvePersonaRequest(
                    umo=umo,
                    conversation_persona_id=conversation_persona_id,
                    platform_name=platform_name,
                    provider_settings_json=json.dumps(provider_settings or {}).encode(),
                ),
                timeout=30,
            )
            return {
                "persona_id": resp.persona_id,
                "persona_name": resp.persona_name,
                "persona_prompt": resp.persona_prompt,
                "force_applied_persona_id": resp.force_applied_persona_id,
                "is_default": resp.is_default,
            }
        except Exception:
            return {}

    # ── Provider 管理 ───────────────────────────────────────────────────────
    def list_providers(self, capability: str = "") -> list[dict]:
        if not self.ensure_connected():
            return []
        try:
            resp = self._stub.ListProviders(
                plugin_pb2.ListProvidersRequest(capability=capability),
                timeout=30,
            )
            out: list[dict] = []
            for raw in resp.providers_json:
                data = json.loads(raw)
                if isinstance(data, dict):
                    out.append(data)
            return out
        except Exception:
            return []

    def get_using_provider(self, umo: str = "", capability: str = "chat_completion") -> dict | None:
        if not self.ensure_connected():
            return None
        try:
            resp = self._stub.GetUsingProvider(
                plugin_pb2.GetUsingProviderRequest(umo=umo, capability=capability),
                timeout=30,
            )
            if not resp.provider_json:
                return None
            data = json.loads(resp.provider_json)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def set_provider(
        self, umo: str = "", provider_id: str = "", capability: str = "chat_completion"
    ) -> bool:
        if not self.ensure_connected():
            return False
        try:
            self._stub.SetProvider(
                plugin_pb2.SetProviderRequest(
                    umo=umo,
                    provider_id=provider_id,
                    capability=capability,
                ),
                timeout=30,
            )
            return True
        except Exception:
            return False

    def get_provider_models(self, provider_id: str) -> list[str]:
        if not self.ensure_connected():
            return []
        try:
            resp = self._stub.GetProviderModels(
                plugin_pb2.GetProviderModelsRequest(provider_id=provider_id),
                timeout=30,
            )
            return list(resp.models)
        except Exception:
            return []

    # ── 插件/Star 管理 ──────────────────────────────────────────────────────
    def list_stars(self) -> list[dict]:
        if not self.ensure_connected():
            return []
        try:
            resp = self._stub.ListStars(plugin_pb2.Empty(), timeout=30)
            out: list[dict] = []
            for raw in resp.stars_json:
                data = json.loads(raw)
                if isinstance(data, dict):
                    out.append(data)
            return out
        except Exception:
            return []

    def get_star(self, name: str) -> dict | None:
        if not self.ensure_connected():
            return None
        try:
            resp = self._stub.GetStar(
                plugin_pb2.GetStarRequest(name=name),
                timeout=30,
            )
            if not resp.star_json:
                return None
            data = json.loads(resp.star_json)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def set_plugin_enabled(self, plugin_name: str, enabled: bool) -> bool:
        if not self.ensure_connected():
            return False
        try:
            self._stub.SetPluginEnabled(
                plugin_pb2.SetPluginEnabledRequest(plugin_name=plugin_name, enabled=enabled),
                timeout=30,
            )
            return True
        except Exception:
            return False

    def install_plugin(self, repo: str) -> bool:
        if not self.ensure_connected():
            return False
        try:
            self._stub.InstallPlugin(
                plugin_pb2.InstallPluginRequest(repo=repo),
                timeout=30,
            )
            return True
        except Exception:
            return False

    def uninstall_plugin(self, plugin_name: str) -> bool:
        if not self.ensure_connected():
            return False
        try:
            self._stub.UninstallPlugin(
                plugin_pb2.UninstallPluginRequest(plugin_name=plugin_name),
                timeout=30,
            )
            return True
        except Exception:
            return False

    # ── 会话管理 async ──────────────────────────────────────────────────────
    async def get_curr_conversation_id_async(self, umo: str) -> str:
        return await asyncio.to_thread(self.get_curr_conversation_id, umo)

    async def new_conversation_async(
        self, umo: str, platform_id: str = "", persona_id: str = ""
    ) -> str:
        return await asyncio.to_thread(self.new_conversation, umo, platform_id, persona_id)

    async def get_conversation_async(
        self, umo: str, conversation_id: str = "", create_if_not_exists: bool = False
    ) -> dict | None:
        return await asyncio.to_thread(
            self.get_conversation, umo, conversation_id, create_if_not_exists
        )

    async def get_conversations_async(self, umo: str = "") -> list[dict]:
        return await asyncio.to_thread(self.get_conversations, umo)

    async def delete_conversation_async(self, umo: str, conversation_id: str = "") -> bool:
        return await asyncio.to_thread(self.delete_conversation, umo, conversation_id)

    async def switch_conversation_async(self, umo: str, conversation_id: str) -> bool:
        return await asyncio.to_thread(self.switch_conversation, umo, conversation_id)

    async def update_conversation_title_async(
        self, umo: str, title: str, conversation_id: str = ""
    ) -> bool:
        return await asyncio.to_thread(self.update_conversation_title, umo, title, conversation_id)

    async def update_conversation_persona_id_async(
        self, umo: str, persona_id: str, conversation_id: str = ""
    ) -> bool:
        return await asyncio.to_thread(
            self.update_conversation_persona_id, umo, persona_id, conversation_id
        )

    # ── 人格管理 async ──────────────────────────────────────────────────────
    async def get_personas_async(self) -> list[dict]:
        return await asyncio.to_thread(self.get_personas)

    async def get_default_persona_async(self, umo: str = "") -> dict | None:
        return await asyncio.to_thread(self.get_default_persona, umo)

    async def get_persona_tree_async(self) -> tuple[list[dict], list[dict]]:
        return await asyncio.to_thread(self.get_persona_tree)

    async def resolve_selected_persona_async(
        self,
        umo: str,
        conversation_persona_id: str = "",
        platform_name: str = "",
        provider_settings: dict | None = None,
    ) -> dict:
        return await asyncio.to_thread(
            self.resolve_selected_persona,
            umo,
            conversation_persona_id,
            platform_name,
            provider_settings,
        )

    # ── Provider 管理 async ─────────────────────────────────────────────────
    async def list_providers_async(self, capability: str = "") -> list[dict]:
        return await asyncio.to_thread(self.list_providers, capability)

    async def get_using_provider_async(
        self, umo: str = "", capability: str = "chat_completion"
    ) -> dict | None:
        return await asyncio.to_thread(self.get_using_provider, umo, capability)

    async def set_provider_async(
        self, umo: str = "", provider_id: str = "", capability: str = "chat_completion"
    ) -> bool:
        return await asyncio.to_thread(self.set_provider, umo, provider_id, capability)

    async def get_provider_models_async(self, provider_id: str) -> list[str]:
        return await asyncio.to_thread(self.get_provider_models, provider_id)

    # ── 插件/Star 管理 async ────────────────────────────────────────────────
    async def list_stars_async(self) -> list[dict]:
        return await asyncio.to_thread(self.list_stars)

    async def get_star_async(self, name: str) -> dict | None:
        return await asyncio.to_thread(self.get_star, name)

    async def set_plugin_enabled_async(self, plugin_name: str, enabled: bool) -> bool:
        return await asyncio.to_thread(self.set_plugin_enabled, plugin_name, enabled)

    async def install_plugin_async(self, repo: str) -> bool:
        return await asyncio.to_thread(self.install_plugin, repo)

    async def uninstall_plugin_async(self, plugin_name: str) -> bool:
        return await asyncio.to_thread(self.uninstall_plugin, plugin_name)


_bridge: HostBridge | None = None


def get_bridge() -> HostBridge:
    global _bridge
    if _bridge is None:
        _bridge = HostBridge()
    return _bridge
