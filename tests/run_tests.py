"""Python SDK 单元测试（不依赖 pytest，直接运行：python3 tests/run_tests.py）。

覆盖：装饰器收集/Register 元数据、序列化往返、go-plugin 握手、broker。
"""
import json
import os
import sys
import tempfile
import types  # noqa: F401  （供 _make_plugin 使用，保持在文件头部）
import unittest

# 插入仓库根目录（而非 astrbot/ 子目录）：测试都是
# `from astrbot._bridge... import ...` 风格，把 astrbot/ 本身加入搜索路径
# 会使 `astrbot` 包不可导入 → ModuleNotFoundError。
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestSerialize(unittest.TestCase):
    def test_component_roundtrip(self):
        from astrbot._bridge.serialize import component_from_json, component_to_json
        from astrbot.core.message.components import At, Image, Plain

        cases = [
            {"type": "Plain", "text": "hi"},
            {"type": "At", "target_id": "123", "name": "u"},
            {"type": "AtAll"},
            {"type": "Image", "url": "https://x/y.png"},
            {"type": "Reply", "id": "42"},
            {"type": "Face", "id": "1"},
        ]
        for c in cases:
            comp = component_from_json(c)
            out = component_to_json(comp)
            self.assertEqual(out["type"], c["type"], f"{c} -> {out}")
        # Plain -> 组件 -> JSON 属性一致
        p = component_from_json({"type": "Plain", "text": "abc"})
        self.assertIsInstance(p, Plain)
        self.assertEqual(component_to_json(p)["text"], "abc")
        # At
        a = component_from_json({"type": "At", "target_id": "456"})
        self.assertIsInstance(a, At)
        # Python 组件 -> JSON（Image url）
        self.assertEqual(
            component_to_json(Image.fromURL("http://a/b.png")),
            {"type": "Image", "url": "http://a/b.png"},
        )

    def test_event_from_json(self):
        from astrbot.core.platform.astr_message_event import AstrMessageEvent

        ev = AstrMessageEvent.from_event_json(
            {
                "type": "message",
                "platform": "qq_official",
                "self_id": "bot1",
                "sender_id": "u1",
                "sender_name": "小明",
                "conv_id": "g1",
                "is_group": True,
                "is_at_bot": True,
                "is_admin": True,
                "message_str": "/cmd 1",
                "plain_text": "/cmd 1",
                "message_id": "m1",
                "timestamp": 123,
                "chain": [{"type": "Plain", "text": "/cmd 1"}],
            }
        )
        self.assertEqual(ev.get_sender_id(), "u1")
        self.assertEqual(ev.get_sender_name(), "小明")
        self.assertEqual(ev.get_group_id(), "g1")
        self.assertTrue(ev.is_admin())
        self.assertEqual(ev.get_platform_name(), "qq_official")
        self.assertEqual(ev.session.message_type.value, "GroupMessage")

    def test_result_to_json(self):
        from astrbot._bridge.serialize import result_to_json
        from astrbot.core.message.message_event_result import EventResultType, MessageEventResult

        chain, stop = result_to_json("hello")
        self.assertEqual(chain, [{"type": "Plain", "text": "hello"}])
        r = MessageEventResult().message("a").url_image("https://x/y.png")
        chain2, _ = result_to_json(r)
        self.assertEqual(chain2[0]["type"], "Plain")
        self.assertEqual(chain2[1]["type"], "Image")
        r.stop_event()
        _, stop2 = result_to_json(r)
        self.assertTrue(stop2)
        _, stop3 = result_to_json(None)
        self.assertEqual(stop3, False)


class TestRegistry(unittest.TestCase):
    def _make_plugin(self):
        # 动态创建插件模块并加载（与 _bridge.loader 相同路径）
        import importlib
        import sys as _sys

        mod = types.ModuleType("pytest_dyn_plugin")
        code = '''
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

@register("dyn_plugin", "动态插件", "测试", "1.0.0")
class Dyn(Star):
    @filter.command("dc")
    async def cmd(self, event: AstrMessageEvent, a: int):
        yield event.plain_result(str(a))

    @filter.regex(r"^drx")
    async def rx(self, event: AstrMessageEvent):
        yield event.plain_result("rx")

    @filter.llm_tool(name="dyn_tool")
    async def tool(self, event: AstrMessageEvent, x: str) -> str:
        """动态工具。

        Args:
            x (string): 输入
        """
        return x

    @filter.on_llm_request()
    async def llm_req(self, event: AstrMessageEvent, req):
        req.system_prompt += "X"
'''
        _sys.modules[mod.__name__] = mod
        exec(compile(code, "<dyn>", "exec"), mod.__dict__)
        return mod

    def test_register_metadata(self):
        from astrbot._bridge.dispatch import PluginServiceServicer
        from astrbot.core.star.star import star_map

        mod = self._make_plugin()
        md = star_map.get(mod.__name__)
        self.assertIsNotNone(md, "Star 类未注册")
        self.assertEqual(md.name, "dyn_plugin")

        svc = PluginServiceServicer("dyn_plugin", "1.0.0", "", "", plugin_dir=tempfile.gettempdir())
        # 不加载真实实例（无宿主），仅验证 build_registry 收集。
        # 先放行 ready 门闩：Register 会 _ready.wait(timeout=120)，不放行
        # 会硬等 120s 后才注册出空 handler 集。
        svc.mark_ready()
        from astrbot._bridge.dispatch import star_handlers_registry as _s  # noqa

        # 无宿主环境：Register 内 _inject_host_commands 会经 HostBridge
        # 拉取宿主全局命令，桥未连接时 dial 重试 20 次（8s 超时 × 20）导致
        # 测试挂起 160s。mock get_bridge 返回空注册表，仅验证注册收集。
        from unittest import mock

        fake_bridge = mock.MagicMock()
        fake_bridge.ensure_connected.return_value = True
        fake_bridge.get_plugin_registry.return_value = []
        with mock.patch("astrbot._bridge.host.get_bridge", return_value=fake_bridge):
            from astrbot._bridge.gen import plugin_pb2
            resp = svc.Register(plugin_pb2.RegisterRequest(protocol_version=2), None)
        names = {c.name for c in resp.commands}
        self.assertIn("dc", names)
        tools = {t.name for t in resp.tools}
        self.assertIn("dyn_tool", tools)
        hooks = {h.event for h in resp.hooks}
        self.assertIn("on_llm_request", hooks)
        filters = {f.name for f in resp.filters}
        self.assertTrue(any("rx" in n for n in filters), f"filters={filters}")


class TestGoHandshake(unittest.TestCase):
    def test_magic_cookie(self):
        import astrbot._bridge.go_handshake as gh

        old = os.environ.get(gh.MAGIC_COOKIE_KEY)
        try:
            os.environ[gh.MAGIC_COOKIE_KEY] = "wrong"
            with self.assertRaises(SystemExit):
                gh.check_magic_cookie()
            os.environ[gh.MAGIC_COOKIE_KEY] = gh.MAGIC_COOKIE_VALUE
            gh.check_magic_cookie()  # 不抛
            del os.environ[gh.MAGIC_COOKIE_KEY]
            gh.check_magic_cookie()  # 未设置跳过
        finally:
            if old is None:
                os.environ.pop(gh.MAGIC_COOKIE_KEY, None)
            else:
                os.environ[gh.MAGIC_COOKIE_KEY] = old

    def test_no_multiplex(self):
        import astrbot._bridge.go_handshake as gh

        old = os.environ.get(gh.ENV_MULTIPLEX_GRPC)
        try:
            os.environ[gh.ENV_MULTIPLEX_GRPC] = "true"
            with self.assertRaises(SystemExit):
                gh.check_no_multiplex()
            os.environ[gh.ENV_MULTIPLEX_GRPC] = ""
            gh.check_no_multiplex()
        finally:
            if old is None:
                os.environ.pop(gh.ENV_MULTIPLEX_GRPC, None)
            else:
                os.environ[gh.ENV_MULTIPLEX_GRPC] = old

    def test_handshake_line(self):
        import io
        import sys as _sys

        import astrbot._bridge.go_handshake as gh

        buf = io.StringIO()
        old = _sys.stdout
        _sys.stdout = buf
        try:
            gh.print_handshake_line("127.0.0.1", 12345)
        finally:
            _sys.stdout = old
        self.assertEqual(buf.getvalue().strip(), "1|1|tcp|127.0.0.1:12345|grpc|")


class TestBroker(unittest.TestCase):
    def test_dial_timeout(self):
        from astrbot._bridge.broker import GRPCBrokerServicer

        b = GRPCBrokerServicer()
        with self.assertRaises(TimeoutError):
            b.dial(9999, timeout=0.3)

    def test_broker_internals(self):
        from astrbot._bridge.broker import GRPCBrokerServicer

        b = GRPCBrokerServicer()
        with b._cv:
            b._conns[7] = ("tcp", "127.0.0.1:1")
            b._cv.notify_all()
        # dial 会尝试连接（端口 1 不可用，不抛超时即可；连接失败由 grpc 延迟暴露）
        try:
            ch = b.dial(7, timeout=1)
            ch.close()
        except Exception:
            pass
        self.assertIn(7, b._conns)


class TestWebAPI(unittest.TestCase):
    """Web UI 插件 API：路由匹配、动态参数、quart 注入、响应序列化。"""

    def test_web_route_pattern(self):
        from astrbot._bridge.dispatch import PluginServiceServicer

        svc = PluginServiceServicer("x", "1", "", "")
        p, names = svc._web_route_pattern("/meme_manager/emoji/<category>")
        self.assertEqual(names, ["category"])
        m = p.match("/meme_manager/emoji/happy")
        self.assertIsNotNone(m)
        self.assertEqual(m.groupdict(), {"category": "happy"})
        self.assertIsNone(p.match("/meme_manager/other/happy"))
        p2, _ = svc._web_route_pattern("/a/<x>/<y>")
        self.assertEqual(p2.match("/a/1/2").groupdict(), {"x": "1", "y": "2"})

    def test_web_handler_dispatch(self):
        import asyncio

        from astrbot._bridge.dispatch import PluginServiceServicer
        from astrbot._bridge.gen import plugin_pb2

        async def api_emoji(category: str = None):
            return {"status": "ok", "category": category}

        svc = PluginServiceServicer("webdemo", "1", "", "")
        svc.web_apis = [("/webdemo/emoji/<category>", api_emoji, ["GET"], "x")]
        r = plugin_pb2.HandleWebRequestRequest(
            method="GET", path="/webdemo/emoji/happy",
            query=[plugin_pb2.WebKV(key="a", value="1"), plugin_pb2.WebKV(key="a", value="2")],
        )
        out = svc.HandleWebRequest(r, None)
        self.assertEqual(out.status_code, 200)
        self.assertEqual(json.loads(out.body), {"status": "ok", "category": "happy"})
        # 方法不匹配 → 404
        r2 = plugin_pb2.HandleWebRequestRequest(method="POST", path="/webdemo/emoji/happy")
        self.assertEqual(svc.HandleWebRequest(r2, None).status_code, 404)
        # 未注册 → 404
        r3 = plugin_pb2.HandleWebRequestRequest(method="GET", path="/webdemo/nope")
        self.assertEqual(svc.HandleWebRequest(r3, None).status_code, 404)

    def test_quart_request_injection(self):
        """quart 全局 request（_cv_request.get().request）可用。"""
        import asyncio

        try:
            import quart  # noqa: F401
        except ImportError:
            self.skipTest("quart 未安装")
        from astrbot._bridge.dispatch import PluginServiceServicer
        from astrbot._bridge.gen import plugin_pb2

        async def api_uses_quart():
            from quart.globals import _cv_request

            fake = _cv_request.get()
            assert fake is not None and fake.request is fake
            return {"status": "ok", "q": fake.request.args.get("pack_id", "")}

        svc = PluginServiceServicer("qd", "1", "", "")
        svc.web_apis = [("/qd/check", api_uses_quart, ["GET"], "x")]
        r = plugin_pb2.HandleWebRequestRequest(
            method="GET", path="/qd/check",
            query=[plugin_pb2.WebKV(key="pack_id", value="p9")],
        )
        out = svc.HandleWebRequest(r, None)
        self.assertEqual(out.status_code, 200)
        self.assertEqual(json.loads(out.body), {"status": "ok", "q": "p9"})

    def test_web_response_serialization(self):
        from astrbot._bridge.dispatch import PluginServiceServicer
        from astrbot._bridge.gen import plugin_pb2

        svc = PluginServiceServicer("s", "1", "", "")
        # dict → json 200
        self.assertEqual(
            svc._serialize_web_result({"status": "ok"}).status_code, 200
        )
        # (dict, status) 元组
        out = svc._serialize_web_result(({"e": "x"}, 409))
        self.assertEqual(out.status_code, 409)
        # quart Response 兼容对象（有 status_code + get_data，对齐真实 quart）
        class FakeResp:
            status_code = 201
            body = b'{"created": true}'
            headers = {"Content-Type": "application/json"}

            def get_data(self):
                return self.body

        out2 = svc._serialize_web_result(FakeResp())
        self.assertEqual(out2.status_code, 201)
        self.assertEqual(out2.body, b'{"created": true}')
        # None → 200 空
        self.assertEqual(svc._serialize_web_result(None).status_code, 200)


class TestPlatformBotProxy(unittest.TestCase):
    """_PlatformBotProxy 动态 OneBot API 转发（__getattr__ → call_action）。

    覆盖：未知 action（set_group_whole_ban 等）转发到
    call_action(action, **params)；_platform_id / call_action 等
    已定义属性不被 __getattr__ 拦截。
    """

    def setUp(self):
        import astrbot.core.star.context as ctx_mod
        from astrbot.core.star.context import _PlatformBotProxy

        self.calls = []
        calls = self.calls

        async def fake_call_action_async(self, platform, api, params):
            calls.append((platform, api, params))
            return {}

        bridge = type(
            "FakeBridge",
            (),
            {
                "ensure_connected": lambda self: True,
                "call_action_async": fake_call_action_async,
            },
        )()
        old_get_bridge = ctx_mod.get_host_bridge
        ctx_mod.get_host_bridge = lambda: bridge
        # 必须恢复模块级函数，否则后续测试的 set_host_bridge 单例失效
        self.addCleanup(lambda: setattr(ctx_mod, "get_host_bridge", old_get_bridge))
        self.proxy = _PlatformBotProxy("aiocqhttp_main")

    def test_dynamic_action_forwarding(self):
        import asyncio

        async def run():
            await self.proxy.set_group_whole_ban(group_id=123, enable=True)
            await self.proxy.get_login_info()
            await self.proxy.send_group_msg(group_id=456, message="hi")

        asyncio.run(run())
        self.assertEqual(
            self.calls,
            [
                ("aiocqhttp_main", "set_group_whole_ban", {"group_id": 123, "enable": True}),
                ("aiocqhttp_main", "get_login_info", {}),
                ("aiocqhttp_main", "send_group_msg", {"group_id": 456, "message": "hi"}),
            ],
        )

    def test_defined_attrs_not_intercepted(self):
        import asyncio

        # _platform_id：实例属性（不带下划线访问会被 __getattr__ 拦截前
        # 先命中 __init__ 设置的实例属性，直接返回）
        self.assertEqual(self.proxy._platform_id, "aiocqhttp_main")
        # call_action：显式方法，不走 __getattr__
        self.assertTrue(callable(self.proxy.call_action))
        # 下划线开头 → 明确 AttributeError（不落入动态转发）
        with self.assertRaises(AttributeError):
            self.proxy._internal_secret
        # 对齐 CQHttp 排除项：api/send/run/config/context 等非 OneBot action
        # 名称不得被误转发成宿主 action（否则宿主会收到 "api" 这类垃圾 action）
        for name in ("api", "send", "run", "config", "context", "call_api"):
            with self.subTest(name=name):
                with self.assertRaises(AttributeError):
                    getattr(self.proxy, name)

    def test_call_action_reaches_bridge(self):
        import asyncio

        async def run():
            await self.proxy.call_action("set_group_ban", group_id=1, user_id=2, duration=30)

        asyncio.run(run())
        self.assertEqual(
            self.calls,
            [("aiocqhttp_main", "set_group_ban", {"group_id": 1, "user_id": 2, "duration": 30})],
        )


class TestAiocqhttpPlatformStub(unittest.TestCase):
    """_AiocqhttpPlatformStub 的 isinstance 兼容（跨进程平台占位对齐原版类型）。

    覆盖：插件 `isinstance(inst, AiocqhttpAdapter)` 命中、metadata 可读、
    get_client 返回 _PlatformBotProxy、非 aiocqhttp 平台仍用通用 _PlatformStub。
    """

    def test_isinstance_aiocqhttp_adapter(self):
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
            AiocqhttpAdapter,
        )
        from astrbot.core.star.context import _AiocqhttpPlatformStub

        stub = _AiocqhttpPlatformStub(
            {"id": "aiocqhttp_main", "type": "aiocqhttp", "name": "aiocqhttp"}
        )
        self.assertIsInstance(stub, AiocqhttpAdapter)
        self.assertEqual(stub.metadata.id, "aiocqhttp_main")
        self.assertEqual(stub.meta().type, "aiocqhttp")
        client = stub.get_client()
        self.assertEqual(client._platform_id, "aiocqhttp_main")

    def test_platform_stub_selector(self):
        from astrbot.core.star.context import (
            _AiocqhttpPlatformStub,
            _PlatformManagerStub,
            _PlatformStub,
        )

        pm = _PlatformManagerStub()
        meta_list = [
            {"id": "a1", "type": "aiocqhttp", "name": "aiocqhttp"},
            {"id": "w1", "type": "webchat", "name": "webchat"},
        ]

        class FakeBridge:
            def ensure_connected(self):
                return True

            def list_platforms(self):
                return meta_list

        import astrbot.core.star.context as ctx_mod

        old = ctx_mod.get_host_bridge
        ctx_mod.get_host_bridge = lambda: FakeBridge()
        try:
            insts = pm.get_insts()
        finally:
            ctx_mod.get_host_bridge = old

        self.assertEqual(len(insts), 2)
        self.assertIsInstance(insts[0], _AiocqhttpPlatformStub)
        self.assertIsInstance(insts[0], _PlatformStub)
        # 非 aiocqhttp 平台保持通用 stub（不误判为 aiocqhttp）
        self.assertIsInstance(insts[1], _PlatformStub)
        self.assertNotIsInstance(
            insts[1],
            _AiocqhttpPlatformStub,
        )


class TestSkillsHistoryBridge(unittest.TestCase):
    """SkillManager / HostBridge 的 skills + platform-message-history 薄壳
    转发（mock HostBridge，验证请求构造与返回到强类型/降级）。"""

    def _fake_bridge(self, **overrides):
        """构造一个可注入 get_host_bridge 的 FakeBridge（缺省方法 no-op）。"""
        import types

        methods = {
            "ensure_connected": lambda self: True,
            "list_skills": lambda self, active_only=False, runtime="": [],
            "set_skill_active": lambda self, name, active: True,
            "delete_skill": lambda self, name: True,
            "get_platform_message_history": lambda self, platform_id, user_id, limit=200: [],
            "insert_platform_message_history": lambda self, platform_id, user_id, content, sender_id=None, llm_checkpoint_id=None, max_messages=0: {},
            "update_platform_message_history": lambda self, message_id, content=None, llm_checkpoint_id=None: True,
            "delete_platform_message_history": lambda self, message_id: True,
        }
        methods.update(overrides)
        return type("FakeBridge", (), methods)()

    def _patch_bridge(self, fake):
        import astrbot.core.star.context as ctx_mod

        old = ctx_mod.get_host_bridge
        ctx_mod.get_host_bridge = lambda: fake
        self.addCleanup(lambda: setattr(ctx_mod, "get_host_bridge", old))

    def test_skill_manager_list_skills_parses_host_payload(self):
        from astrbot.core.skills.skill_manager import SkillInfo, SkillManager

        fake = self._fake_bridge(
            list_skills=lambda self, active_only=False, runtime="": [
                {
                    "name": "weather",
                    "description": "查询天气",
                    "path": "data/skills/weather/SKILL.md",
                    "active": True,
                    "source_type": "local_only",
                    "source_label": "local",
                    "local_exists": True,
                    "sandbox_exists": False,
                    "plugin_name": "",
                    "readonly": False,
                    "preset": False,
                }
            ]
        )
        self._patch_bridge(fake)
        mgr = SkillManager()
        skills = mgr.list_skills()
        self.assertEqual(len(skills), 1)
        s = skills[0]
        self.assertIsInstance(s, SkillInfo)
        self.assertEqual(s.name, "weather")
        self.assertTrue(s.active)
        self.assertEqual(s.source_type, "local_only")

    def test_skill_manager_lists_skills_info_dict(self):
        from astrbot.core.skills.skill_manager import SkillManager

        fake = self._fake_bridge(
            list_skills=lambda self, active_only=False, runtime="": [
                {"name": "a", "description": "d", "path": "p", "active": True}
            ]
        )
        self._patch_bridge(fake)
        infos = SkillManager().list_skills_info()
        self.assertEqual(infos[0]["name"], "a")
        self.assertEqual(infos[0].get("source_type", "local_only"), "local_only")

    def test_skill_manager_set_active_and_delete(self):
        from astrbot.core.skills.skill_manager import SkillManager

        calls = {}

        def set_active(self, name, active):
            calls["set"] = (name, active)
            return True

        def delete(self, name):
            calls["delete"] = name
            return True

        fake = self._fake_bridge(set_skill_active=set_active, delete_skill=delete)
        self._patch_bridge(fake)
        mgr = SkillManager()
        mgr.set_skill_active("weather", True)
        mgr.delete_skill("obsolete")
        self.assertEqual(calls["set"], ("weather", True))
        self.assertEqual(calls["delete"], "obsolete")

    def test_skill_manager_downgrades_without_host(self):
        # host bridge 未提供 skills 方法 / 未连接 → list 空、操作 no-op 不抛错
        from astrbot.core.skills.skill_manager import SkillManager

        fake = self._fake_bridge()  # 不含 list_skills / set_skill_active…
        self._patch_bridge(fake)
        mgr = SkillManager()
        self.assertEqual(mgr.list_skills(), [])
        mgr.set_skill_active("x", True)  # 不应抛异常
        mgr.delete_skill("x")

    def test_build_skills_prompt_empty_and_markdown(self):
        from astrbot.core.skills.skill_manager import SkillInfo, build_skills_prompt, SkillManager

        # 对齐本体：空列表也返回完整技能清单模板（与 astrbot-py 输出一致）
        empty_out = build_skills_prompt([])
        self.assertIn("## Skills", empty_out)
        self.assertIn("### Available skills", empty_out)
        skills = [SkillInfo(name="test", description="desc", path="/x", active=True)]
        out = build_skills_prompt(skills)
        self.assertIn("- **test**: desc", out)
        self.assertIn("File: `/x`", out)

        # SkillInfo.from_dict / to_dict 往返
        s = SkillInfo.from_dict({"name": "n", "description": "d", "path": "p", "active": True})
        d = s.to_dict()
        self.assertEqual(d["name"], "n")
        self.assertEqual(d["source_type"], "local_only")

    def test_host_bridge_history_rpc_forwarding(self):
        # 直接以 HostBridge 实例 + FakeStub 验证 skills/history RPC 构造
        from astrbot._bridge.host import HostBridge
        from astrbot._bridge.gen import plugin_pb2

        calls = []

        class FakeStub:
            def ListSkills(self, req, timeout=30):
                calls.append("ListSkills")
                return plugin_pb2.SkillsResponse(skills_json=[b'{"name":"x","active":true}'])
            def SetSkillActive(self, req, timeout=30):
                calls.append(("SetSkillActive", req.name, req.active))
                return plugin_pb2.Empty()
            def DeleteSkill(self, req, timeout=30):
                calls.append(("DeleteSkill", req.name))
                return plugin_pb2.Empty()
            def GetPlatformMessageHistory(self, req, timeout=30):
                calls.append(("GetPMH", req.platform_id, req.user_id, req.limit))
                return plugin_pb2.PMHistoryRecordsResponse(
                    records_json=[b'{"id":7,"platform_id":"aiocqhttp","user_id":"g:1"}']
                )
            def InsertPlatformMessageHistory(self, req, timeout=30):
                calls.append(("InsertPMH", req.platform_id, req.user_id))
                return plugin_pb2.PMHistoryRecordResponse(record_json=b'{"id":99}')
            def UpdatePlatformMessageHistory(self, req, timeout=30):
                calls.append(("UpdatePMH", req.id))
                return plugin_pb2.Empty()
            def DeletePlatformMessageHistory(self, req, timeout=30):
                calls.append(("DeletePMH", req.id))
                return plugin_pb2.Empty()

        bridge = HostBridge()
        bridge._stub = FakeStub()
        bridge._probed = True

        self.assertEqual(len(bridge.list_skills()), 1)
        self.assertTrue(bridge.set_skill_active("x", True))
        self.assertTrue(bridge.delete_skill("y"))

        recs = bridge.get_platform_message_history("aiocqhttp", "g:1", 50)
        self.assertEqual(recs[0]["id"], 7)
        ins = bridge.insert_platform_message_history(
            "aiocqhttp", "g:1", {"type": "user", "message": ["hi"]}, max_messages=100
        )
        self.assertEqual(ins.get("id"), 99)
        self.assertTrue(bridge.update_platform_message_history(7, {"x": 1}))
        self.assertTrue(bridge.delete_platform_message_history(7))

        self.assertIn("ListSkills", calls)
        self.assertIn(("SetSkillActive", "x", True), calls)
        self.assertIn(("DeleteSkill", "y"), calls)

    def test_host_bridge_returns_empty_on_rpc_failure(self):
        from astrbot._bridge.host import HostBridge

        class FailingStub:
            def ListSkills(self, req, timeout=30):
                raise Exception("host down")
            def GetPlatformMessageHistory(self, req, timeout=30):
                raise Exception("host down")

        bridge = HostBridge()
        bridge._stub = FailingStub()
        bridge._probed = True
        # stub 调用失败 → 方法内部降级为空列表，不抛异常
        self.assertEqual(bridge.list_skills(), [])
        self.assertEqual(bridge.get_platform_message_history("a", "b", 10), [])


class TestProtoEvent(unittest.TestCase):
    """P1：proto SDKEvent → Python Event 语义等价 + proto Component 往返。"""

    def test_component_proto_roundtrip(self):
        from astrbot._bridge.gen import plugin_pb2
        from astrbot._bridge.serialize import component_from_proto, component_to_proto, proto_to_component_list
        from astrbot.core.message.components import At, AtAll, Image, Json, Plain

        cases = [
            Plain(text="hi"),
            At(qq="123", name="u"),
            AtAll(),
            Image(file="base64://aGk="),
            Image(url="https://x/y.png"),
            Json(data={"app": "test", "n": 1}),
        ]
        for c in cases:
            pc = component_to_proto(c)
            back = component_from_proto(pc)
            self.assertEqual(str(back.type.value if hasattr(back.type, "value") else back.type),
                             str(c.type.value if hasattr(c.type, "value") else c.type),
                             f"type mismatch for {c}")
            if isinstance(c, Plain):
                self.assertEqual(back.text, "hi")
            elif isinstance(c, At) and c.qq != "all":
                self.assertEqual(back.qq, "123")
            elif isinstance(c, Json):
                self.assertEqual(back.data.get("app"), "test")

    def test_from_proto_semantics(self):
        import json as _json

        from astrbot._bridge.gen import plugin_pb2
        from astrbot.core.message.components import ComponentType
        from astrbot.core.platform.astr_message_event import AstrMessageEvent

        se = plugin_pb2.SDKEvent(
            type="message", platform="test", platform_id="p1",
            message_type="GroupMessage", self_id="self", sender_id="u1",
            sender_name="alice", conv_id="g1", group_name="g",
            is_group=True, is_at_bot=True, is_admin=False,
            message_str="hello world", plain_text="hello world",
            raw_message=_json.dumps({"notice_type": "x"}).encode(),
            message_id="m1", timestamp=1700000000,
            metadata_json=_json.dumps({"role": "admin", "foo": "bar"}).encode(),
            components=[plugin_pb2.Component(type="Plain", text="hi"),
                        plugin_pb2.Component(type="At", target_id="u2", name="bob")],
        )
        ev = AstrMessageEvent.from_proto(se)
        self.assertEqual(ev.message_str, "hello world")
        self.assertEqual(ev.get_sender_id(), "u1")
        self.assertEqual(ev.get_sender_name(), "alice")
        self.assertEqual(ev.get_platform_id(), "p1")
        self.assertEqual(ev.get_self_id(), "self")
        self.assertEqual(ev.get_session_id(), "g1")
        self.assertEqual(ev.get_group_id(), "g1")
        self.assertEqual(ev.session.message_type.value, "GroupMessage")
        self.assertTrue(ev.is_at_bot)
        self.assertEqual(ev.role, "admin")
        self.assertEqual(ev.message_obj.message_id, "m1")
        self.assertEqual(ev.message_obj.timestamp, 1700000000)
        # raw_message 解析为 dict
        self.assertIsInstance(ev.message_obj.raw_message, dict)
        self.assertEqual(ev.message_obj.raw_message.get("notice_type"), "x")
        # components
        msgs = ev.get_messages() or []
        self.assertEqual(len(msgs), 2)
        self.assertEqual(
            str(msgs[0].type.value if hasattr(msgs[0].type, "value") else msgs[0].type),
            "Plain",
        )
        self.assertEqual(
            str(msgs[1].type.value if hasattr(msgs[1].type, "value") else msgs[1].type),
            "At",
        )

    def test_from_proto_field_completeness(self):
        """spec #18：SDKEvent 全字段 → Python Event 字段完整性。"""
        import json as _json

        from astrbot._bridge.gen import plugin_pb2
        from astrbot.core.platform.astr_message_event import AstrMessageEvent

        se = plugin_pb2.SDKEvent(
            type="message", platform="telegram", platform_id="t1",
            message_type="FriendMessage", self_id="self", sender_id="u1",
            sender_name="alice", conv_id="c1", group_name="",
            is_group=False, is_at_bot=False, is_admin=False,
            message_str="hi", plain_text="hi", raw_message=b"raw",
            message_id="m1", timestamp=1700000000,
            metadata_json=_json.dumps({"foo": "bar"}).encode(),
            components=[plugin_pb2.Component(type="Plain", text="hi")],
        )
        ev = AstrMessageEvent.from_proto(se)
        # 固定字段逐一核对（用 SDK 暴露的访问器）
        self.assertEqual(ev.get_platform_id(), "t1")
        self.assertEqual(ev.get_self_id(), "self")
        self.assertEqual(ev.get_sender_id(), "u1")
        self.assertEqual(ev.get_sender_name(), "alice")
        self.assertEqual(ev.get_session_id(), "c1")
        self.assertFalse(ev.is_at_bot)
        self.assertEqual(ev.role, "member")
        self.assertEqual(ev.message_str, "hi")
        self.assertEqual(ev.message_obj.message_id, "m1")
        self.assertEqual(ev.message_obj.timestamp, 1700000000)
        # metadata（role 已由 metadata_json 注入）
        self.assertEqual(ev.role, "member")  # 未声明 role → 回退 member

    def test_from_proto_empty_semantics(self):
        """spec #18：None / empty string / false / 0 / empty list / empty dict
        语义不得被改变或破坏。"""
        import json as _json

        from astrbot._bridge.gen import plugin_pb2
        from astrbot.core.platform.astr_message_event import AstrMessageEvent

        # 全部默认值（proto3 零值）
        se = plugin_pb2.SDKEvent()
        ev = AstrMessageEvent.from_proto(se)
        self.assertEqual(ev.get_sender_id(), "")
        self.assertEqual(ev.message_str, "")
        self.assertEqual(ev.get_session_id(), "")
        self.assertFalse(ev.is_at_bot)
        self.assertEqual(ev.role, "member")  # 非 admin 回退 member
        # timestamp=0（proto3 零值）→ 视为未设置，回退当前时间（>0）
        self.assertGreater(ev.message_obj.timestamp, 0)
        # 空链
        self.assertEqual(ev.get_messages() or [], [])
        # 默认私聊（无 is_group → FriendMessage）
        self.assertEqual(ev.session.message_type.value, "FriendMessage")

        # 空链
        self.assertEqual(ev.get_messages() or [], [])

        # 显式 false / 0
        se = plugin_pb2.SDKEvent(
            is_group=False, is_at_bot=False, is_admin=False,
            timestamp=0, components=[],
        )
        ev = AstrMessageEvent.from_proto(se)
        self.assertFalse(ev.is_at_bot)
        self.assertEqual(ev.role, "member")
        self.assertGreater(ev.message_obj.timestamp, 0)
        self.assertEqual(ev.get_messages() or [], [])

    def test_component_proto_full_types(self):
        """spec #18：各组件类型 proto 往返（含 binary/base64/Json 卡片）。"""
        from astrbot._bridge.gen import plugin_pb2
        from astrbot._bridge.serialize import component_from_proto, component_to_proto, proto_to_component_list
        from astrbot.core.message.components import (
            At, AtAll, Face, File, Image, Json, Plain, Record, Reply, Video,
        )

        cases = [
            Plain(text="hello"),
            At(qq="123", name="u"),
            AtAll(),
            Reply(id="r1", message_str="quoted"),
            Image(file="base64://aGVsbG8="),
            Image(url="https://x/y.png"),
            Image(path="/tmp/a.png"),
            Record(file="a.ogg", url="https://e/r.ogg"),
            Video(file="v.mp4"),
            File(name="doc.pdf", file="/tmp/d.pdf"),
            Face(id=1),
            Json(data={"app": "card", "n": 1, "ok": True, "arr": [1, 2, 3]}),
        ]
        for c in cases:
            pc = component_to_proto(c)
            back = component_from_proto(pc)
            ct = str(back.type.value if hasattr(back.type, "value") else back.type)
            want = str(c.type.value if hasattr(c.type, "value") else c.type)
            self.assertEqual(ct, want, f"type mismatch for {c}")
        # 组合链：Plain + At
        chain = component_list_to_proto_stub()
        comps = proto_to_component_list(chain)
        self.assertEqual(len(comps), 2)
        self.assertEqual(comps[0].text, "hello")
        self.assertEqual(comps[1].qq, "456")


def component_list_to_proto_stub():
    """构造 Plain + At 组合链的 proto 组件。"""
    from astrbot._bridge.gen import plugin_pb2
    return [
        plugin_pb2.Component(type="Plain", text="hello"),
        plugin_pb2.Component(type="At", target_id="456", name="bob"),
    ]


class TestP1Benchmark:
    """P1 native 数据面轻量 benchmark（spec #21，100KB/1MB）。

    非 unittest（unittest.main 会跑真 benchmark 太慢）：用 __main__ 手动触发。
    """

    @staticmethod
    def run():
        import json as _json
        import time

        from astrbot._bridge.gen import plugin_pb2
        from astrbot.core.platform.astr_message_event import AstrMessageEvent

        def build_event(target_bytes):
            body = "这是一段用于 P1 数据面基准测试的长文本回复内容。"
            while True:
                se = plugin_pb2.SDKEvent(
                    type="message", platform="aiocqhttp", platform_id="p",
                    message_type="GroupMessage", self_id="s", sender_id="u",
                    sender_name="n", conv_id="g", group_name="g",
                    is_group=True, is_at_bot=True, is_admin=True,
                    message_str=body, plain_text=body, raw_message=body,
                    message_id="m", timestamp=1700000000,
                    metadata_json=_json.dumps({"role": "admin", "n": 42}).encode(),
                    components=[plugin_pb2.Component(type="Plain", text=body)],
                )
                if len(se.SerializeToString()) >= target_bytes:
                    break
                body += "这是一段用于 P1 数据面基准测试的长文本回复内容。"
            return se

        print("\n[Python SDK P1 benchmark]")
        for name, target in (("100KB", 100 << 10), ("1MB", 1 << 20)):
            se = build_event(target)
            wire = se.SerializeToString()
            # from_proto
            n = 200 if name == "100KB" else 20
            t0 = time.perf_counter()
            for _ in range(n):
                AstrMessageEvent.from_proto(se)
            dt_from = (time.perf_counter() - t0) / n
            # proto wire marshal
            t0 = time.perf_counter()
            for _ in range(n):
                se.SerializeToString()
            dt_marshal = (time.perf_counter() - t0) / n
            # proto wire unmarshal
            t0 = time.perf_counter()
            for _ in range(n):
                plugin_pb2.SDKEvent.FromString(wire)
            dt_unmarshal = (time.perf_counter() - t0) / n
            # JSON 参照（整事件 marshal——旧路径）
            ev_dict = {"type": se.type, "platform": se.platform, "message_str": se.message_str}
            t0 = time.perf_counter()
            for _ in range(n):
                _json.dumps(ev_dict)
            dt_json = (time.perf_counter() - t0) / n
            print(f"  {name} wire={len(wire)}B  from_proto={dt_from*1e6:.1f}us  "
                  f"marshal={dt_marshal*1e6:.1f}us  unmarshal={dt_unmarshal*1e6:.1f}us  "
                  f"json_ref={dt_json*1e6:.1f}us")


class TestToolFirstArg(unittest.TestCase):
    """HandleTool 工具首参判定（原版 call 型 ContextWrapper vs run 型 event）。"""

    def _dispatch_mod(self):
        from astrbot._bridge import dispatch

        return dispatch

    def test_call_style_first_arg_context(self):
        """livingmemory 风格 call(self, context, **kw)：首参应判定为 context 语境。"""
        d = self._dispatch_mod()

        class Tool:
            async def call(self, context, query, k=5):
                return context.context.event

        t = Tool()
        self.assertTrue(d._tool_first_arg_is_context(t.call))

    def test_run_style_first_arg_event(self):
        """SDK 文档约定 run(self, event, **kw)：应保持传事件本身。"""
        d = self._dispatch_mod()

        class Tool:
            async def run(self, event, query):
                return "ok"

        t = Tool()
        self.assertFalse(d._tool_first_arg_is_context(t.run))

    def test_annotated_event(self):
        """注解 AstrMessageEvent 的首参 → 恒为 run 型。"""
        d = self._dispatch_mod()
        try:
            from astrbot.core.platform.astr_message_event import AstrMessageEvent as AME
        except ImportError:
            self.skipTest("AstrMessageEvent 导入路径不可用")
        code = "def h(event: AstrMessageEvent, query: str):\n    return None\n"
        globs = {"AstrMessageEvent": AME}
        exec(compile(code, "<test>", "exec"), globs)
        self.assertFalse(d._tool_first_arg_is_context(globs["h"]))

    def test_annotated_context_wrapper(self):
        """注解 ContextWrapper 的首参 → call 型。"""
        d = self._dispatch_mod()
        from astrbot.core.agent.run_context import ContextWrapper

        def h(context: ContextWrapper, query: str):
            return None

        self.assertTrue(d._tool_first_arg_is_context(h))

    def test_unresolvable_signature_keeps_old_behavior(self):
        """签名不可解析（*args）→ False，保持旧行为（传事件）。"""
        d = self._dispatch_mod()

        def h(*args, **kwargs):
            return None

        self.assertFalse(d._tool_first_arg_is_context(h))

    def test_wrapper_access_chain(self):
        """包装后的访问链：wrapper.context.event 即事件（livingmemory 炸点）。"""
        d = self._dispatch_mod()
        from astrbot.core.agent.run_context import ContextWrapper

        sentinel = types.SimpleNamespace(message_str="hi")
        wrapper = ContextWrapper(context=d._ToolTContext(sentinel), messages=[])
        self.assertIs(wrapper.context.event, sentinel)

        # 端到端：livingmemory 风格 call 在包装首参下取到事件
        class Tool:
            async def call(self, context, query, k=5):
                return context.context.event

        import asyncio

        got = asyncio.run(Tool().call(wrapper, query="q"))
        self.assertIs(got, sentinel)


class TestEmbeddingBatch(unittest.TestCase):
    """EmbeddingProvider.get_embeddings_batch 默认实现（对齐原版语义）。"""

    def _make_provider(self, get_embeddings):
        from astrbot.core.provider.provider import EmbeddingProvider

        class P(EmbeddingProvider):
            def __init__(self):
                self.provider_config = {}
                self.model_name = "t"

            async def get_embeddings(self, texts):
                return await get_embeddings(texts)

            def get_dim(self):
                return 0

        return P()

    def test_batch_order_and_chunking(self):
        """分片调用且结果按原顺序拼接。"""
        import asyncio

        seen = []

        async def fake(texts):
            seen.append(list(texts))
            return [[float(len(texts))] * 2 for _ in texts]

        p = self._make_provider(fake)
        out = asyncio.run(
            p.get_embeddings_batch(["a", "b", "c", "d", "e"], batch_size=2)
        )
        # 3 片：[a,b] [c,d] [e]，fake 返回向量的值=该分片长度
        self.assertEqual([len(b) for b in seen], [2, 2, 1])
        self.assertEqual(len(out), 5)
        self.assertEqual(out[0], [2.0, 2.0])
        self.assertEqual(out[4], [1.0, 1.0])

    def test_empty_input(self):
        import asyncio

        async def fake(texts):
            return []

        p = self._make_provider(fake)
        out = asyncio.run(p.get_embeddings_batch([]))
        self.assertEqual(out, [])

    def test_progress_callback_async(self):
        import asyncio

        calls = []

        async def cb(cur, total):
            calls.append((cur, total))

        async def fake(texts):
            return [[0.0] for _ in texts]

        p = self._make_provider(fake)
        asyncio.run(
            p.get_embeddings_batch(["x", "y"], batch_size=1, progress_callback=cb)
        )
        self.assertEqual(calls[-1], (2, 2))


if __name__ == "__main__":
    # 1) 本文件内嵌测试（unittest.main 只加载 __main__ 模块，不扫描目录）
    prog = unittest.main(verbosity=2, exit=False)
    TestP1Benchmark.run()

    # 2) 独立测试文件：tests/test_*.py（unittest.main 不会做目录发现，
    #    这里显式 discover，保证 CI 全量执行新增测试文件）
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    suite = unittest.TestLoader().discover(
        start_dir=tests_dir, pattern="test_*.py", top_level_dir=tests_dir
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    embedded_ok = (
        prog.result.wasSuccessful() if getattr(prog, "result", None) else True
    )
    # 任一部分失败即以非零退出，CI 才能捕捉（exit=False 不会自动退出）
    if not (embedded_ok and result.wasSuccessful()):
        sys.exit(1)
