"""宿主 MCP 读写桥接（list_host_mcp_tools / HostMcpTool）的单测。

覆盖点：
- list_host_mcp_tools：tools_json 解析（server/name/description/schema，
  schema_json 字符串与 schema dict 双形态）、非法条目跳过、宿主不可用 →
  []、旧宿主桥（无 mcp_list_tools）→ []；
- HostMcpTool：name/description/parameters 从宿主 schema 构造、
  call → bridge.mcp_call_tool 转发（server/tool_name/arguments）、
  is_error → "error: " 前缀文本（对齐本体 MCP 工具错误语义）、text 为空
  时回退 result JSON、宿主不可用 → 错误文本；
- astrbot.core.agent 包懒导出面（list_host_mcp_tools / HostMcpTool）。

运行：python3 tests/test_host_mcp_tools.py
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _BridgeTestCase(unittest.IsolatedAsyncioTestCase):
    """提供 patch astrbot.core.star.context.get_host_bridge 的基类
    （FakeBridge 注入模式对齐 tests/test_skill_manager_alignment.py）。"""

    def _fake_bridge(self, **overrides):
        methods = {
            "ensure_connected": lambda self: True,
        }
        methods.update(overrides)
        return type("FakeBridge", (), methods)()

    def _patch_bridge(self, fake):
        import astrbot.core.star.context as ctx_mod

        old = ctx_mod.get_host_bridge
        ctx_mod.get_host_bridge = lambda: fake
        self.addCleanup(lambda: setattr(ctx_mod, "get_host_bridge", old))


class TestListHostMcpTools(_BridgeTestCase):
    """list_host_mcp_tools：列表解析与降级。"""

    def test_parses_tools_json_schema_string_and_dict(self):
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        self._patch_bridge(
            self._fake_bridge(
                mcp_list_tools=lambda self: [
                    {
                        "server": "srv",
                        "name": "tool_a",
                        "description": "da",
                        "schema_json": json.dumps(schema),
                    },
                    {
                        "server": "srv",
                        "name": "tool_b",
                        "description": "db",
                        "schema": schema,
                    },
                    "bad-entry",  # 非 dict：跳过
                ]
            )
        )
        from astrbot.core.agent.mcp_client import list_host_mcp_tools

        tools = list_host_mcp_tools()
        self.assertEqual(len(tools), 2)
        self.assertEqual(
            tools[0],
            {"server": "srv", "name": "tool_a", "description": "da", "schema": schema},
        )
        self.assertEqual(tools[1]["schema"], schema)

    def test_missing_schema_falls_back_to_empty(self):
        self._patch_bridge(
            self._fake_bridge(
                mcp_list_tools=lambda self: [
                    {"server": "srv", "name": "tool_a", "description": "d"}
                ]
            )
        )
        from astrbot.core.agent.mcp_client import list_host_mcp_tools

        tools = list_host_mcp_tools()
        self.assertEqual(tools[0]["schema"], {})

    def test_host_unavailable_returns_empty(self):
        self._patch_bridge(None)
        from astrbot.core.agent.mcp_client import list_host_mcp_tools

        self.assertEqual(list_host_mcp_tools(), [])

    def test_bridge_without_rpc_returns_empty(self):
        """旧宿主桥（无 mcp_list_tools）→ []，不抛 AttributeError。"""
        self._patch_bridge(self._fake_bridge())
        from astrbot.core.agent.mcp_client import list_host_mcp_tools

        self.assertEqual(list_host_mcp_tools(), [])

    def test_rpc_error_returns_empty(self):
        def mcp_list_tools(self):
            raise RuntimeError("host down")

        self._patch_bridge(self._fake_bridge(mcp_list_tools=mcp_list_tools))
        from astrbot.core.agent.mcp_client import list_host_mcp_tools

        self.assertEqual(list_host_mcp_tools(), [])


class TestHostMcpTool(_BridgeTestCase):
    """HostMcpTool：schema 构造 + call 转发 + 错误语义。"""

    def _tool(self):
        from astrbot.core.agent.mcp_client import HostMcpTool

        return HostMcpTool(
            server="srv",
            name="tool_a",
            description="da",
            schema={"type": "object", "properties": {"q": {"type": "string"}}},
        )

    def test_fields_built_from_host_schema(self):
        t = self._tool()
        self.assertEqual(t.name, "tool_a")
        self.assertEqual(t.description, "da")
        self.assertEqual(t.server, "srv")
        self.assertEqual(t.parameters["type"], "object")
        self.assertEqual(t.parameters["properties"], {"q": {"type": "string"}})

    def test_from_dict_with_schema_json_string(self):
        from astrbot.core.agent.mcp_client import HostMcpTool

        t = HostMcpTool.from_dict(
            {
                "server": "srv",
                "name": "tool_a",
                "description": "da",
                "schema_json": json.dumps({"type": "object"}),
            }
        )
        self.assertEqual(t.name, "tool_a")
        self.assertEqual(t.parameters["type"], "object")

    async def test_call_forwards_and_returns_text(self):
        calls = {}

        def mcp_call_tool(self, server="", tool_name="", arguments=None):
            calls.update(server=server, tool_name=tool_name, arguments=arguments)
            return {"result": {"answer": 42}, "is_error": False, "text": "hi"}

        self._patch_bridge(self._fake_bridge(mcp_call_tool=mcp_call_tool))
        out = await self._tool().call(None, q="x")
        self.assertEqual(out, "hi")
        self.assertEqual(
            calls, {"server": "srv", "tool_name": "tool_a", "arguments": {"q": "x"}}
        )

    async def test_call_is_error_returns_error_prefixed_text(self):
        """is_error=True → "error: " 前缀（对齐本体 MCP 工具错误语义）。"""
        self._patch_bridge(
            self._fake_bridge(
                mcp_call_tool=lambda self, server="", tool_name="",
                arguments=None: {"result": None, "is_error": True, "text": "boom"}
            )
        )
        out = await self._tool().call(None)
        self.assertEqual(out, "error: boom")

    async def test_call_empty_text_falls_back_to_result_json(self):
        self._patch_bridge(
            self._fake_bridge(
                mcp_call_tool=lambda self, server="", tool_name="",
                arguments=None: {"result": {"k": "v"}, "is_error": False, "text": ""}
            )
        )
        out = await self._tool().call(None)
        self.assertEqual(out, json.dumps({"k": "v"}, ensure_ascii=False))

    async def test_call_host_unavailable_returns_error_text(self):
        self._patch_bridge(None)
        out = await self._tool().call(None)
        self.assertTrue(str(out).startswith("error:"))

    async def test_call_rpc_exception_returns_error_text(self):
        def mcp_call_tool(self, **kwargs):
            raise RuntimeError("host down")

        self._patch_bridge(self._fake_bridge(mcp_call_tool=mcp_call_tool))
        out = await self._tool().call(None)
        self.assertTrue(str(out).startswith("error:"))


class TestAgentPackageExport(_BridgeTestCase):
    """astrbot.core.agent 包导出面（懒导出）。"""

    def test_agent_package_exports_new_symbols(self):
        from astrbot.core.agent import HostMcpTool, list_host_mcp_tools

        self.assertTrue(callable(list_host_mcp_tools))
        self.assertTrue(issubclass(HostMcpTool, object))

    def test_agent_package_unknown_attr_raises(self):
        import astrbot.core.agent as agent_pkg

        with self.assertRaises(AttributeError):
            agent_pkg.not_a_real_symbol


if __name__ == "__main__":
    unittest.main()
