"""全量 gRPC 接口覆盖测试（proto/plugin.proto）。

覆盖 HostService（插件→宿主）与 PluginService（宿主→插件）两个服务的
全部 RPC（proto/plugin.proto 实际定义：HostService 59 个 + PluginService
15 个 = 74 个，任务口径的"71 个"按 proto 实际为准全部覆盖）。

断言语义以 astrbot-py 本体源码（/tmp/opencode/AstrBot-py，v4.27.4，只读
权威）为准：先对照本体同名 API 的参数名/默认值/返回形态/异常语义，再写
断言；SDK 跨进程契约差异（如"宿主 RPC 失败返回空值而非抛异常"）在测试
处用中文注释标明。

两条测试通道：
A. HostService 面：FakeHostStub 注入模式——
       bridge = HostBridge(); bridge._stub = FakeHostStub(responses);
       bridge._probed = True
   FakeHostStub 记录每个 RPC 的（rpc 名, request）并按 responses[rpc 名]
   返回构造好的响应；断言请求字段用 bridge._stub.last(rpc 名).xxx。
   管理器入口（conversation_mgr / persona_mgr / provider manager /
   kb_mgr / cron_manager / file_token_service / skill_manager /
   PluginManager 等）与 bridge 直测都覆盖。
B. PluginService 面：直接实例化
       astrbot._bridge.dispatch.PluginServiceServicer(name, version, "", "")
   调方法（先例 tests/run_tests.py Register 测试：svc.mark_ready() 且
   mock astrbot._bridge.host.get_bridge 防 dial 挂起）。

运行：python tests/test_all_rpcs.py
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

# 插入仓库根目录（与 tests/run_tests.py 相同模式：把 astrbot/ 的父目录
# 加入搜索路径，保证 `astrbot` 包可导入）。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from astrbot._bridge.gen import plugin_pb2  # noqa: E402

# ── 通道 A 基础设施：FakeHostStub + bridge 工厂 ─────────────────────────

# HostService 全部 59 个 RPC 名（proto/plugin.proto service HostService）
HOST_RPC_NAMES = [
    # 平台动作 / 消息
    "CallAction", "SendMessage", "RecallMessage",
    # 配置
    "GetConfig", "SetConfig",
    # LLM / 渲染 / 表情
    "ChatLLM", "React", "TextToImage", "HtmlRender",
    # 会话管理（8）
    "GetCurrConversationID", "NewConversation", "GetConversation",
    "GetConversations", "DeleteConversation", "SwitchConversation",
    "UpdateConversationTitle", "UpdateConversationPersonaID",
    # 人格管理（4）
    "GetPersonas", "GetDefaultPersona", "GetPersonaTree",
    "ResolveSelectedPersona",
    # Provider 管理（4）
    "ListProviders", "GetUsingProvider", "SetProvider", "GetProviderModels",
    # 插件 / Star 管理（6）+ 平台
    "GetPluginRegistry", "GetStar", "SetPluginEnabled", "InstallPlugin",
    "UninstallPlugin", "ListCommandDescriptors", "ListPlatforms",
    # 会话等待（2）/ 桥接钩子（2）
    "RegisterSessionWait", "UnregisterSessionWait",
    "RegisterBridgeHook", "UnregisterBridgeHook",
    # Blob（4）
    "CreateBlob", "ReadBlob", "GetBlobInfo", "ReleaseBlob",
    # 技能（3 + V2）
    "ListSkills", "SetSkillActive", "DeleteSkill", "ListSkillsV2",
    # 平台消息历史（4）
    "GetPlatformMessageHistory", "InsertPlatformMessageHistory",
    "UpdatePlatformMessageHistory", "DeletePlatformMessageHistory",
    # 知识库（3）/ 文件令牌
    "KBRetrieve", "KBUploadFromURL", "KBListKBs", "RegisterFileToken",
    # 定时任务（5）/ MCP（2）
    "CronCreate", "CronUpdate", "CronDelete", "CronList", "CronRunNow",
    "McpListTools", "McpCallTool",
]
assert len(HOST_RPC_NAMES) == 59

# 各 RPC 的默认响应类型（Fake 未配置响应时用其消息零值，而非一律 Empty——
# 桥接方法会读响应字段，类型不对会 AttributeError 掩盖真实语义）
_DEFAULT_RESPONSES = {
    name: plugin_pb2.Empty()
    for name in (
        "SetConfig", "SendMessage", "RecallMessage", "React", "DeleteConversation",
        "SwitchConversation", "UpdateConversationTitle", "UpdateConversationPersonaID",
        "SetProvider", "UnregisterSessionWait", "RegisterBridgeHook",
        "UnregisterBridgeHook", "ReleaseBlob", "SetSkillActive", "DeleteSkill",
        "UpdatePlatformMessageHistory", "DeletePlatformMessageHistory",
        "KBUploadFromURL", "CronDelete", "CronRunNow", "InstallPlugin",
        "UninstallPlugin", "SetPluginEnabled",
    )
}
_DEFAULT_RESPONSES.update({
    "CallAction": plugin_pb2.CallActionResponse(),
    "GetConfig": plugin_pb2.GetConfigResponse(),
    "ChatLLM": plugin_pb2.ChatLLMResponse(),
    "TextToImage": plugin_pb2.TextToImageResponse(),
    "HtmlRender": plugin_pb2.HtmlRenderResponse(),
    "GetCurrConversationID": plugin_pb2.ConversationIDResponse(),
    "NewConversation": plugin_pb2.ConversationIDResponse(),
    "GetConversation": plugin_pb2.ConversationResponse(),
    "GetConversations": plugin_pb2.ConversationsResponse(),
    "GetPersonas": plugin_pb2.PersonasResponse(),
    "GetDefaultPersona": plugin_pb2.PersonaResponse(),
    "GetPersonaTree": plugin_pb2.PersonaTreeResponse(),
    "ResolveSelectedPersona": plugin_pb2.ResolvePersonaResponse(),
    "ListProviders": plugin_pb2.ProvidersResponse(),
    "GetUsingProvider": plugin_pb2.ProviderResponse(),
    "SetProvider": plugin_pb2.Empty(),
    "GetProviderModels": plugin_pb2.ProviderModelsResponse(),
    "GetPluginRegistry": plugin_pb2.StarsResponse(),
    "GetStar": plugin_pb2.StarResponse(),
    "ListCommandDescriptors": plugin_pb2.CommandDescriptorsResponse(),
    "ListPlatforms": plugin_pb2.PlatformsResponse(),
    "RegisterSessionWait": plugin_pb2.RegisterSessionWaitResponse(),
    "CreateBlob": plugin_pb2.CreateBlobResponse(),
    "ReadBlob": plugin_pb2.ReadBlobResponse(),
    "GetBlobInfo": plugin_pb2.GetBlobInfoResponse(),
    "ListSkills": plugin_pb2.SkillsResponse(),
    "ListSkillsV2": plugin_pb2.SkillsResponse(),
    "GetPlatformMessageHistory": plugin_pb2.PMHistoryRecordsResponse(),
    "InsertPlatformMessageHistory": plugin_pb2.PMHistoryRecordResponse(),
    "KBRetrieve": plugin_pb2.KBRetrieveResponse(),
    "KBListKBs": plugin_pb2.KBListResponse(),
    "RegisterFileToken": plugin_pb2.RegisterFileTokenResponse(),
    "CronCreate": plugin_pb2.CronJobResponse(),
    "CronUpdate": plugin_pb2.CronJobResponse(),
    "CronList": plugin_pb2.CronJobsResponse(),
    "McpListTools": plugin_pb2.McpToolsResponse(),
    "McpCallTool": plugin_pb2.McpCallToolResponse(),
})


def rpc_error(msg="host down"):
    """构造 grpc.RpcError（bridge 多数方法只捕获该类型降级）。"""
    import grpc

    return grpc.RpcError(msg)


class FakeHostStub:
    """Fake 宿主 stub：每个 RPC 方法记录（rpc 名, request）并返回构造好的
    响应（responses[rpc 名]，可为消息对象或 callable(request)->响应）。
    未配置响应的 RPC 返回该 RPC 的消息零值（proto3 语义正确）。"""

    def __init__(self, responses=None):
        self.calls = []  # [(rpc 名, request)]
        self.responses = dict(responses or {})

    def __getattr__(self, name):
        if name in HOST_RPC_NAMES:
            responses = self.__dict__["responses"]
            calls = self.__dict__["calls"]

            def _call(req, timeout=None):
                calls.append((name, req))
                resp = responses.get(name)
                if callable(resp):
                    return resp(req)
                return resp if resp is not None else _DEFAULT_RESPONSES.get(
                    name, plugin_pb2.Empty()
                )

            return _call
        # 非 RPC 名（如 skill_manager getattr 探测 list_skills_v2 失败场景）
        raise AttributeError(name)

    def last(self, rpc_name):
        """最近一次 rpc_name 调用的 request（无调用返回 None）。"""
        for name, req in reversed(self.calls):
            if name == rpc_name:
                return req
        return None

    def calls_of(self, rpc_name):
        """全部 rpc_name 调用的 request 列表。"""
        return [req for name, req in self.calls if name == rpc_name]


def make_bridge(responses=None, plugin_name="test_plugin"):
    """构造注入 FakeHostStub 的 HostBridge（_probed=True 跳过连通性探测）。"""
    from astrbot._bridge.host import HostBridge

    bridge = HostBridge()
    bridge._stub = FakeHostStub(responses)
    bridge._probed = True
    bridge.plugin_name = plugin_name
    return bridge


class _HostBridgeCase(unittest.IsolatedAsyncioTestCase):
    """HostService 测试基类：提供 get_host_bridge 补丁与轮询等待工具。"""

    def setUp(self):
        import astrbot.core.star.context as ctx_mod
        import astrbot._bridge.host as host_mod

        self._old_get_host_bridge = ctx_mod.get_host_bridge
        self._old_host_global = ctx_mod._host_bridge
        self._old_host_singleton = host_mod._bridge
        self._bridges = []

    def tearDown(self):
        import astrbot.core.star.context as ctx_mod
        import astrbot._bridge.host as host_mod

        ctx_mod.get_host_bridge = self._old_get_host_bridge
        ctx_mod.set_host_bridge(self._old_host_global)
        host_mod._bridge = self._old_host_singleton

    def make_bridge(self, responses=None, plugin_name="test_plugin"):
        bridge = make_bridge(responses, plugin_name)
        self._bridges.append(bridge)
        return bridge

    def patch_host_bridge(self, bridge):
        """把 bridge 注入桥取入口：context 的 `_host_bridge` 全局 /
        `get_host_bridge`（Context 与各管理器薄壳的取桥路径），以及
        astrbot._bridge.host.get_bridge 单例（Star/html_renderer 等直接
        import get_bridge 的路径）。tearDown 统一恢复。"""
        import astrbot.core.star.context as ctx_mod
        import astrbot._bridge.host as host_mod

        ctx_mod.set_host_bridge(bridge)
        ctx_mod.get_host_bridge = lambda: bridge
        host_mod.set_bridge(bridge)

    @staticmethod
    def wait_until(pred, timeout=3.0, interval=0.02):
        """轮询等待（线程派发的喂入 handler 需要时间执行）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pred():
                return True
            time.sleep(interval)
        return pred()

    @staticmethod
    def wait_until_pumping(pred, timeout=3.0, interval=0.05):
        """轮询等待 + 主动泵 SDK 常驻 loop（跨线程 create_task 不会唤醒
        空闲 loop 的 selector，需经线程安全的 run_coroutine_threadsafe
        触发一次空协程让挂起任务得以调度）。"""
        import astrbot._bridge.loop as sdk_loop

        async def _tick():
            await asyncio.sleep(0)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pred():
                return True
            try:
                sdk_loop.run_coro(_tick(), timeout=1.0)
            except Exception:
                pass
            time.sleep(interval)
        return pred()


def _json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode()


# ── 通道 A：HostService ──────────────────────────────────────────────────

class TestHostServiceCoreActions(_HostBridgeCase):
    """CallAction / SendMessage / RecallMessage / ChatLLM / React /
    TextToImage / HtmlRender（bridge 直测 + Context/事件入口）。"""

    async def test_call_action_request_and_response(self):
        # 本体侧插件经平台适配器 call_action(api, **params) 调平台 API；
        # SDK 跨进程契约：params 经 params_json 编码转发宿主 CallAction。
        bridge = self.make_bridge({
            "CallAction": plugin_pb2.CallActionResponse(
                result_json=_json_bytes({"status": True, "data": {"nick": "bot"}})
            ),
        })
        out = bridge.call_action("aiocqhttp", "get_login_info", {"k": 1})
        req = bridge._stub.last("CallAction")
        self.assertEqual(req.platform, "aiocqhttp")
        self.assertEqual(req.api, "get_login_info")
        self.assertEqual(json.loads(req.params_json), {"k": 1})
        # 响应解析：result_json → dict（本体平台 API 返回 dict）
        self.assertEqual(out, {"status": True, "data": {"nick": "bot"}})
        # params 为 None → {}
        bridge.call_action("aiocqhttp", "get_login_info", None)
        self.assertEqual(
            json.loads(bridge._stub.last("CallAction").params_json), {}
        )
        # 宿主返回空 result_json → {}（契约差异：本体 API 报错会抛异常，
        # SDK 经 RPC 后失败返回空 dict）
        bridge2 = self.make_bridge({"CallAction": plugin_pb2.CallActionResponse()})
        self.assertEqual(bridge2.call_action("p", "api", {}), {})

    async def test_send_message_via_context_send_message(self):
        # 本体 Context.send_message(session, message_chain)（本体
        # core/star/context.py:614）：session 为 str 时解析为 MessageSession。
        # SDK 跨进程契约差异：本体在同进程平台实例直接发送并返回"是否找到
        # 平台"，SDK 经宿主 SendMessage RPC，链走原生 repeated Component。
        from astrbot.core.message.components import Plain
        from astrbot.core.message.message_event_result import MessageChain
        from astrbot.core.star.context import Context

        bridge = self.make_bridge()
        self.patch_host_bridge(bridge)

        ctx = Context()
        ok = await ctx.send_message(
            "aiocqhttp:GroupMessage:12345", MessageChain([Plain(text="hi")])
        )
        self.assertTrue(ok)
        req = bridge._stub.last("SendMessage")
        self.assertEqual(req.platform, "aiocqhttp")
        self.assertEqual(req.session_id, "12345")
        self.assertEqual(len(req.chain_components), 1)
        self.assertEqual(req.chain_components[0].type, "Plain")
        self.assertEqual(req.chain_components[0].text, "hi")

    async def test_recall_message(self):
        # 本体侧撤回消息经平台客户端（如 aiocqhttp delete_msg）；SDK 跨进程
        # 契约：经宿主 RecallMessage RPC（platform + message_id），无管理器
        # 封装（bridge 直测；失败降级返回 False 不抛异常）。
        bridge = self.make_bridge()
        self.assertTrue(bridge.recall_message("aiocqhttp", "msg-1"))
        req = bridge._stub.last("RecallMessage")
        self.assertEqual(req.platform, "aiocqhttp")
        self.assertEqual(req.message_id, "msg-1")

        class _Err:
            def RecallMessage(self, req, timeout=None):
                raise rpc_error()

        bridge2 = self.make_bridge()
        bridge2._stub = _Err()
        self.assertFalse(bridge2.recall_message("aiocqhttp", "msg-1"))

    async def test_chat_llm_request_fields(self):
        # 本体 LLM 调用经 provider.text_chat(...)；SDK 跨进程契约：经宿主
        # ChatLLM RPC（prompt/system_prompt/image_urls/session_id/...）。
        bridge = self.make_bridge({
            "ChatLLM": plugin_pb2.ChatLLMResponse(text="hello reply"),
        })
        out = bridge.chat_llm(
            prompt="hi",
            system_prompt="sys",
            image_urls=["https://i/1.png"],
            session_id="umo-1",
            audio_urls=["https://a/1.mp3"],
            tools_json="[]",
            contexts_json="[]",
            provider_id="prov-1",
        )
        req = bridge._stub.last("ChatLLM")
        self.assertEqual(req.prompt, "hi")
        self.assertEqual(req.system_prompt, "sys")
        self.assertEqual(list(req.image_urls), ["https://i/1.png"])
        self.assertEqual(req.session_id, "umo-1")
        self.assertEqual(list(req.audio_urls), ["https://a/1.mp3"])
        self.assertEqual(req.tools_json, b"[]")
        self.assertEqual(req.contexts_json, b"[]")
        self.assertEqual(req.provider_id, "prov-1")
        self.assertEqual(out, "hello reply")

    async def test_chat_llm_via_context_entry(self):
        # Context.chat_llm(prompt, system_prompt, image_urls, session_id)
        # → bridge.chat_llm_async → ChatLLM RPC（对齐 SDK Context 入口）。
        from astrbot.core.star.context import Context

        bridge = self.make_bridge({
            "ChatLLM": plugin_pb2.ChatLLMResponse(text="ok"),
        })
        self.patch_host_bridge(bridge)

        ctx = Context()
        text = await ctx.chat_llm("问题", system_prompt="S", image_urls=["u"], session_id="s1")
        req = bridge._stub.last("ChatLLM")
        self.assertEqual(req.prompt, "问题")
        self.assertEqual(req.system_prompt, "S")
        self.assertEqual(list(req.image_urls), ["u"])
        self.assertEqual(req.session_id, "s1")
        self.assertEqual(req.provider_id, "")  # 默认 provider
        self.assertEqual(text, "ok")

    async def test_llm_generate_maps_provider_id_and_tools(self):
        # 本体 Context.llm_generate(chat_provider_id=..., tools=...)
        # （本体 core/star/context.py:171）；SDK 经 ChatLLM RPC：
        # chat_provider_id → provider_id，tools → tools_json（OpenAI 格式）。
        from astrbot.core.star.context import Context

        class _Tool:
            name = "t1"
            description = "d1"
            parameters = {"type": "object", "properties": {}}

        bridge = self.make_bridge({
            "ChatLLM": plugin_pb2.ChatLLMResponse(text="done"),
        })
        self.patch_host_bridge(bridge)

        ctx = Context()
        resp = await ctx.llm_generate(
            chat_provider_id="prov-9", prompt="p", tools=[_Tool()]
        )
        req = bridge._stub.last("ChatLLM")
        self.assertEqual(req.provider_id, "prov-9")
        tools = json.loads(req.tools_json)
        self.assertEqual(tools[0]["type"], "function")
        self.assertEqual(tools[0]["function"]["name"], "t1")
        # 本体 llm_generate 返回 LLMResponse（completion_text 可读）
        self.assertEqual(resp.completion_text, "done")

    async def test_react_via_event_entry(self):
        # 本体 event.react(emoji)（本体 core/platform/astr_message_event.py:497
        # 默认实现是补发一条表情消息）；SDK 跨进程契约：经宿主原生 React RPC
        # （platform/session_id/message_id/emoji）。
        from astrbot.core.platform.astr_message_event import AstrMessageEvent

        bridge = self.make_bridge()
        self.patch_host_bridge(bridge)

        se = plugin_pb2.SDKEvent(
            type="message", platform="qq_official", platform_id="qq_main",
            message_type="GroupMessage", sender_id="u1", conv_id="g1",
            message_str="hi", plain_text="hi", message_id="m-9", timestamp=1,
        )
        ev = AstrMessageEvent.from_proto(se)
        await ev.react("👍")
        req = bridge._stub.last("React")
        self.assertEqual(req.platform, "qq_main")
        self.assertEqual(req.session_id, "g1")
        self.assertEqual(req.message_id, "m-9")
        self.assertEqual(req.emoji, "👍")

    async def test_text_to_image_bytes_priority_and_base64_fallback(self):
        # 本体 Star.text_to_image(text, return_url=True)（本体
        # core/star/base.py:77，经 html_renderer.render_t2i）；SDK 跨进程契约：
        # 经宿主 TextToImage RPC，优先读 image_bytes，旧宿主回退 image_base64。
        import base64

        png = b"\x89PNG-fake"
        bridge = self.make_bridge({
            "TextToImage": plugin_pb2.TextToImageResponse(image_bytes=png),
        })
        self.assertEqual(bridge.text_to_image("画一只猫", template_name="t"), png)
        req = bridge._stub.last("TextToImage")
        self.assertEqual(req.text, "画一只猫")
        self.assertEqual(req.template_name, "t")

        bridge2 = self.make_bridge({
            "TextToImage": plugin_pb2.TextToImageResponse(
                image_base64=base64.b64encode(png).decode()
            ),
        })
        self.assertEqual(bridge2.text_to_image("x"), png)

    async def test_text_to_image_via_star_entry(self):
        # Star.text_to_image 入口（本体 core/star/base.py:77 同名 API：
        # return_url=True 返回 data URL）。
        from astrbot.core.star.base import Star

        png = b"\x89PNG"
        bridge = self.make_bridge({
            "TextToImage": plugin_pb2.TextToImageResponse(image_bytes=png),
        })
        self.patch_host_bridge(bridge)
        star = Star.__new__(Star)  # 不走注册表的无状态实例
        url = await star.text_to_image("hi")
        self.assertTrue(url.startswith("data:image/png;base64,"))

    async def test_html_render_request_fields_and_entry(self):
        # 本体 Star.html_render(tmpl, data, return_url, options)（本体
        # core/star/base.py:92）；SDK 经宿主 HtmlRender RPC。
        from astrbot.core.star.base import Star

        png = b"\x89PNG-r"
        bridge = self.make_bridge({
            "HtmlRender": plugin_pb2.HtmlRenderResponse(image_bytes=png),
        })
        out = bridge.html_render("<h1>{{x}}</h1>", data='{"x":1}', options='{"type":"png"}')
        req = bridge._stub.last("HtmlRender")
        self.assertEqual(req.template, "<h1>{{x}}</h1>")
        self.assertEqual(req.data, '{"x":1}')
        self.assertEqual(req.options, '{"type":"png"}')
        self.assertEqual(out, png)

        self.patch_host_bridge(bridge)
        star = Star.__new__(Star)
        url = await star.html_render("<p>d</p>", {"x": 1})
        self.assertTrue(url.startswith("data:image/png;base64,"))
        # html_renderer 入口（本体 from astrbot.core import html_renderer）
        from astrbot.core.utils.html_renderer import html_renderer

        url2 = await html_renderer.render_t2i("文本")
        self.assertTrue(url2.startswith("data:image/png;base64,"))


class TestHostServiceConfig(_HostBridgeCase):
    """GetConfig / SetConfig（Context.get_config / AstrBotConfig save 链）。"""

    async def test_get_config_via_context(self):
        # 本体插件配置在本地文件（data/config/*.json，本体
        # core/config/astrbot_config.py）；SDK 跨进程契约差异：本体直接读
        # 本地配置，SDK 经宿主 GetConfig RPC 拉取（插件名经 Register 绑定）。
        from astrbot.core.star.context import Context

        payload = {"token": "abc", "__schema__": {"type": "object", "properties": {}}}
        bridge = self.make_bridge({
            "GetConfig": plugin_pb2.GetConfigResponse(config_json=_json_bytes(payload)),
        })
        self.patch_host_bridge(bridge)

        ctx = Context()
        ctx.plugin_name = "plug_a"
        cfg = ctx.get_config()
        req = bridge._stub.last("GetConfig")
        self.assertEqual(req.plugin_name, "plug_a")
        # 本体 AstrBotConfig 形态：dict 子类 + schema 属性
        self.assertEqual(cfg["token"], "abc")
        self.assertEqual(cfg.schema, {"type": "object", "properties": {}})

    async def test_set_config_via_save_config_chain(self):
        # 本体 AstrBotConfig.save_config()（本体本地写 JSON 文件）；SDK 跨进程
        # 契约差异：写回宿主 SetConfig RPC（config_json=全量配置 JSON）。
        from astrbot.core.star.context import Context

        bridge = self.make_bridge({
            "GetConfig": plugin_pb2.GetConfigResponse(config_json=_json_bytes({"k": "v"})),
        })
        self.patch_host_bridge(bridge)

        ctx = Context()
        ctx.plugin_name = "plug_a"
        cfg = ctx.get_config()
        cfg["k2"] = "v2"
        # 异步入口（对齐本体 save_config_async 语义）
        ok = await cfg.save_config_async()
        self.assertTrue(ok)
        req = bridge._stub.last("SetConfig")
        self.assertEqual(req.plugin_name, "plug_a")
        sent = json.loads(req.config_json)
        self.assertEqual(sent.get("k"), "v")
        self.assertEqual(sent.get("k2"), "v2")

        # 同步入口：无运行事件循环的线程上走 bridge.set_config（同步 RPC）
        await asyncio.to_thread(cfg.save_config)
        req2 = bridge._stub.last("SetConfig")
        self.assertEqual(json.loads(req2.config_json).get("k2"), "v2")


class TestHostServiceConversation(_HostBridgeCase):
    """会话管理 8 RPC（conversation_mgr 7+1 方法 → 对应 8 个 conversation
    RPC）。本体依据：astrbot/core/conversation_mgr.py。"""

    UMO = "aiocqhttp:GroupMessage:10001"

    def _mgr(self, responses=None):
        from astrbot.core.conversation_mgr import ConversationManager

        bridge = self.make_bridge(responses)
        return ConversationManager(bridge), bridge

    async def test_get_curr_conversation_id(self):
        # 本体 conversation_mgr.py:174 get_curr_conversation_id(unified_msg_origin)
        # → str|None。SDK 契约差异：本体读本地 session_conversations/sp；
        # SDK 先问宿主（GetCurrConversationID RPC），宿主失败才回退本地缓存。
        mgr, bridge = self._mgr({
            "GetCurrConversationID": plugin_pb2.ConversationIDResponse(cid="cid-1"),
        })
        cid = await mgr.get_curr_conversation_id(self.UMO)
        req = bridge._stub.last("GetCurrConversationID")
        self.assertEqual(req.unified_msg_origin, self.UMO)
        self.assertEqual(cid, "cid-1")

        # 宿主无会话 → 空串 → None（对齐本体 str|None 返回形态）
        mgr2, _ = self._mgr({"GetCurrConversationID": plugin_pb2.ConversationIDResponse()})
        self.assertIsNone(await mgr2.get_curr_conversation_id(self.UMO))

    async def test_new_conversation(self):
        # 本体 conversation_mgr.py:92 new_conversation(unified_msg_origin,
        # platform_id=None, content=None, title=None, persona_id=None) → uuid cid。
        # SDK 契约差异：本体 content 经 DB 持久化、title 建库即写；宿主
        # NewConversation 无 content/title 字段 → content 丢弃，title 由
        # 创建后补发的 UpdateConversationTitle 设置。
        mgr, bridge = self._mgr({
            "NewConversation": plugin_pb2.ConversationIDResponse(cid="new-cid"),
        })
        cid = await mgr.new_conversation(
            self.UMO, platform_id=None,
            content=[{"role": "user", "content": "hi"}],
            title="T", persona_id="p1",
        )
        req = bridge._stub.last("NewConversation")
        self.assertEqual(req.unified_msg_origin, self.UMO)
        self.assertEqual(req.platform_id, "aiocqhttp")  # 本体：从 umo 解析
        self.assertEqual(req.persona_id, "p1")
        self.assertEqual(cid, "new-cid")
        # 本地缓存（本体 session_conversations 语义）
        self.assertEqual(mgr.session_conversations[self.UMO], "new-cid")
        # 标题补发（UpdateConversationTitle RPC）
        treq = bridge._stub.last("UpdateConversationTitle")
        self.assertEqual(treq.title, "T")
        self.assertEqual(treq.conversation_id, "new-cid")

    async def test_get_conversation(self):
        # 本体 conversation_mgr.py:190 get_conversation(unified_msg_origin,
        # conversation_id, create_if_not_exists=False) → Conversation|None，
        # PO 含 conversation_id/user_id/history(JSON str)/title/persona_id/
        # token_usage（本体 _convert_conv_from_v2_to_v1）。
        conv_dict = {
            "cid": "cid-2", "user_id": self.UMO, "platform_id": "aiocqhttp",
            "history": json.dumps([{"role": "user", "content": "hi"}]),
            "title": "标题", "persona_id": "p2", "token_usage": 128,
        }
        mgr, bridge = self._mgr({
            "GetConversation": plugin_pb2.ConversationResponse(
                conversation_json=_json_bytes(conv_dict)
            ),
        })
        conv = await mgr.get_conversation(
            self.UMO, "cid-2", create_if_not_exists=True
        )
        req = bridge._stub.last("GetConversation")
        self.assertEqual(req.unified_msg_origin, self.UMO)
        self.assertEqual(req.conversation_id, "cid-2")
        self.assertTrue(req.create_if_not_exists)
        # 本体 Conversation PO 字段一一对应（cid = conversation_id）
        self.assertEqual(conv.cid, "cid-2")
        self.assertEqual(conv.user_id, self.UMO)
        self.assertIn('"content"', conv.history)  # history 是 JSON 字符串
        self.assertEqual(conv.title, "标题")
        self.assertEqual(conv.persona_id, "p2")
        self.assertEqual(conv.token_usage, 128)

        # 不存在且不创建 → None（本体返回 None）
        mgr2, _ = self._mgr({"GetConversation": plugin_pb2.ConversationResponse()})
        self.assertIsNone(await mgr2.get_conversation(self.UMO, "nope"))

    async def test_get_conversations(self):
        # 本体 conversation_mgr.py:216 get_conversations(unified_msg_origin=None,
        # platform_id=None) → list[Conversation]。
        mgr, bridge = self._mgr({
            "GetConversations": plugin_pb2.ConversationsResponse(
                conversations_json=[
                    _json_bytes({"cid": "c1", "platform_id": "aiocqhttp"}),
                    _json_bytes({"cid": "c2", "platform_id": "telegram"}),
                ]
            ),
        })
        convs = await mgr.get_conversations(self.UMO)
        req = bridge._stub.last("GetConversations")
        self.assertEqual(req.unified_msg_origin, self.UMO)
        self.assertEqual([c.cid for c in convs], ["c1", "c2"])
        # platform_id 为本地过滤维度（契约注释见 mgr docstring）
        convs2 = await mgr.get_conversations(self.UMO, platform_id="telegram")
        self.assertEqual([c.cid for c in convs2], ["c2"])

    async def test_delete_conversation(self):
        # 本体 conversation_mgr.py:139 delete_conversation(unified_msg_origin,
        # conversation_id=None)。本体返回 None；SDK 返回 bool（成功 True）。
        mgr, bridge = self._mgr()
        mgr.session_conversations[self.UMO] = "cid-3"
        await mgr.delete_conversation(self.UMO, "cid-3")
        req = bridge._stub.last("DeleteConversation")
        self.assertEqual(req.unified_msg_origin, self.UMO)
        self.assertEqual(req.conversation_id, "cid-3")

    async def test_switch_conversation(self):
        # 本体 conversation_mgr.py:126 switch_conversation(unified_msg_origin,
        # conversation_id)。
        mgr, bridge = self._mgr()
        await mgr.switch_conversation(self.UMO, "cid-4")
        req = bridge._stub.last("SwitchConversation")
        self.assertEqual(req.unified_msg_origin, self.UMO)
        self.assertEqual(req.conversation_id, "cid-4")
        self.assertEqual(mgr.session_conversations[self.UMO], "cid-4")

    async def test_update_conversation_title(self):
        # 本体 conversation_mgr.py:310 update_conversation_title(unified_msg_origin,
        # title, conversation_id=None)（deprecated，本体转发 update_conversation）。
        mgr, bridge = self._mgr()
        await mgr.update_conversation_title(self.UMO, "新标题", "cid-5")
        req = bridge._stub.last("UpdateConversationTitle")
        self.assertEqual(req.unified_msg_origin, self.UMO)
        self.assertEqual(req.conversation_id, "cid-5")
        self.assertEqual(req.title, "新标题")

    async def test_update_conversation_persona_id(self):
        # 本体 conversation_mgr.py:335 update_conversation_persona_id(
        # unified_msg_origin, persona_id, conversation_id=None)（deprecated）。
        mgr, bridge = self._mgr()
        await mgr.update_conversation_persona_id(self.UMO, "p9", "cid-6")
        req = bridge._stub.last("UpdateConversationPersonaID")
        self.assertEqual(req.unified_msg_origin, self.UMO)
        self.assertEqual(req.conversation_id, "cid-6")
        self.assertEqual(req.persona_id, "p9")

    async def test_host_failure_returns_empty_values(self):
        # SDK 跨进程契约差异：本体 DB 操作失败会抛异常，SDK 经 RPC 后宿主
        # 失败返回空值（cid=""、列表=[]、get_conversation=None）不抛异常。
        class _Err:
            def __getattr__(self, name):
                raise rpc_error()

        from astrbot.core.conversation_mgr import ConversationManager

        bridge = self.make_bridge()
        bridge._stub = _Err()
        bridge._probed = True
        mgr = ConversationManager(bridge)
        self.assertEqual(await mgr.get_curr_conversation_id(self.UMO), None)
        self.assertEqual(await mgr.new_conversation(self.UMO), "")
        self.assertIsNone(await mgr.get_conversation(self.UMO, "x"))
        self.assertEqual(await mgr.get_conversations(self.UMO), [])


class TestHostServicePersona(_HostBridgeCase):
    """人格管理 4 RPC（persona_mgr 读方法）。本体依据：
    astrbot/core/persona_mgr.py。"""

    def _mgr(self, responses=None):
        from astrbot.core.persona_mgr import PersonaManager

        bridge = self.make_bridge(responses)
        return PersonaManager(bridge), bridge

    async def test_get_personas_via_get_all_personas(self):
        # 本体 persona_mgr.py:172 get_all_personas() → list[Persona]（PO 含
        # persona_id/system_prompt/begin_dialogs/...）。
        personas = [
            {"persona_id": "p1", "name": "助手", "system_prompt": "你是助手",
             "folder_id": "", "begin_dialogs": []},
            {"persona_id": "p2", "name": "猫娘", "system_prompt": "喵", "folder_id": "f1"},
        ]
        mgr, bridge = self._mgr({
            "GetPersonas": plugin_pb2.PersonasResponse(
                personas_json=[_json_bytes(p) for p in personas]
            ),
        })
        out = await mgr.get_all_personas()
        bridge._stub.last("GetPersonas")  # Empty 请求
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].persona_id, "p1")
        self.assertEqual(out[0].name, "助手")
        self.assertEqual(out[0].system_prompt, "你是助手")
        # .personas 属性（本体 persona_mgr.personas）
        self.assertEqual(mgr.personas[1].persona_id, "p2")
        # .personas_v3（本体 personas_v3 list[Personality]：含 prompt/name 键；
        # SDK 契约：宿主 dict 透传 + 补 prompt 别名，name 取宿主显示名）
        self.assertEqual(mgr.personas_v3[0]["prompt"], "你是助手")
        self.assertEqual(mgr.personas_v3[0]["name"], "助手")
        self.assertEqual(mgr.personas_v3[0]["persona_id"], "p1")

    async def test_get_default_persona(self):
        # 本体 persona_mgr.py:63 get_default_persona_v3(umo=None) → Personality
        # （dict 风格，含 "name"）。
        mgr, bridge = self._mgr({
            "GetDefaultPersona": plugin_pb2.PersonaResponse(
                persona_json=_json_bytes(
                    {"persona_id": "p1", "name": "助手", "system_prompt": "你是助手"}
                )
            ),
        })
        persona = await mgr.get_default_persona_v3("umo-1")
        req = bridge._stub.last("GetDefaultPersona")
        self.assertEqual(req.umo, "umo-1")
        # 本体 Personality 是 dict 形态（persona["name"] 访问）
        self.assertEqual(persona["name"], "助手")
        self.assertEqual(persona.get("name"), "助手")

        # 宿主未配置人格 → 回退 {"name": "default"}。
        # 契约差异：本体回退 DEFAULT_PERSONALITY（完整 Personality 对象，
        # prompt="You are a helpful and friendly assistant."），SDK 仅含 name。
        mgr2, _ = self._mgr({"GetDefaultPersona": plugin_pb2.PersonaResponse()})
        fallback = await mgr2.get_default_persona_v3("")
        self.assertEqual(fallback, {"name": "default"})

    async def test_get_persona_tree(self):
        # 本体 persona_mgr.py:276 get_folder_tree() → list[dict]（嵌套 children）。
        folders = [{"folder_id": "f1", "name": "目录A", "children": [
            {"folder_id": "f2", "name": "子目录", "children": []},
        ]}]
        personas = [{"persona_id": "p2", "name": "猫娘", "folder_id": "f1"}]
        mgr, bridge = self._mgr({
            "GetPersonaTree": plugin_pb2.PersonaTreeResponse(
                folders_json=[_json_bytes(f) for f in folders],
                personas_json=[_json_bytes(p) for p in personas],
            ),
        })
        tree = await mgr.get_folder_tree()
        bridge._stub.last("GetPersonaTree")  # Empty 请求
        self.assertEqual(tree[0]["folder_id"], "f1")
        self.assertEqual(tree[0]["children"][0]["name"], "子目录")
        # get_personas_by_folder（本体 persona_mgr.py:176）
        in_f1 = await mgr.get_personas_by_folder("f1")
        self.assertEqual([p.persona_id for p in in_f1], ["p2"])

    async def test_resolve_selected_persona(self):
        # 本体 persona_mgr.py:75 resolve_selected_persona(*, umo,
        # conversation_persona_id, platform_name, provider_settings=None)
        # （keyword-only）→ (persona_id, persona, force_applied_persona_id,
        # use_webchat_special_default)。
        # SDK 契约差异：本体第 2 位是 Personality 对象、第 4 位是 bool；
        # SDK 经 RPC 后第 2 位是 persona_name 字符串、第 4 位是 persona_prompt。
        mgr, bridge = self._mgr({
            "ResolveSelectedPersona": plugin_pb2.ResolvePersonaResponse(
                persona_id="p1", persona_name="助手", persona_prompt="你是助手",
                force_applied_persona_id="pf", is_default=False,
            ),
        })
        persona_id, persona_name, force_applied, prompt = await mgr.resolve_selected_persona(
            umo="umo-1",
            conversation_persona_id="p1",
            platform_name="aiocqhttp",
            provider_settings={"default_personality": "p1"},
        )
        req = bridge._stub.last("ResolveSelectedPersona")
        self.assertEqual(req.umo, "umo-1")
        self.assertEqual(req.conversation_persona_id, "p1")
        self.assertEqual(req.platform_name, "aiocqhttp")
        self.assertEqual(json.loads(req.provider_settings_json), {"default_personality": "p1"})
        self.assertEqual(persona_id, "p1")
        self.assertEqual(persona_name, "助手")
        self.assertEqual(force_applied, "pf")
        self.assertEqual(prompt, "你是助手")

        # 宿主无解析结果 → ("[%None]", "", None, "")（本体 persona_id 为
        # "[%None]" 语义一致）
        mgr2, _ = self._mgr({"ResolveSelectedPersona": plugin_pb2.ResolvePersonaResponse()})
        out = await mgr2.resolve_selected_persona(umo="umo-1", conversation_persona_id=None, platform_name="")
        self.assertEqual(out, ("[%None]", "", None, ""))


class TestHostServiceProvider(_HostBridgeCase):
    """Provider 管理 4 RPC（ListProviders/GetUsingProvider/SetProvider/
    GetProviderModels）。本体依据：astrbot/core/provider/manager.py。"""

    PROV = {"id": "p1", "model": "gpt-x", "type": "openai", "provider_type": "chat_completion"}

    async def test_list_providers(self):
        # 本体 provider/manager.py provider_insts 属性 → list[Provider 实例]；
        # SDK 经 ListProviders RPC 返回包装 Provider（meta().id/model 可读）。
        from astrbot.core.provider.manager import ProviderManager

        bridge = self.make_bridge({
            "ListProviders": plugin_pb2.ProvidersResponse(
                providers_json=[_json_bytes(self.PROV)]
            ),
        })
        mgr = ProviderManager(bridge)
        insts = mgr.provider_insts
        req = bridge._stub.last("ListProviders")
        self.assertEqual(req.capability, "chat_completion")
        self.assertEqual(len(insts), 1)
        self.assertEqual(insts[0].meta_id, "p1")
        self.assertEqual(insts[0].get_model(), "gpt-x")

    async def test_get_using_provider(self):
        # 本体 provider/manager.py:281 get_using_provider(provider_type: ProviderType,
        # umo=None) → Providers|None。SDK 契约差异：本体第一参数为必填
        # ProviderType 枚举；SDK 兼容枚举/能力字符串，缺省 "chat_completion"。
        from astrbot.core.provider.entities import ProviderType
        from astrbot.core.provider.manager import ProviderManager

        bridge = self.make_bridge({
            "GetUsingProvider": plugin_pb2.ProviderResponse(
                provider_json=_json_bytes(self.PROV)
            ),
        })
        mgr = ProviderManager(bridge)
        prov = mgr.get_using_provider(provider_type=ProviderType.CHAT_COMPLETION, umo="umo-1")
        req = bridge._stub.last("GetUsingProvider")
        self.assertEqual(req.umo, "umo-1")
        self.assertEqual(req.capability, "chat_completion")
        self.assertEqual(prov.meta().id, "p1")
        self.assertEqual(prov.meta().provider_type, ProviderType.CHAT_COMPLETION)
        # 缓存（对齐本体 curr_provider_inst 属性）
        self.assertIs(mgr.curr_provider_inst, prov)

        # 宿主无 provider → None（本体返回 None）
        mgr2, _ = (lambda b: (ProviderManager(b), b))(
            self.make_bridge({"GetUsingProvider": plugin_pb2.ProviderResponse()})
        )
        self.assertIsNone(mgr2.get_using_provider("chat_completion", ""))

    async def test_set_provider(self):
        # 本体 provider/manager.py:146 set_provider(provider_id, provider_type,
        # umo=None)：提供商不存在抛 ValueError（"Provider {id} does not exist
        # and cannot be set."）。
        from astrbot.core.provider.entities import ProviderType
        from astrbot.core.provider.manager import ProviderManager

        bridge = self.make_bridge({
            "ListProviders": plugin_pb2.ProvidersResponse(
                providers_json=[_json_bytes(self.PROV)]
            ),
        })
        mgr = ProviderManager(bridge)
        await mgr.set_provider("p1", ProviderType.CHAT_COMPLETION, umo="umo-1")
        req = bridge._stub.last("SetProvider")
        self.assertEqual(req.umo, "umo-1")
        self.assertEqual(req.provider_id, "p1")
        self.assertEqual(req.capability, "chat_completion")

        # 不存在 → ValueError（对齐本体）
        with self.assertRaises(ValueError):
            await mgr.set_provider("ghost", ProviderType.CHAT_COMPLETION, umo="umo-1")

    async def test_get_provider_models(self):
        # 本体 provider/provider.py:91 get_models() → list[str]；SDK 经
        # GetProviderModels RPC（provider.py:154 薄壳）。
        from astrbot.core.provider.manager import ProviderManager

        bridge = self.make_bridge({
            "ListProviders": plugin_pb2.ProvidersResponse(
                providers_json=[_json_bytes(self.PROV)]
            ),
            "GetProviderModels": plugin_pb2.ProviderModelsResponse(models=["m1", "m2"]),
        })
        mgr = ProviderManager(bridge)
        prov = mgr.provider_insts[0]
        models = await prov.get_models()
        req = bridge._stub.last("GetProviderModels")
        self.assertEqual(req.provider_id, "p1")
        self.assertEqual(models, ["m1", "m2"])
        # bridge 直测
        self.assertEqual(bridge.get_provider_models("p1"), ["m1", "m2"])


class TestHostServiceStarManager(_HostBridgeCase):
    """插件/Star 管理 6 RPC。本体依据：astrbot/core/star/star_manager.py。"""

    def _star_json(self, **kw):
        star = {"name": "plug_x", "author": "a", "desc": "d", "version": "1.0",
                "module_path": "m", "activated": True, "repo": ""}
        star.update(kw)
        return _json_bytes(star)

    async def test_get_plugin_registry(self):
        # 本体 star_manager.get_all_stars() → list[StarMetadata]；SDK 经
        # GetPluginRegistry RPC → StarInfo（name/author/desc/version/activated）。
        bridge = self.make_bridge({
            "GetPluginRegistry": plugin_pb2.StarsResponse(
                stars_json=[self._star_json(), self._star_json(name="plug_y", activated=False)]
            ),
        })
        out = bridge.get_plugin_registry()
        self.assertEqual(len(out), 2)
        from astrbot.core.star.context import Context

        self.patch_host_bridge(bridge)
        ctx = Context()
        stars = ctx.get_all_stars()
        self.assertEqual(stars[0].name, "plug_x")
        self.assertEqual(stars[0].version, "1.0")
        self.assertTrue(stars[0].activated)
        self.assertFalse(stars[1].activated)

    async def test_get_star(self):
        # 本体 star_manager.get_registered_star(name)；未找到 → None。
        bridge = self.make_bridge({
            "GetStar": plugin_pb2.StarResponse(star_json=self._star_json()),
        })
        star = bridge.get_star("plug_x")
        req = bridge._stub.last("GetStar")
        self.assertEqual(req.name, "plug_x")
        self.assertEqual(star["name"], "plug_x")

        bridge2 = self.make_bridge({"GetStar": plugin_pb2.StarResponse()})
        self.assertIsNone(bridge2.get_star("nope"))

    async def test_set_plugin_enabled_turn_off_on(self):
        # 本体 star_manager.py:1980 turn_off_plugin / 2056 turn_on_plugin：
        # 插件不存在抛异常（"插件不存在。"），存在则启停。
        from astrbot.core.star.star_manager import PluginManager

        bridge = self.make_bridge({
            "GetStar": plugin_pb2.StarResponse(star_json=self._star_json()),
        })
        mgr = PluginManager(bridge=bridge)
        await mgr.turn_off_plugin("plug_x")
        req = bridge._stub.last("SetPluginEnabled")
        self.assertEqual(req.plugin_name, "plug_x")
        self.assertFalse(req.enabled)
        await mgr.turn_on_plugin("plug_x")
        self.assertTrue(bridge._stub.last("SetPluginEnabled").enabled)

        # 插件不存在 → Exception（对齐本体），且不发 SetPluginEnabled RPC
        bridge2 = self.make_bridge({"GetStar": plugin_pb2.StarResponse()})
        mgr2 = PluginManager(bridge=bridge2)
        with self.assertRaises(Exception):
            await mgr2.turn_off_plugin("ghost")
        self.assertIsNone(bridge2._stub.last("SetPluginEnabled"))

    async def test_install_plugin(self):
        # 本体 star_manager.py:1614 install_plugin(repo_url, proxy, ...)；
        # SDK 契约差异：本体在进程内 git 克隆+装依赖，SDK 仅转发 repo 给宿主
        # InstallPlugin RPC，proxy 等参数宿主忽略。
        from astrbot.core.star.star_manager import PluginManager

        bridge = self.make_bridge()
        mgr = PluginManager(bridge=bridge)
        await mgr.install_plugin("https://github.com/x/y", proxy="http://p")
        req = bridge._stub.last("InstallPlugin")
        self.assertEqual(req.repo, "https://github.com/x/y")

    async def test_uninstall_plugin(self):
        # 本体 star_manager.py:1753 uninstall_plugin(plugin_name, delete_config,
        # delete_data)；SDK 契约差异：细粒度清理参数由宿主处理，RPC 仅传名。
        from astrbot.core.star.star_manager import PluginManager

        bridge = self.make_bridge()
        mgr = PluginManager(bridge=bridge)
        await mgr.uninstall_plugin("plug_x", delete_config=True)
        req = bridge._stub.last("UninstallPlugin")
        self.assertEqual(req.plugin_name, "plug_x")

    async def test_list_command_descriptors(self):
        # 宿主聚合的命令描述符（helps 类插件跨进程枚举全部指令；本体同进程
        # 注册表直接遍历，SDK 经 ListCommandDescriptors RPC）。
        desc = {"plugin_name": "p", "command": "cmd1", "aliases": ["c1"],
                "description": "d", "permission": "everyone", "enabled": True}
        bridge = self.make_bridge({
            "ListCommandDescriptors": plugin_pb2.CommandDescriptorsResponse(
                descriptors_json=[_json_bytes(desc)]
            ),
        })
        out = bridge.list_command_descriptors()
        self.assertEqual(out[0]["command"], "cmd1")
        self.assertEqual(out[0]["permission"], "everyone")


class TestHostServicePlatform(_HostBridgeCase):
    """ListPlatforms（platform_manager.get_insts 入口）。本体依据：
    astrbot/core/platform/manager.py:316 get_insts() → list[平台实例]。"""

    async def test_list_platforms(self):
        # SDK 契约差异：本体 get_insts() 返回真实平台适配器实例（同进程）；
        # SDK 经 ListPlatforms RPC 返回跨进程 _PlatformStub 占位（metadata
        # id/type/name 元数据可读，bot 经 call_action 转发宿主）。
        platforms = [
            {"id": "aiocqhttp_main", "type": "aiocqhttp", "name": "aiocqhttp",
             "display_name": "QQ"},
            {"id": "w1", "type": "webchat", "name": "webchat", "display_name": "Web"},
        ]
        bridge = self.make_bridge({
            "ListPlatforms": plugin_pb2.PlatformsResponse(
                platforms_json=[_json_bytes(p) for p in platforms]
            ),
        })
        self.patch_host_bridge(bridge)
        out = bridge.list_platforms()
        self.assertEqual(out[0]["id"], "aiocqhttp_main")

        # 管理器入口：platform_manager.get_insts()（本体 manager.py:316）
        from astrbot.core.platform.manager import PlatformManager

        pm = PlatformManager()
        insts = pm.get_insts()
        self.assertEqual(len(insts), 2)
        self.assertEqual(insts[0].metadata.id, "aiocqhttp_main")
        self.assertEqual(insts[0].meta().type, "aiocqhttp")
        # get_platform（本体 Context.get_platform(platform_type) 语义）
        self.assertIs(pm.get_platform("w1"), insts[1])


class TestHostServiceSessionWait(_HostBridgeCase):
    """RegisterSessionWait / UnregisterSessionWait（session_waiter 入口）。
    本体依据：astrbot/core/utils/session_waiter.py:124 register_wait。"""

    async def test_register_and_unregister_via_session_waiter(self):
        from astrbot.core.utils.session_waiter import (
            USER_SESSIONS,
            DefaultSessionFilter,
            SessionWaiter,
        )

        bridge = self.make_bridge({
            "RegisterSessionWait": plugin_pb2.RegisterSessionWaitResponse(wait_id="w-1"),
        })
        self.patch_host_bridge(bridge)
        waiter = SessionWaiter(DefaultSessionFilter(), "aiocqhttp:GroupMessage:1", False)
        # register_wait 内部调用 _register_host_wait（session_waiter.py:144）
        await waiter._register_host_wait(45)
        req = bridge._stub.last("RegisterSessionWait")
        self.assertEqual(req.umo, "aiocqhttp:GroupMessage:1")
        self.assertEqual(req.timeout_seconds, 45)
        self.assertEqual(waiter.wait_id, "w-1")

        # 注销：waiter._cleanup → unregister_session_wait_async（派发到 SDK 常驻
        # loop；跨线程 create_task 需泵 loop 才能被调度）
        waiter.wait_id = "w-1"
        waiter._cleanup()
        self.assertNotIn(waiter.session_id, USER_SESSIONS)
        self.assertTrue(self.wait_until_pumping(
            lambda: bridge._stub.last("UnregisterSessionWait") is not None
        ))
        req2 = bridge._stub.last("UnregisterSessionWait")
        self.assertEqual(req2.wait_id, "w-1")

    async def test_unregister_empty_wait_id_skips_rpc(self):
        # wait_id 为空（宿主注册失败降级为本地等待）→ 不发注销 RPC
        bridge = self.make_bridge()
        bridge.unregister_session_wait("")
        self.assertIsNone(bridge._stub.last("UnregisterSessionWait"))

    async def test_register_failure_returns_empty(self):
        # SDK 契约差异：宿主不支持/RPC 失败 → ""（调用方降级为纯本地等待）；
        # 本体无宿主注册概念（纯本地）。
        class _Err:
            def RegisterSessionWait(self, req, timeout=None):
                raise rpc_error()

        bridge = self.make_bridge()
        bridge._stub = _Err()
        bridge._probed = True
        self.assertEqual(bridge.register_session_wait("umo", 30), "")


class TestHostServiceBridgeHook(_HostBridgeCase):
    """RegisterBridgeHook / UnregisterBridgeHook（botpy/telegram 兼容层用）。

    入口：botpy.Client 实例化时注册（botpy/__init__.py:223）、telegram
    Application 实例化时注册（telegram/__init__.py:326）；此处经 bridge
    直测请求构造（兼容层薄壳无逻辑）。本体无跨进程钩子注册概念（同进程
    直接分发）。"""

    async def test_register_and_unregister_bridge_hook(self):
        bridge = self.make_bridge(plugin_name="myplug")
        self.assertTrue(bridge.register_bridge_hook("__botpy_bridge__"))
        req = bridge._stub.last("RegisterBridgeHook")
        self.assertEqual(req.plugin_name, "myplug")
        self.assertEqual(req.hook_name, "__botpy_bridge__")

        self.assertTrue(bridge.unregister_bridge_hook("__botpy_bridge__"))
        req2 = bridge._stub.last("UnregisterBridgeHook")
        self.assertEqual(req2.plugin_name, "myplug")
        self.assertEqual(req2.hook_name, "__botpy_bridge__")

    async def test_bridge_hook_failure_returns_false(self):
        class _Err:
            def RegisterBridgeHook(self, req, timeout=None):
                raise rpc_error()

        bridge = self.make_bridge()
        bridge._stub = _Err()
        bridge._probed = True
        self.assertFalse(bridge.register_bridge_hook("__telegram_bridge__"))


class TestHostServiceBlob(_HostBridgeCase):
    """CreateBlob / ReadBlob / GetBlobInfo / ReleaseBlob。

    宿主 blob 由 SendMessage 链内部使用（serialize 媒体组件 payload 超过
    inline 阈值 1MB 时走 FileReference handle，宿主读 blob 组装平台消息）；
    bridge 之外的插件面无独立管理器封装，故按任务说明直接经 bridge 方法
    （FakeHostStub）验证请求构造与响应解析。本体无 blob 概念（同进程内存
    直读文件）；SDK 跨进程契约：handle_id 为高熵随机串（非路径，防穿越）。"""

    FILE = {"handle_id": "h-1", "size": 4, "mime_type": "image/png",
            "filename": "a.png", "expires_at": 123456}

    async def test_create_blob(self):
        bridge = self.make_bridge({
            "CreateBlob": plugin_pb2.CreateBlobResponse(
                file=plugin_pb2.FileReference(**self.FILE)
            ),
        })
        out = bridge.create_blob(b"data", mime_type="image/png", filename="a.png", ttl_seconds=60)
        req = bridge._stub.last("CreateBlob")
        self.assertEqual(req.data, b"data")
        self.assertEqual(req.mime_type, "image/png")
        self.assertEqual(req.filename, "a.png")
        self.assertEqual(req.ttl_seconds, 60)
        self.assertEqual(out, self.FILE)

    async def test_read_blob(self):
        bridge = self.make_bridge({
            "ReadBlob": plugin_pb2.ReadBlobResponse(data=b"chunk-1", eof=True, total_size=7),
        })
        self.assertEqual(bridge.read_blob("h-1", offset=0, limit=7), b"chunk-1")
        req = bridge._stub.last("ReadBlob")
        self.assertEqual(req.handle_id, "h-1")
        self.assertEqual(req.offset, 0)
        self.assertEqual(req.limit, 7)

    async def test_get_blob_info(self):
        bridge = self.make_bridge({
            "GetBlobInfo": plugin_pb2.GetBlobInfoResponse(
                file=plugin_pb2.FileReference(**self.FILE)
            ),
        })
        out = bridge.get_blob_info("h-1")
        self.assertEqual(bridge._stub.last("GetBlobInfo").handle_id, "h-1")
        self.assertEqual(out["handle_id"], "h-1")
        self.assertEqual(out["size"], 4)

    async def test_release_blob(self):
        bridge = self.make_bridge()
        self.assertTrue(bridge.release_blob("h-1"))
        self.assertEqual(bridge._stub.last("ReleaseBlob").handle_id, "h-1")

        class _Err:
            def ReleaseBlob(self, req, timeout=None):
                raise rpc_error()

        bridge2 = self.make_bridge()
        bridge2._stub = _Err()
        bridge2._probed = True
        self.assertFalse(bridge2.release_blob("h-1"))


class TestHostServiceSkills(_HostBridgeCase):
    """ListSkills / ListSkillsV2 / SetSkillActive / DeleteSkill（skill_manager
    入口 + bridge 直测）。本体依据：astrbot/core/skills/skill_manager.py:520
    list_skills(*, active_only=False, runtime="local", show_sandbox_path=True)
    → list[SkillInfo]。"""

    SKILL = {"name": "weather", "description": "查询天气", "path": "data/skills/weather/SKILL.md",
             "active": True, "source_type": "local_only"}

    async def test_list_skills_v2_via_skill_manager(self):
        # skill_manager.list_skills 优先走 bridge.list_skills_v2（宿主 sandbox
        # 视图）。SDK 契约差异：本体本地扫描 skills 目录；SDK 经宿主 RPC。
        from astrbot.core.skills.skill_manager import SkillInfo, SkillManager

        bridge = self.make_bridge({
            "ListSkillsV2": plugin_pb2.SkillsResponse(
                skills_json=[_json_bytes(self.SKILL)]
            ),
        })
        self.patch_host_bridge(bridge)
        mgr = SkillManager()
        skills = mgr.list_skills(active_only=True, runtime="sandbox", show_sandbox_path=True)
        req = bridge._stub.last("ListSkillsV2")
        self.assertTrue(req.active_only)
        self.assertEqual(req.runtime, "sandbox")
        self.assertTrue(req.show_sandbox_path)
        self.assertEqual(len(skills), 1)
        self.assertIsInstance(skills[0], SkillInfo)
        self.assertEqual(skills[0].name, "weather")
        self.assertTrue(skills[0].active)

        # 旧宿主桥（无 list_skills_v2 封装）回退 bridge.list_skills →
        # ListSkills RPC（实例属性 None 屏蔽类方法，模拟旧版 bridge）
        bridge2 = self.make_bridge({
            "ListSkills": plugin_pb2.SkillsResponse(
                skills_json=[_json_bytes(self.SKILL)]
            ),
        })
        bridge2.list_skills_v2 = None
        self.patch_host_bridge(bridge2)
        skills2 = SkillManager().list_skills()
        self.assertEqual(skills2[0].name, "weather")

    async def test_list_skills_active_only_client_filter(self):
        # bridge.list_skills(active_only=True)：客户端过滤 active（本体
        # list_skills 的 active_only 过滤语义）。
        bridge = self.make_bridge({
            "ListSkills": plugin_pb2.SkillsResponse(
                skills_json=[
                    _json_bytes(self.SKILL),
                    _json_bytes({**self.SKILL, "name": "off", "active": False}),
                ]
            ),
        })
        out = bridge.list_skills(active_only=True)
        self.assertEqual([s["name"] for s in out], ["weather"])

    async def test_set_skill_active_and_delete_skill(self):
        # skill_manager.set_skill_active(name, active) / delete_skill(name)
        # 薄壳转发宿主（本体同名方法写 skills.json；SDK 经 RPC）。
        from astrbot.core.skills.skill_manager import SkillManager

        bridge = self.make_bridge()
        self.patch_host_bridge(bridge)
        mgr = SkillManager()
        mgr.set_skill_active("weather", False)
        req = bridge._stub.last("SetSkillActive")
        self.assertEqual(req.name, "weather")
        self.assertFalse(req.active)
        mgr.delete_skill("weather")
        self.assertEqual(bridge._stub.last("DeleteSkill").name, "weather")
        # bridge 直测
        self.assertTrue(bridge.set_skill_active("w2", True))
        self.assertTrue(bridge.delete_skill("w2"))


class TestHostServicePMHistory(_HostBridgeCase):
    """Get/Insert/Update/DeletePlatformMessageHistory（4 RPC）。

    本体依据：astrbot/core/platform_message_history_mgr.py（insert(platform_id,
    user_id, content, sender_id, sender_name, llm_checkpoint_id, max_messages) /
    get(platform_id, user_id, page=1, page_size=200) / update(message_id,
    content, llm_checkpoint_id) / delete_by_id(message_id)）。

    SDK 跨进程契约差异：本体管理器经 DB 存取（page/page_size 分页）；SDK 的
    context.message_history_manager 为本地 JSON 文件持久化实现
    （astrbot/core/utils/message_history_manager.py），不经宿主 RPC；宿主
    PMHistory 4 RPC 无管理器封装，仅 HostBridge 方法（供插件直调），get 的
    分页映射为 limit（最近 N 条）。"""

    RECORD = {"id": 7, "platform_id": "aiocqhttp", "user_id": "g:1",
              "sender_id": "u1", "content": {"type": "user", "message": []},
              "llm_checkpoint_id": "", "created_at": "2026-09-01T00:00:00Z"}

    async def test_get_platform_message_history(self):
        bridge = self.make_bridge({
            "GetPlatformMessageHistory": plugin_pb2.PMHistoryRecordsResponse(
                records_json=[_json_bytes(self.RECORD)]
            ),
        })
        recs = bridge.get_platform_message_history("aiocqhttp", "g:1", limit=50)
        req = bridge._stub.last("GetPlatformMessageHistory")
        self.assertEqual(req.platform_id, "aiocqhttp")
        self.assertEqual(req.user_id, "g:1")
        self.assertEqual(req.limit, 50)
        self.assertEqual(recs[0]["id"], 7)
        # 默认 limit=200（本体 get page_size=200 默认值对齐）
        bridge.get_platform_message_history("aiocqhttp", "g:1")
        self.assertEqual(bridge._stub.last("GetPlatformMessageHistory").limit, 200)

    async def test_insert_platform_message_history(self):
        bridge = self.make_bridge({
            "InsertPlatformMessageHistory": plugin_pb2.PMHistoryRecordResponse(
                record_json=_json_bytes({**self.RECORD, "id": 99})
            ),
        })
        rec = bridge.insert_platform_message_history(
            "aiocqhttp", "g:1", {"type": "user", "message": ["hi"]},
            sender_id="u1", llm_checkpoint_id="ck-1", max_messages=100,
        )
        req = bridge._stub.last("InsertPlatformMessageHistory")
        self.assertEqual(req.platform_id, "aiocqhttp")
        self.assertEqual(req.user_id, "g:1")
        self.assertEqual(req.sender_id, "u1")
        self.assertEqual(json.loads(req.content_json), {"type": "user", "message": ["hi"]})
        self.assertEqual(req.llm_checkpoint_id, "ck-1")
        self.assertEqual(req.max_messages, 100)
        self.assertEqual(rec["id"], 99)

    async def test_update_platform_message_history(self):
        bridge = self.make_bridge()
        self.assertTrue(
            bridge.update_platform_message_history(7, content={"x": 1}, llm_checkpoint_id="ck-2")
        )
        req = bridge._stub.last("UpdatePlatformMessageHistory")
        self.assertEqual(req.id, 7)
        self.assertEqual(json.loads(req.content_json), {"x": 1})
        self.assertEqual(req.llm_checkpoint_id, "ck-2")

    async def test_delete_platform_message_history(self):
        bridge = self.make_bridge()
        self.assertTrue(bridge.delete_platform_message_history(7))
        self.assertEqual(bridge._stub.last("DeletePlatformMessageHistory").id, 7)


class TestHostServiceKB(_HostBridgeCase):
    """KBRetrieve / KBUploadFromURL / KBListKBs（kb_mgr 入口）。本体依据：
    astrbot/core/knowledge_base/kb_mgr.py:282 retrieve。"""

    def _patch(self, responses=None):
        bridge = self.make_bridge(responses)
        self.patch_host_bridge(bridge)
        return bridge

    def _mgr(self):
        from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager

        return KnowledgeBaseManager()

    async def test_kb_retrieve_returns_context_and_results(self):
        # 本体 kb_mgr.py:282 retrieve(query, kb_names, top_k_fusion=20,
        # top_m_final=5) → {"context_text": str, "results": list[dict]}。
        results = [{"chunk_id": "c1", "doc_id": "d1", "kb_id": "kb1", "kb_name": "kbA",
                    "doc_name": "doc", "content": "文本", "score": 0.9}]
        self._patch({
            "KBListKBs": plugin_pb2.KBListResponse(
                kbs_json=[_json_bytes({"kb_id": "kb1", "kb_name": "kbA"})]
            ),
            "KBRetrieve": plugin_pb2.KBRetrieveResponse(
                context_text="以下是相关的知识库内容...", results_json=json.dumps(results)
            ),
        })
        out = await self._mgr().retrieve("查询", ["kbA"], top_k_fusion=20, top_m_final=5)
        self.assertIn("context_text", out)
        self.assertIn("results", out)
        self.assertEqual(out["results"][0]["chunk_id"], "c1")
        self.assertEqual(out["results"][0]["score"], 0.9)

    async def test_kb_retrieve_request_fields_via_bridge(self):
        bridge = self._patch({
            "KBRetrieve": plugin_pb2.KBRetrieveResponse(context_text="x", results_json="[]"),
        })
        bridge.kb_retrieve("q", ["kbA", "kbB"], top_k_fusion=30, top_m_final=8)
        req = bridge._stub.last("KBRetrieve")
        self.assertEqual(req.query, "q")
        self.assertEqual(list(req.kb_names), ["kbA", "kbB"])
        self.assertEqual(req.top_k_fusion, 30)
        self.assertEqual(req.top_m_final, 8)

    async def test_kb_retrieve_no_result_none(self):
        # 本体：检索已执行但无结果 → None（kb_mgr.py:316）。
        self._patch({
            "KBListKBs": plugin_pb2.KBListResponse(
                kbs_json=[_json_bytes({"kb_id": "kb1", "kb_name": "kbA"})]
            ),
            "KBRetrieve": plugin_pb2.KBRetrieveResponse(context_text="", results_json="[]"),
        })
        self.assertIsNone(await self._mgr().retrieve("q", ["kbA"]))

    async def test_kb_retrieve_empty_names_no_kbs_empty_dict(self):
        # 本体：kb_names 为空且无可检索知识库 → {}（kb_mgr.py:306）。
        self._patch({"KBRetrieve": plugin_pb2.KBRetrieveResponse()})
        self.assertEqual(await self._mgr().retrieve("q", []), {})

    async def test_kb_retrieve_all_unavailable_raises_value_error(self):
        # 本体：指定的知识库全部不可用 → ValueError（kb_mgr.py:300
        # "所有请求的知识库均不可用"）。SDK 对齐该语义。
        self._patch({
            "KBListKBs": plugin_pb2.KBListResponse(
                kbs_json=[_json_bytes({"kb_id": "kb1", "kb_name": "kbA"})]
            ),
        })
        with self.assertRaises(ValueError):
            await self._mgr().retrieve("q", ["ghost"])

    async def test_kb_upload_from_url(self):
        # 本体 kb_mgr upload_from_url(kb_id, url, chunk_size=512,
        # chunk_overlap=50)；SDK 经 KBUploadFromURL RPC（<=0 用宿主默认）。
        bridge = self._patch()
        await self._mgr().upload_from_url(
            "kb1", "https://e/doc.pdf", chunk_size=256, chunk_overlap=30
        )
        req = bridge._stub.last("KBUploadFromURL")
        self.assertEqual(req.kb_id, "kb1")
        self.assertEqual(req.url, "https://e/doc.pdf")
        self.assertEqual(req.chunk_size, 256)
        self.assertEqual(req.chunk_overlap, 30)
        self.assertTrue(bridge.kb_upload_from_url("kb1", "https://e/2"))

    async def test_kb_list_kbs(self):
        # 本体 kb_mgr list_kbs() → list[KnowledgeBase]；SDK 经 KBListKBs RPC
        # 返回元数据 dict 列表（kb_id/kb_name/...）。
        bridge = self._patch({
            "KBListKBs": plugin_pb2.KBListResponse(
                kbs_json=[_json_bytes({"kb_id": "kb1", "kb_name": "kbA", "doc_count": 3})]
            ),
        })
        kbs = await self._mgr().list_kbs()
        self.assertEqual(kbs[0]["kb_id"], "kb1")
        self.assertEqual(kbs[0]["doc_count"], 3)
        # get_kb / get_kb_by_name（本体同名方法）
        kb = await self._mgr().get_kb("kb1")
        self.assertEqual(kb["kb_name"], "kbA")
        by_name = await self._mgr().get_kb_by_name("kbA")
        self.assertEqual(by_name["kb_id"], "kb1")
        self.assertIsNone(await self._mgr().get_kb("nope"))


class TestHostServiceFileToken(_HostBridgeCase):
    """RegisterFileToken（file_token_service.register_file 入口）。本体依据：
    astrbot/core/file_token_service.py:29 register_file(file_path, timeout=None)
    → str（uuid4 token）。"""

    async def test_register_file_via_file_token_service(self):
        # 本体返回 uuid4 token 字符串；SDK 返回宿主 token。
        # SDK 跨进程契约差异：本体路径不存在抛 FileNotFoundError
        # （file_token_service.py:52-56）；SDK 路径校验在宿主侧执行，桥不可用
        # /登记失败返回 None，不抛异常。
        from astrbot.core.file_token_service import FileTokenService

        bridge = self.make_bridge({
            "RegisterFileToken": plugin_pb2.RegisterFileTokenResponse(token="tok-123"),
        })
        self.patch_host_bridge(bridge)
        svc = FileTokenService()
        token = await svc.register_file("/data/a.png", timeout=60)
        req = bridge._stub.last("RegisterFileToken")
        self.assertEqual(req.path, "/data/a.png")
        self.assertEqual(req.timeout_sec, 60)  # 本体 timeout → proto timeout_sec
        self.assertEqual(token, "tok-123")

        # timeout=None → 0（宿主默认 TTL，对齐本体 default_timeout 语义）
        await svc.register_file("/data/a.png")
        self.assertEqual(bridge._stub.last("RegisterFileToken").timeout_sec, 0)

        # 宿主返回空 token → None（降级，不抛 FileNotFoundError）
        bridge2 = self.make_bridge({"RegisterFileToken": plugin_pb2.RegisterFileTokenResponse()})
        self.patch_host_bridge(bridge2)
        self.assertIsNone(await FileTokenService().register_file("/not/exist"))

    async def test_register_file_token_bridge_direct(self):
        bridge = self.make_bridge({
            "RegisterFileToken": plugin_pb2.RegisterFileTokenResponse(token="t"),
        })
        self.assertEqual(bridge.register_file_token("/x", 30), "t")
        self.assertEqual(bridge._stub.last("RegisterFileToken").timeout_sec, 30)


class TestHostServiceCron(_HostBridgeCase):
    """Cron 五 RPC + FeedCronJob（cron_manager 入口）。本体依据：
    astrbot/core/cron/manager.py:145 add_basic_job / :172 add_active_job
    （keyword-only、返回 CronJob PO）/ update_job / delete_job / list_jobs /
    run_job_now（本体均为 async）。"""

    HOST_JOB = {"job_id": "h1", "name": "n", "job_type": "cron",
                "cron_expression": "*/5 * * * *", "payload": {"a": 1},
                "description": "d", "enabled": True,
                "next_run_time": "2026-09-01T00:00:00+00:00"}

    def setUp(self):
        super().setUp()
        import astrbot.core.utils.cron_manager as cm

        self._cm = cm
        self._old_manager = cm._manager

    def tearDown(self):
        self._cm._manager = self._old_manager
        super().tearDown()

    def _mgr(self, responses=None):
        from astrbot.core.utils.cron_manager import CronJobManager

        bridge = self.make_bridge(responses)
        self.patch_host_bridge(bridge)
        return CronJobManager(), bridge

    async def test_add_basic_job_creates_host_cron(self):
        # 本体 add_basic_job(*, name, cron_expression, handler, description=None,
        # timezone=None, payload=None, enabled=True, persistent=False) → CronJob。
        # SDK 契约差异：本体本地调度执行 handler；SDK 转发宿主 CronCreate
        # （job_type=cron），handler 保留本地供宿主 FeedCronJob 回传路由。
        mgr, bridge = self._mgr({
            "CronCreate": plugin_pb2.CronJobResponse(
                job_json=_json_bytes(self.HOST_JOB)
            ),
        })
        handler = lambda: None  # noqa: E731
        record = await mgr.add_basic_job(
            name="n", cron_expression="*/5 * * * *", handler=handler,
            timezone="Asia/Shanghai", payload={"a": 1}, description="d",
        )
        req = bridge._stub.last("CronCreate")
        self.assertEqual(req.name, "n")
        self.assertEqual(req.job_type, "cron")
        self.assertEqual(req.cron_expression, "*/5 * * * *")
        self.assertEqual(req.timezone, "Asia/Shanghai")
        self.assertEqual(json.loads(req.payload_json), {"a": 1})
        self.assertEqual(req.description, "d")
        self.assertTrue(req.enabled)
        self.assertFalse(req.run_once)
        # 返回记录对齐本体 CronJob PO 字段
        self.assertEqual(record.job_id, "h1")
        self.assertEqual(record.job_type, "cron")
        self.assertEqual(record.cron_expression, "*/5 * * * *")
        self.assertEqual(record.payload, {"a": 1})
        self.assertTrue(record.enabled)
        # 宿主路径不本地调度；handler 保留（FeedCronJob 回传路由用）
        self.assertIs(mgr._basic_handlers["h1"], handler)

    async def test_add_active_job_run_once_maps_to_once(self):
        # 本体 add_active_job(*, name, cron_expression, payload, ...)（本体
        # manager.py:157-160：run_once 且 run_at 时把 run_at 写入 payload）。
        mgr, bridge = self._mgr({
            "CronCreate": plugin_pb2.CronJobResponse(
                job_json=_json_bytes({**self.HOST_JOB, "job_type": "once"})
            ),
        })
        run_at = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        record = await mgr.add_active_job(
            name="wake", cron_expression=None, payload={"p": 1},
            run_once=True, run_at=run_at,
        )
        req = bridge._stub.last("CronCreate")
        self.assertEqual(req.job_type, "once")
        self.assertTrue(req.run_once)
        self.assertEqual(req.run_at, "2026-09-01T12:00:00+00:00")  # RFC3339
        self.assertEqual(record.job_type, "once")

    async def test_update_job_partial_fields(self):
        # 本体 update_job(job_id, **kwargs) → CronJob|None（本体 manager.py:203）。
        mgr, bridge = self._mgr({
            "CronUpdate": plugin_pb2.CronJobResponse(
                job_json=_json_bytes({**self.HOST_JOB, "name": "n2", "enabled": False})
            ),
        })
        record = await mgr.update_job("h1", name="n2", enabled=False)
        req = bridge._stub.last("CronUpdate")
        self.assertEqual(req.job_id, "h1")
        fields = json.loads(req.fields_json)
        self.assertEqual(fields.get("name"), "n2")
        self.assertIs(fields.get("enabled"), False)
        self.assertEqual(record.name, "n2")
        self.assertFalse(record.enabled)

    async def test_delete_job(self):
        # 本体 delete_job(job_id)（本体 manager.py:212，async）。
        mgr, bridge = self._mgr()
        await mgr.delete_job("h1")
        self.assertEqual(bridge._stub.last("CronDelete").job_id, "h1")

    async def test_list_jobs(self):
        # 本体 list_jobs(job_type=None) → list[CronJob]（本体 manager.py:217）。
        mgr, bridge = self._mgr({
            "CronList": plugin_pb2.CronJobsResponse(
                jobs_json=[_json_bytes(self.HOST_JOB)]
            ),
        })
        records = await mgr.list_jobs("cron")
        req = bridge._stub.last("CronList")
        self.assertEqual(req.job_type, "cron")
        self.assertEqual(records[0].job_id, "h1")
        self.assertEqual(records[0].next_run_time.year, 2026)
        # job_type 空 = 全部类型
        await mgr.list_jobs()
        self.assertEqual(bridge._stub.last("CronList").job_type, "")

    async def test_run_job_now(self):
        # 本体 run_job_now(job_id)（本体 manager.py:307，async）。
        mgr, bridge = self._mgr()
        await mgr.run_job_now("h1")
        self.assertEqual(bridge._stub.last("CronRunNow").job_id, "h1")


class TestHostServiceMcp(_HostBridgeCase):
    """McpListTools / McpCallTool（list_host_mcp_tools / HostMcpTool 入口）。
    本体依据：本体 MCP 工具经 mcp_market 客户端（同进程）；SDK 跨进程契约：
    经宿主 MCP 桥 RPC（本体无此 RPC，行为对齐 MCP 工具错误语义）。"""

    async def test_mcp_list_tools_via_entry(self):
        from astrbot.core.agent.mcp_client import list_host_mcp_tools

        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        bridge = self.make_bridge({
            "McpListTools": plugin_pb2.McpToolsResponse(
                tools_json=[_json_bytes({
                    "server": "srv", "name": "search",
                    "description": "搜索", "schema_json": json.dumps(schema),
                })]
            ),
        })
        self.patch_host_bridge(bridge)
        tools = list_host_mcp_tools()
        bridge._stub.last("McpListTools")  # Empty 请求
        self.assertEqual(tools[0]["server"], "srv")
        self.assertEqual(tools[0]["name"], "search")
        self.assertEqual(tools[0]["schema"], schema)

    async def test_mcp_call_tool_via_host_tool(self):
        from astrbot.core.agent.mcp_client import HostMcpTool

        bridge = self.make_bridge({
            "McpCallTool": plugin_pb2.McpCallToolResponse(
                result_json=_json_bytes({"content": "r"}), is_error=False, text="r"
            ),
        })
        self.patch_host_bridge(bridge)
        tool = HostMcpTool(server="srv", name="search", description="d", schema={})
        out = await tool.call(None, query="x")
        req = bridge._stub.last("McpCallTool")
        self.assertEqual(req.server, "srv")
        self.assertEqual(req.tool_name, "search")
        self.assertEqual(json.loads(req.arguments_json), {"query": "x"})
        self.assertEqual(out, "r")

    async def test_mcp_call_tool_error_semantics(self):
        # is_error=True → "error: " 前缀（对齐本体 MCP 工具错误语义）
        from astrbot.core.agent.mcp_client import HostMcpTool

        bridge = self.make_bridge({
            "McpCallTool": plugin_pb2.McpCallToolResponse(is_error=True, text="bad"),
        })
        self.patch_host_bridge(bridge)
        tool = HostMcpTool(server="srv", name="search", description="d", schema={})
        out = await tool.call(None)
        self.assertEqual(out, "error: bad")

    async def test_mcp_call_tool_bridge_direct(self):
        bridge = self.make_bridge({
            "McpCallTool": plugin_pb2.McpCallToolResponse(text="t", is_error=False),
        })
        out = bridge.mcp_call_tool("srv", "tool", {"a": 1})
        req = bridge._stub.last("McpCallTool")
        self.assertEqual(json.loads(req.arguments_json), {"a": 1})
        self.assertEqual(out, {"result": None, "is_error": False, "text": "t"})
        # 桥未就绪 → is_error=True（契约：失败返回错误 dict 而非抛异常）。
        # 抛 RpcError 的 stub 触发异常路径（ensure_connected 补丁态恒 True，
        # 不会走真实 dial 挂起）。
        bridge2 = self.make_bridge()

        class _ErrStub:
            def McpCallTool(self, req, timeout=None):
                raise rpc_error()

        bridge2._stub = _ErrStub()
        out2 = bridge2.mcp_call_tool("s", "t", {})
        self.assertTrue(out2["is_error"])


# ── 通道 B：PluginService（宿主→插件，15 RPC）────────────────────────────

def _make_plugin_module(code: str, name: str):
    """动态创建插件模块并加载（与 tests/run_tests.py _make_plugin 相同路径）。"""
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    exec(compile(code, f"<{name}>", "exec"), mod.__dict__)
    return mod


class _PluginServiceCase(unittest.TestCase):
    """PluginService 测试基类：servicer 工厂（mark_ready + mock get_bridge
    防 dial 挂起，先例 tests/run_tests.py:140-157）。"""

    _module_seq = 0

    def make_servicer(self, code, plugin_id="p_srv"):
        from astrbot._bridge.dispatch import PluginServiceServicer
        from astrbot.core.star.star import star_map

        type(self)._module_seq += 1
        mod_name = f"{plugin_id}_{type(self)._module_seq}"
        mod = _make_plugin_module(code, mod_name)
        self.addCleanup(sys.modules.pop, mod_name, None)
        md = star_map[mod_name]
        self.addCleanup(star_map.pop, mod_name, None)
        # star_cls_type 未填（插件类未继承 Star / 仅占位）：用最小实例兜底，
        # 保持 svc.inst 可用（GetConfigSchema/Cleanup 等读实例配置）
        cls = md.star_cls_type or type("EmptyStar", (), {"__init__": lambda self, ctx: None})
        self.inst = cls(None)  # Star.__init__(context)
        svc = PluginServiceServicer(md.name, md.version or "1.0.0", "测试插件", "tester",
                                    plugin_dir=self._plugin_dir)
        svc.mark_ready()  # 放行 ready 门闩（Register 不再等 120s）
        svc.inst = self.inst
        self.svc = svc
        self.md = md
        self._mod_name = mod_name
        return svc

    def setUp(self):
        import tempfile as _tf

        self._plugin_dir = _tf.mkdtemp(prefix="astrbot_plugin_dir_")

    def register(self, svc):
        """执行 Register（mock 宿主桥防 dial 挂起），返回 RegisterResponse。

        Register 内部 mark_registered 会把状态从 RUNNING 拉回 REGISTERED
        （回退告警），随后 mark_instanced 恢复 RUNNING——Handle* RPC 的
        _wait_instanced 才能放行。"""
        fake_bridge = mock.MagicMock()
        fake_bridge.ensure_connected.return_value = True
        fake_bridge.get_plugin_registry.return_value = []
        with mock.patch("astrbot._bridge.host.get_bridge", return_value=fake_bridge):
            req = plugin_pb2.RegisterRequest(protocol_version=2)
            resp = svc.Register(req, None)
        # 隔离：其他测试（如 run_tests.py TestRegistry）遗留在全局
        # star_handlers_registry 的 handler 元数据会被 build_registry 收进
        # svc.filter_handlers/commands（HandleFilter 按 md 查找会先命中旧
        # 条目）。只保留本插件模块（md.module_path = 动态模块名）的条目。
        mod_path = self._mod_name
        svc.filter_handlers = [
            (n, h, i) for n, h, i in svc.filter_handlers
            if n.startswith(mod_path)
        ]
        svc.commands = {
            k: v for k, v in svc.commands.items()
            if getattr(v[0], "handler_md", None) is not None
            and v[0].handler_md.handler_module_path == mod_path
        }
        svc.hook_handlers = {
            k: v for k, v in svc.hook_handlers.items()
            if k.startswith(mod_path)
        }
        svc.mark_instanced()
        return resp

    def hook_name(self, event, index=0):
        """从（已隔离过滤的）svc.hook_handlers 找 event 对应的第 index 个
        handler 全名。"""
        names = [
            name for name, (ev, _, _) in self.svc.hook_handlers.items()
            if ev == event
        ]
        if not names or index >= len(names):
            self.fail(f"hook {event} 未注册：{self.svc.hook_handlers}")
        return names[index]


def _sdk_event(**kw):
    base = dict(
        type="message", platform="aiocqhttp", platform_id="p1",
        message_type="GroupMessage", self_id="self", sender_id="u1",
        sender_name="alice", conv_id="g1", is_group=True, is_at_bot=True,
        message_str="/cmd 5", plain_text="/cmd 5", message_id="m1",
        timestamp=1700000000,
    )
    base.update(kw)
    return plugin_pb2.SDKEvent(**base)


class TestPluginServiceRegister(_PluginServiceCase):
    """Register：元数据 + handler 收集（commands/filters/hooks/tools/
    web_apis/config schema）。本体语义：同进程注册表在 Register 阶段由
    star registry 收集（本体 star_manager 的注册语义）。"""

    CODE = '''
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

@register("reg_plugin", "注册插件", "测试", "1.0.0")
class RegPlugin(Star):
    @filter.command("rcmd", alias={"rc"})
    async def rcmd(self, event: AstrMessageEvent):
        yield event.plain_result("ok")

    @filter.regex(r"^rxx")
    async def rrx(self, event: AstrMessageEvent):
        yield event.plain_result("rx")

    @filter.llm_tool(name="rt_tool")
    async def rtool(self, event: AstrMessageEvent, x: str) -> str:
        """动态工具。

        Args:
            x (string): 输入
        """
        return x

    @filter.on_llm_request()
    async def llm_req(self, event: AstrMessageEvent, req):
        req.system_prompt += "X"

    @filter.on_decorating_result()
    async def deco(self, event: AstrMessageEvent):
        pass
'''

    def test_register_metadata_and_handler_collection(self):
        # 插件目录 _conf_schema.json → 宿主 FlatSchema 格式
        with open(os.path.join(self._plugin_dir, "_conf_schema.json"), "w", encoding="utf-8") as f:
            json.dump({"token": {"type": "string", "default": ""}}, f)

        svc = self.make_servicer(self.CODE, plugin_id="regplug")
        svc.web_apis = [("/regplug/api", lambda: None, ["GET"], "测试 API")]
        resp = self.register(svc)

        # 元数据（RegisterRequest/Reregister 元数据：name/version/description/author）
        self.assertEqual(resp.name, "reg_plugin")
        self.assertEqual(resp.version, "1.0.0")
        self.assertEqual(resp.description, "测试插件")
        self.assertEqual(resp.author, "tester")
        self.assertEqual(resp.protocol_version, 2)

        # 命令收集（name/aliases；权限缺省 everyone）
        cmd = next(c for c in resp.commands if c.name == "rcmd")
        self.assertIn("rc", cmd.aliases)
        self.assertEqual(cmd.permission, "everyone")

        # 过滤器收集（regex）
        self.assertTrue(any("rrx" in f.name for f in resp.filters), f"{resp.filters}")

        # 钩子收集（event 名对齐本体 hook 命名）
        events = {h.event for h in resp.hooks}
        self.assertIn("on_llm_request", events)
        self.assertIn("on_decorating_result", events)

        # LLM 工具收集（name/params_json）
        tool = next(t for t in resp.tools if t.name == "rt_tool")
        params = json.loads(tool.params_json)
        self.assertEqual(params["properties"]["x"]["type"], "string")

        # Web API 收集（context.register_web_api 注册的路由）
        api = next(a for a in resp.web_apis if a.route == "/regplug/api")
        self.assertEqual(list(api.methods), ["GET"])

        # 配置 schema（{"type":"object","properties":{...}}，FlatSchema 格式）
        schema = json.loads(resp.config_schema_json)
        self.assertEqual(schema["type"], "object")
        self.assertIn("token", schema["properties"])

    def test_register_protocol_mismatch_aborts(self):
        # P1 协议协商：Host 上报版本不匹配 → FAILED_PRECONDITION abort
        from astrbot._bridge.dispatch import P1_PROTOCOL_VERSION

        svc = self.make_servicer(self.CODE, plugin_id="regplug2")

        class _Ctx:
            def __init__(self):
                self.aborted = None

            def abort(self, code, msg):
                self.aborted = (code, msg)

        ctx = _Ctx()
        req = plugin_pb2.RegisterRequest(protocol_version=P1_PROTOCOL_VERSION + 1)
        import grpc

        with mock.patch(
            "astrbot._bridge.host.get_bridge",
            return_value=mock.MagicMock(get_plugin_registry=lambda: []),
        ):
            # P1 协议协商：版本不匹配 → grpc FAILED_PRECONDITION abort
            #（对齐 P1 spec "不匹配明确失败"；dispatch.py 已 import grpc）。
            svc.Register(req, ctx)
            self.assertIsNotNone(ctx.aborted, "协商不匹配必须 abort")
            code, msg = ctx.aborted
            self.assertEqual(code, grpc.StatusCode.FAILED_PRECONDITION)
            self.assertIn("protocol version mismatch", msg)


class TestPluginServiceCommand(_PluginServiceCase):
    """HandleCommand：命令分发 + 参数转换 + 结果链/stop/sent。本体语义：
    本体 pipeline 直接调 handler 并把结果挂 event（SDK 经 RPC 响应回传）。"""

    CODE = '''
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

@register("cmd_plugin", "", "", "1.0.0")
class CmdPlugin(Star):
    @filter.command("hcmd")
    async def hcmd(self, event: AstrMessageEvent, a: int):
        yield event.plain_result("got " + str(a))

    @filter.command("hstop")
    async def hstop(self, event: AstrMessageEvent):
        event.stop_event()
        yield event.plain_result("stopped")

    @filter.command("hboom")
    async def hboom(self, event: AstrMessageEvent):
        raise ValueError("炸了")
'''

    def _req(self, name, args):
        return plugin_pb2.HandleCommandRequest(
            name=name, args=args, event=_sdk_event(message_str=f"/{name} " + " ".join(args))
        )

    def test_handle_command_result_chain_and_params(self):
        svc = self.make_servicer(self.CODE, plugin_id="cmdplug")
        resp = self.register(svc)
        self.assertIn("hcmd", {c.name for c in resp.commands})

        out = svc.HandleCommand(self._req("hcmd", ["5"]), None)
        # 结果链（P1 原生 Component）
        self.assertEqual(len(out.chain), 1)
        self.assertEqual(out.chain[0].type, "Plain")
        self.assertEqual(out.chain[0].text, "got 5")  # 参数 int("5") 转换
        # EventResult 复合消息 + 旧字段（新旧宿主双兼容语义）
        self.assertTrue(out.result.handled)
        self.assertFalse(out.stop)
        self.assertFalse(out.sent)

    def test_handle_command_stop_propagation(self):
        # event.stop_event() → stop=True（对齐本体 _force_stopped 语义）
        svc = self.make_servicer(self.CODE, plugin_id="cmdplug2")
        self.register(svc)
        out = svc.HandleCommand(self._req("hstop", []), None)
        self.assertTrue(out.stop)
        self.assertTrue(out.result.stop_propagation)

    def test_handle_command_param_error(self):
        # 参数类型错误 → resp.text 错误提示，handled=True（不抛异常）
        svc = self.make_servicer(self.CODE, plugin_id="cmdplug3")
        self.register(svc)
        out = svc.HandleCommand(self._req("hcmd", ["abc"]), None)
        self.assertIn("参数错误", out.text)
        self.assertTrue(out.result.handled)

    def test_handle_command_handler_exception(self):
        # handler 抛异常 → resp.text 错误提示（对齐本体插件错误不崩管线）
        svc = self.make_servicer(self.CODE, plugin_id="cmdplug4")
        self.register(svc)
        out = svc.HandleCommand(self._req("hboom", []), None)
        self.assertIn("插件执行失败", out.text)
        self.assertTrue(out.result.handled)

    def test_handle_command_unknown_command_empty(self):
        # 未注册命令 → 空响应（宿主按未处理继续管线）
        svc = self.make_servicer(self.CODE, plugin_id="cmdplug5")
        self.register(svc)
        out = svc.HandleCommand(self._req("nope", []), None)
        self.assertEqual(len(out.chain), 0)
        self.assertFalse(out.result.handled)


class TestPluginServiceFilter(_PluginServiceCase):
    """HandleFilter：过滤器分发（先跑过滤器，全部匹配才调 handler；
    注册表 miss fail-closed 拒绝）。"""

    CODE = '''
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

@register("flt_plugin", "", "", "1.0.0")
class FltPlugin(Star):
    @filter.regex(r"^hello")
    async def frx(self, event: AstrMessageEvent):
        return False  # 拦截（本体过滤器返回 False 语义）
'''

    def test_filter_match_calls_handler_allow_false(self):
        svc = self.make_servicer(self.CODE, plugin_id="fltplug")
        resp = self.register(svc)
        fname = svc.filter_handlers[0][0]  # 隔离后的本插件过滤器全名
        req = plugin_pb2.HandleFilterRequest(
            name=fname, event=_sdk_event(message_str="hello world")
        )
        out = svc.HandleFilter(req, None)
        self.assertFalse(out.allow)  # handler 返回 False → 拦截
        self.assertTrue(out.result.handled)

    def test_filter_not_match_passes_without_calling(self):
        # 不匹配 → 放行（allow=True）且不调用 handler（对齐本体过滤器语义）
        svc = self.make_servicer(self.CODE, plugin_id="fltplug2")
        resp = self.register(svc)
        fname = svc.filter_handlers[0][0]  # 隔离后的本插件过滤器全名
        req = plugin_pb2.HandleFilterRequest(
            name=fname, event=_sdk_event(message_str="bye")
        )
        out = svc.HandleFilter(req, None)
        self.assertTrue(out.allow)

    def test_filter_unregistered_fail_closed(self):
        # 未注册过滤器名 → fail-closed 拒绝（allow=False，对齐 fail-closed 语义）
        svc = self.make_servicer(self.CODE, plugin_id="fltplug3")
        self.register(svc)
        req = plugin_pb2.HandleFilterRequest(
            name="ghost_filter", event=_sdk_event(message_str="hello")
        )
        self.assertFalse(svc.HandleFilter(req, None).allow)


class TestPluginServiceHook(_PluginServiceCase):
    """HandleHook：on_llm_response / on_decorating_result / on_plugin_loaded。
    本体语义（astrbot-py）：各钩子是多位置参数（on_llm_response(event,
    response)、on_plugin_loaded(metadata)），结果钩子 handler(event) 经
    event.get_result()/set_result() 读写结果链。"""

    CODE = '''
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

@register("hook_plugin", "", "", "1.0.0")
class HookPlugin(Star):
    def __init__(self, context):
        super().__init__(context)
        self.recorded = []

    @filter.on_llm_response()
    async def on_resp(self, event: AstrMessageEvent, response):
        self.recorded.append(("resp", response.completion_text))

    @filter.on_decorating_result()
    async def deco(self, event: AstrMessageEvent):
        event.get_result().chain[0].text = "deco"

    @filter.on_plugin_loaded()
    async def on_loaded(self, metadata):
        self.recorded.append(("loaded", metadata))
'''

    def test_handle_hook_llm_response_payload(self):
        # on_llm_response(event, response)：payload → LLMResponse
        svc = self.make_servicer(self.CODE, plugin_id="hookplug")
        resp = self.register(svc)
        name = self.hook_name("on_llm_response")
        req = plugin_pb2.HandleHookRequest(
            name=name, event=_sdk_event(),
            payload_json=_json_bytes({"text": "hi"}),
        )
        out = svc.HandleHook(req, None)
        self.assertTrue(out.handled)
        self.assertEqual(self.inst.recorded, [("resp", "hi")])
        self.assertEqual(out.result.handled, True)

    def test_handle_hook_decorating_result_chain(self):
        # on_decorating_result：入站链预置 event 结果 → handler 原地改 →
        # 回传修改后链（对齐本体 result_decorate 语义）
        svc = self.make_servicer(self.CODE, plugin_id="hookplug2")
        resp = self.register(svc)
        name = self.hook_name("on_decorating_result")
        req = plugin_pb2.HandleHookRequest(
            name=name, event=_sdk_event(),
            chain=[plugin_pb2.Component(type="Plain", text="orig")],
        )
        out = svc.HandleHook(req, None)
        self.assertTrue(out.handled)
        self.assertEqual(len(out.chain), 1)
        self.assertEqual(out.chain[0].text, "deco")

    def test_handle_hook_plugin_loaded_metadata(self):
        # on_plugin_loaded(metadata)：payload dict 作首个位置参数（本体签名
        # handler(metadata)，非 event）
        svc = self.make_servicer(self.CODE, plugin_id="hookplug3")
        resp = self.register(svc)
        name = self.hook_name("on_plugin_loaded")
        req = plugin_pb2.HandleHookRequest(
            name=name, payload_json=_json_bytes({"plugin_name": "hp"})
        )
        out = svc.HandleHook(req, None)
        self.assertTrue(out.handled)
        self.assertEqual(self.inst.recorded[-1], ("loaded", {"plugin_name": "hp"}))

    def test_handle_hook_unknown_name_not_handled(self):
        # 未注册钩子名 → handled=False（宿主按未处理继续）
        svc = self.make_servicer(self.CODE, plugin_id="hookplug4")
        self.register(svc)
        req = plugin_pb2.HandleHookRequest(name="ghost", event=_sdk_event())
        self.assertFalse(svc.HandleHook(req, None).handled)


class TestPluginServiceLLMRequest(_PluginServiceCase):
    """HandleLLMRequest：on_llm_request 钩子（检查/修改 system_prompt 与
    user_prompt）。本体语义：on_llm_request(event, req) 修改 ProviderRequest。"""

    CODE = '''
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

@register("llmr_plugin", "", "", "1.0.0")
class LlmrPlugin(Star):
    @filter.on_llm_request()
    async def req_hook(self, event: AstrMessageEvent, req):
        req.system_prompt = req.system_prompt + "X"

    @filter.on_llm_request()
    async def stop_hook(self, event: AstrMessageEvent, req):
        req.stop = True
'''

    def test_handle_llm_request_modifies_prompts(self):
        svc = self.make_servicer(self.CODE, plugin_id="llmrplug")
        resp = self.register(svc)
        name = self.hook_name("on_llm_request")
        req = plugin_pb2.HandleLLMRequestRequest(
            name=name, event=_sdk_event(), system_prompt="S", user_prompt="U",
        )
        out = svc.HandleLLMRequest(req, None)
        self.assertEqual(out.system_prompt, "SX")
        self.assertEqual(out.user_prompt, "U")
        self.assertFalse(out.stop)

    def test_handle_llm_request_stop_flag(self):
        svc = self.make_servicer(self.CODE, plugin_id="llmrplug2")
        self.register(svc)
        stop_name = self.hook_name("on_llm_request", index=1)  # stop_hook 注册在后
        req = plugin_pb2.HandleLLMRequestRequest(
            name=stop_name, event=_sdk_event(), system_prompt="S", user_prompt="U",
        )
        out = svc.HandleLLMRequest(req, None)
        self.assertTrue(out.stop)  # 不调 LLM（对齐本体 req.stop 语义）

    def test_handle_llm_request_unknown_name_echoes(self):
        # 未注册钩子 → 原样回显 prompt（宿主继续默认 LLM 流程）
        svc = self.make_servicer(self.CODE, plugin_id="llmrplug3")
        self.register(svc)
        req = plugin_pb2.HandleLLMRequestRequest(
            name="ghost", event=_sdk_event(), system_prompt="S", user_prompt="U",
        )
        out = svc.HandleLLMRequest(req, None)
        self.assertEqual(out.system_prompt, "S")
        self.assertEqual(out.user_prompt, "U")


class TestPluginServiceTool(_PluginServiceCase):
    """HandleTool：LLM 函数工具调用。PR#2 语义（本体 astrbot-py）：
    call 型工具首参是 context（ContextWrapper，context.context.event 可达），
    run 型是 event。"""

    CODE = '''
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

@register("tool_plugin", "", "", "1.0.0")
class ToolPlugin(Star):
    @filter.llm_tool(name="t_run")
    async def run_tool(self, event: AstrMessageEvent, query: str) -> str:
        """运行型工具。

        Args:
            query (string): 查询
        """
        return "run:" + query + ":" + event.message_str
'''

    def _register_tool(self, name, handler):
        from astrbot.core.provider.func_tool_manager import FuncTool, llm_tools

        llm_tools.remove_func(name)
        llm_tools.func_list.append(FuncTool(
            name=name,
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            description="测试工具",
            handler=handler,
        ))
        self.addCleanup(llm_tools.remove_func, name)

    def test_handle_tool_run_style_first_arg_event(self):
        # run 型（SDK 文档约定）：首参 event
        svc = self.make_servicer(self.CODE, plugin_id="toolplug")
        self.register(svc)
        req = plugin_pb2.HandleToolRequest(
            name="t_run", args_json=_json_bytes({"query": "q"}),
            event=_sdk_event(message_str="msg"),
        )
        out = svc.HandleTool(req, None)
        self.assertEqual(out.text, "run:q:msg")
        self.assertFalse(out.is_error)
        self.assertTrue(out.result.handled)

    def test_handle_tool_call_style_wraps_context_wrapper(self):
        # PR#2：call 型工具（本体 FunctionTool.call(context, **kwargs) 约定）
        # 首参包装 ContextWrapper，context.context.event 可达
        from astrbot.core.agent.run_context import ContextWrapper

        recorded = {}

        class CallStyle:
            async def call(self, context, query):
                recorded["wrapper"] = context
                return "call:" + query + ":" + context.context.event.message_str

        self._register_tool("t_call", CallStyle().call)
        svc = self.make_servicer(self.CODE, plugin_id="toolplug2")
        self.register(svc)
        req = plugin_pb2.HandleToolRequest(
            name="t_call", args_json=_json_bytes({"query": "q"}),
            event=_sdk_event(message_str="msg"),
        )
        out = svc.HandleTool(req, None)
        self.assertEqual(out.text, "call:q:msg")
        # 首参确为 ContextWrapper，且 .context.event 即原始事件（livingmemory
        # 插件经该访问链取事件）
        self.assertIsInstance(recorded["wrapper"], ContextWrapper)
        self.assertEqual(recorded["wrapper"].context.event.message_str, "msg")

    def test_handle_tool_missing_tool_is_error(self):
        # 未注册工具 → is_error=True（"工具 X 未找到"）
        svc = self.make_servicer(self.CODE, plugin_id="toolplug3")
        self.register(svc)
        req = plugin_pb2.HandleToolRequest(
            name="t_nope", args_json=b"{}",
            event=_sdk_event(message_str="m"),
        )
        out = svc.HandleTool(req, None)
        self.assertTrue(out.is_error)
        self.assertIn("未找到", out.text)

    def test_handle_tool_handler_exception_is_error(self):
        from astrbot.core.provider.func_tool_manager import FuncTool, llm_tools

        async def boom(event, query):
            raise ValueError("工具炸了")

        llm_tools.remove_func("t_err")
        llm_tools.func_list.append(FuncTool(
            name="t_err",
            parameters={"type": "object", "properties": {}},
            description="d", handler=boom,
        ))
        self.addCleanup(llm_tools.remove_func, "t_err")
        svc = self.make_servicer(self.CODE, plugin_id="toolplug4")
        self.register(svc)
        req = plugin_pb2.HandleToolRequest(
            name="t_err", args_json=_json_bytes({"query": "x"}),
            event=_sdk_event(message_str="m"),
        )
        out = svc.HandleTool(req, None)
        self.assertTrue(out.is_error)
        self.assertIn("执行失败", out.text)


class TestPluginServiceListToolsAndWebApis(_PluginServiceCase):
    """ListTools / ListWebApis：运行期实时拉取（Register 后注册的工具/
    路由无需重启即可被宿主发现）。"""

    def test_list_tools_reflects_live_registry(self):
        from astrbot.core.provider.func_tool_manager import FuncTool, llm_tools

        async def live_tool(event, x: str):
            return x

        llm_tools.remove_func("t_live")
        llm_tools.func_list.append(FuncTool(
            name="t_live",
            parameters={"type": "object", "properties": {"x": {"type": "string"}}},
            description="实时工具", handler=live_tool,
        ))
        self.addCleanup(llm_tools.remove_func, "t_live")
        svc = self.make_servicer(self.CODE_EMPTY, plugin_id="ltplug")
        self.register(svc)
        out = svc.ListTools(plugin_pb2.Empty(), None)
        tool = next(t for t in out.tools if t.name == "t_live")
        self.assertEqual(tool.description, "实时工具")
        self.assertEqual(json.loads(tool.params_json)["properties"]["x"]["type"], "string")

    CODE_EMPTY = '''
from astrbot.api.star import register

@register("lt_plugin", "", "", "1.0.0")
class LtPlugin:
    pass
'''

    def test_list_web_apis_reflects_runtime_routes(self):
        from astrbot.core.star.context import Context

        svc = self.make_servicer(self.CODE_EMPTY, plugin_id="waplug")
        self.register(svc)
        # 实例化阶段注册（context.register_web_api，本体 Context.registered_web_apis）
        ctx = Context()
        ctx.plugin_name = "waplug"

        async def handler(request, category: str = None):
            return {"ok": True}

        ctx.register_web_api("/waplug/emoji/<category>", handler, ["GET"], "表情")
        self.svc.web_apis = list(ctx.registered_web_apis)
        out = svc.ListWebApis(plugin_pb2.Empty(), None)
        api = out.web_apis[0]
        self.assertEqual(api.route, "/waplug/emoji/<category>")
        self.assertEqual(list(api.methods), ["GET"])
        self.assertEqual(api.description, "表情")


class TestPluginServiceWebRequest(_PluginServiceCase):
    """HandleWebRequest：宿主 /api/plug/<plugin>/<path> 网关转发。"""

    def test_handle_web_request_route_match_and_response(self):
        async def api_emoji(category: str = None):
            return {"status": "ok", "category": category}

        svc = self.make_servicer(self.CODE_EMPTY, plugin_id="webplug")
        self.register(svc)
        svc.web_apis = [("/webplug/emoji/<category>", api_emoji, ["GET"], "x")]
        req = plugin_pb2.HandleWebRequestRequest(
            method="GET", path="/webplug/emoji/happy",
            query=[plugin_pb2.WebKV(key="a", value="1")],
        )
        out = svc.HandleWebRequest(req, None)
        self.assertEqual(out.status_code, 200)
        self.assertEqual(json.loads(out.body), {"status": "ok", "category": "happy"})
        # 方法不匹配 / 未注册路由 → 404
        req2 = plugin_pb2.HandleWebRequestRequest(method="POST", path="/webplug/emoji/happy")
        self.assertEqual(svc.HandleWebRequest(req2, None).status_code, 404)
        req3 = plugin_pb2.HandleWebRequestRequest(method="GET", path="/webplug/nope")
        self.assertEqual(svc.HandleWebRequest(req3, None).status_code, 404)

    CODE_EMPTY = '''
from astrbot.api.star import register

@register("web_plugin", "", "", "1.0.0")
class WebPlugin:
    pass
'''


class TestPluginServiceHealthAndLogLevel(_PluginServiceCase):
    """HealthCheck / SetLogLevel。"""

    CODE_EMPTY = '''
from astrbot.api.star import register

@register("hl_plugin", "", "", "3.2.1")
class HlPlugin:
    pass
'''

    def test_health_check_reflects_instanced_state(self):
        # ok 反映实例化完成状态：宿主推送生命周期钩子前先探测
        svc = self.make_servicer(self.CODE_EMPTY, plugin_id="hlplug")
        out = svc.HealthCheck(plugin_pb2.Empty(), None)
        self.assertFalse(out.ok)
        self.assertEqual(out.version, "3.2.1")
        svc.mark_instanced()
        out2 = svc.HealthCheck(plugin_pb2.Empty(), None)
        self.assertTrue(out2.ok)

    def test_set_log_level(self):
        # level 为空串/非法 → 回 INFO；合法值改 root logger
        old_level = logging.getLogger().level
        self.addCleanup(logging.getLogger().setLevel, old_level)
        svc = self.make_servicer(self.CODE_EMPTY, plugin_id="hlplug2")
        out = svc.SetLogLevel(plugin_pb2.SetLogLevelRequest(level="debug"), None)
        self.assertIsInstance(out, plugin_pb2.Empty)
        self.assertEqual(logging.getLogger().level, logging.DEBUG)
        svc.SetLogLevel(plugin_pb2.SetLogLevelRequest(level="bogus"), None)
        self.assertEqual(logging.getLogger().level, logging.INFO)


class TestPluginServiceFeedSessionWait(_PluginServiceCase):
    """FeedSessionWait：宿主推送等待中 umo 的入站消息 → session_waiter
    try_trigger 匹配。本体依据：astrbot/core/utils/session_waiter.py
    （本体同进程经 SessionWaiter.trigger 喂入；SDK 契约：经 RPC 喂入，
    无匹配返回 handled=False）。"""

    CODE_EMPTY = '''
from astrbot.api.star import register

@register("fsw_plugin", "", "", "1.0.0")
class FswPlugin:
    pass
'''

    def setUp(self):
        super().setUp()
        # SessionWaiter.__init__ 里 asyncio.Future() 需要 main 线程事件循环
        try:
            self._loop = asyncio.get_event_loop()
            if self._loop.is_closed():
                raise RuntimeError
        except RuntimeError:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        from astrbot.core.utils.session_waiter import USER_SESSIONS

        self._saved_sessions = dict(USER_SESSIONS)
        USER_SESSIONS.clear()
        self.addCleanup(USER_SESSIONS.update, self._saved_sessions)
        self.addCleanup(USER_SESSIONS.clear)

    def _install_waiter(self, session_id):
        from astrbot.core.utils.session_waiter import (
            DefaultSessionFilter,
            SessionWaiter,
        )

        waiter = SessionWaiter(DefaultSessionFilter(), session_id, False)
        recorded = []

        async def handler(controller, event):
            recorded.append(event.message_str)
            controller.stop()

        waiter.handler = handler
        waiter.session_id = session_id
        from astrbot.core.utils.session_waiter import USER_SESSIONS

        USER_SESSIONS[session_id] = waiter
        self.addCleanup(USER_SESSIONS.pop, session_id, None)
        return waiter, recorded

    def test_feed_session_wait_matches_waiting_session(self):
        svc = self.make_servicer(self.CODE_EMPTY, plugin_id="fswplug")
        self.register(svc)
        umo = "aiocqhttp:GroupMessage:g1"
        waiter, recorded = self._install_waiter(umo)
        req = plugin_pb2.FeedSessionWaitRequest(
            # umo = platform_id:message_type:conv_id（AstrMessageEvent 拼接规则）
            event=_sdk_event(platform_id="aiocqhttp", conv_id="g1", message_str="答案 42")
        )
        out = svc.FeedSessionWait(req, None)
        self.assertTrue(out.handled)
        self.assertTrue(self.wait_until(lambda: recorded == ["答案 42"]))
        waiter.session_controller.stop()

    def test_feed_session_wait_no_match_not_handled(self):
        svc = self.make_servicer(self.CODE_EMPTY, plugin_id="fswplug2")
        self.register(svc)
        self._install_waiter("aiocqhttp:GroupMessage:other")
        req = plugin_pb2.FeedSessionWaitRequest(
            event=_sdk_event(platform_id="aiocqhttp", conv_id="g1", message_str="无匹配")
        )
        out = svc.FeedSessionWait(req, None)
        self.assertFalse(out.handled)

    wait_until = staticmethod(_HostBridgeCase.wait_until)


class TestPluginServiceConfigSchemaAndCleanup(_PluginServiceCase):
    """GetConfigSchema / Cleanup。"""

    CODE_EMPTY = '''
from astrbot.api.star import register

@register("gcs_plugin", "", "", "1.0.0")
class GcsPlugin:
    pass
'''

    def test_get_config_schema_reads_inst_config(self):
        # 本体语义：WebUI 配置对话框读取运行中 star 实例的 config.schema
        from types import SimpleNamespace

        svc = self.make_servicer(self.CODE_EMPTY, plugin_id="gcsplug")
        self.register(svc)
        schema = {"type": "object", "properties": {"a": {"type": "int"}}}
        svc.inst = SimpleNamespace(config=SimpleNamespace(schema=schema))
        out = svc.GetConfigSchema(plugin_pb2.Empty(), None)
        self.assertEqual(json.loads(out.schema_json), schema)

        # 取不到 → 空 bytes（宿主回退 Register 快照）
        svc2 = self.make_servicer(self.CODE_EMPTY, plugin_id="gcsplug2")
        out2 = svc2.GetConfigSchema(plugin_pb2.Empty(), None)
        self.assertEqual(out2.schema_json, b"")

    def test_cleanup_terminates_plugin_instances(self):
        from astrbot.core.star.star import star_map

        svc = self.make_servicer(self.CODE_EMPTY, plugin_id="clplug")
        self.register(svc)
        # star_map 注入带 star_cls 的假条目 → Cleanup 应调用 terminate_plugin
        fake_md = types.SimpleNamespace(star_cls=object(), name="fake")
        star_map["__fake_cleanup__"] = fake_md
        self.addCleanup(star_map.pop, "__fake_cleanup__", None)
        with mock.patch(
            "astrbot._bridge.loader.terminate_plugin", wraps=lambda md: calls.append(md)
        ) as mocked:
            calls = []
            out = svc.Cleanup(plugin_pb2.Empty(), None)
        self.assertIsInstance(out, plugin_pb2.Empty)
        self.assertEqual([c.name for c in calls], ["fake"])
        self.assertTrue(mocked.called)


class TestPluginServiceFeedCronJob(_PluginServiceCase):
    """FeedCronJob：宿主 cron 到点触发插件 basic 任务 → feed_cron_job 按
    job_id 匹配 handler。本体依据：astrbot/core/cron/manager.py
    _run_basic_job（handler(**payload)，payload 为空时 handler()）。
    handled true/false 分支均覆盖。"""

    CODE_EMPTY = '''
from astrbot.api.star import register

@register("fcj_plugin", "", "", "1.0.0")
class FcjPlugin:
    pass
'''

    def setUp(self):
        super().setUp()
        import astrbot.core.utils.cron_manager as cm

        self._cm = cm
        self._old_manager = cm._manager

    def tearDown(self):
        self._cm._manager = self._old_manager
        super().tearDown()

    def _mgr(self):
        from astrbot.core.utils.cron_manager import CronJobManager

        return CronJobManager()

    def test_feed_cron_job_sync_handler_handled_true(self):
        svc = self.make_servicer(self.CODE_EMPTY, plugin_id="fcjplug")
        self.register(svc)
        mgr = self._mgr()
        recorded = []
        event = threading.Event()

        def handler(**payload):
            recorded.append(payload)
            event.set()

        mgr._basic_handlers["job-1"] = handler
        req = plugin_pb2.FeedCronJobRequest(
            job_id="job-1", job_name="n",
            payload_json=_json_bytes({"a": 1, "_plugin_id": "fcj_plugin"}),
            run_at="2026-09-01T00:00:00Z",
        )
        out = svc.FeedCronJob(req, None)
        self.assertTrue(out.handled)  # 有匹配 handler 且已调度执行
        self.assertTrue(event.wait(3))
        # 本体 _run_basic_job 语义：handler(**payload)，宿主注入的 _plugin_id
        # 路由键在触发时剥离
        self.assertEqual(recorded, [{"a": 1}])

    def test_feed_cron_job_async_handler_handled_true(self):
        svc = self.make_servicer(self.CODE_EMPTY, plugin_id="fcjplug2")
        self.register(svc)
        mgr = self._mgr()
        recorded = []

        async def handler(**payload):
            recorded.append(payload)

        mgr._basic_handlers["job-2"] = handler
        req = plugin_pb2.FeedCronJobRequest(
            job_id="job-2", job_name="n2", payload_json=_json_bytes({"b": 2}),
        )
        out = svc.FeedCronJob(req, None)
        self.assertTrue(out.handled)
        self.assertTrue(self.wait_until(lambda: recorded == [{"b": 2}]))

    def test_feed_cron_job_no_matching_handler_false(self):
        # 无匹配 handler → handled=False（对齐 FeedSessionWait 语义）
        svc = self.make_servicer(self.CODE_EMPTY, plugin_id="fcjplug3")
        self.register(svc)
        self._mgr()
        req = plugin_pb2.FeedCronJobRequest(
            job_id="nope", job_name="x", payload_json=b"{}",
        )
        self.assertFalse(svc.FeedCronJob(req, None).handled)

    def test_feed_cron_job_bad_payload_false(self):
        # payload 解析失败 → handled=False
        svc = self.make_servicer(self.CODE_EMPTY, plugin_id="fcjplug4")
        self.register(svc)
        req = plugin_pb2.FeedCronJobRequest(
            job_id="job-3", job_name="x", payload_json=b"{bad json",
        )
        self.assertFalse(svc.FeedCronJob(req, None).handled)

    wait_until = staticmethod(_HostBridgeCase.wait_until)


if __name__ == "__main__":
    unittest.main(verbosity=2)
