"""StarTools.register_llm_tool 参数名对齐单测。

对齐本体 astrbot/core/star/star_tools.py:207-227：末位参数名为 func_obj。
SDK 修复前参数名为 handler——插件按本体文档以关键字
`StarTools.register_llm_tool(name=..., func_args=..., desc=..., func_obj=f)`
调用时抛 TypeError: unexpected keyword argument 'func_obj'。

另覆盖：
- Context 构造时经 PluginManager 注入 StarTools（对齐本体
  star_manager.py:193-199；SDK 修复前生产环境 StarTools._context 恒为
  None，send_message/register_llm_tool 等全部抛 ValueError）；
- create_event 平台不存在时抛 ValueError（对齐本体 Raises 语义）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeContext:
    """Context 桩：记录 register_llm_tool 透传参数。"""

    def __init__(self):
        self.calls = []

    def register_llm_tool(self, name, func_args, desc, func_obj):
        self.calls.append((name, func_args, desc, func_obj))


async def _tool_handler(event, context=None, **kwargs):
    return "ok"


class TestStarToolsRegisterLlmToolFuncObj(unittest.TestCase):
    def setUp(self):
        from astrbot.core.star.star_tools import StarTools

        self.StarTools = StarTools
        self.fake_ctx = _FakeContext()
        self._old_ctx = StarTools._context
        StarTools.initialize(self.fake_ctx)

    def tearDown(self):
        self.StarTools._context = self._old_ctx

    def test_keyword_func_obj_accepted(self):
        """关键字 func_obj= 调用命中（本体签名，修复前 TypeError）。"""
        self.StarTools.register_llm_tool(
            name="my_tool",
            func_args=[{"type": "string", "name": "q", "description": "查询"}],
            desc="测试工具",
            func_obj=_tool_handler,
        )
        self.assertEqual(len(self.fake_ctx.calls), 1)
        name, func_args, desc, func_obj = self.fake_ctx.calls[0]
        self.assertEqual(name, "my_tool")
        self.assertEqual(func_args[0]["name"], "q")
        self.assertEqual(desc, "测试工具")
        self.assertIs(func_obj, _tool_handler)

    def test_positional_call_accepted(self):
        """位置参数调用兼容（与本体顺序一致：name/func_args/desc/func_obj）。"""
        self.StarTools.register_llm_tool("t2", [], "d", _tool_handler)
        self.assertEqual(self.fake_ctx.calls[0][0], "t2")
        self.assertIs(self.fake_ctx.calls[0][3], _tool_handler)

    def test_uninitialized_raises_value_error(self):
        """未初始化抛 ValueError（对齐本体行为）。"""
        self.StarTools._context = None
        with self.assertRaises(ValueError):
            self.StarTools.register_llm_tool("t3", [], "d", _tool_handler)


class TestStarToolsWiringAndCreateEvent(unittest.TestCase):
    """StarTools 与 Context 的注入链路 + create_event 平台校验。"""

    def tearDown(self):
        from astrbot.core.star import star_tools

        star_tools.StarTools._context = None

    def test_context_construction_wires_star_tools(self):
        """Context() 构造后 StarTools._context 指向该 Context（对齐本体注入点）。"""
        import astrbot.core.star.context as ctx_mod

        old_bridge = ctx_mod.get_host_bridge()
        ctx_mod.set_host_bridge(None)
        try:
            context = ctx_mod.Context()
            from astrbot.core.star.star_tools import StarTools

            self.assertIs(StarTools._context, context)
        finally:
            ctx_mod.set_host_bridge(old_bridge)

    def test_create_event_unknown_platform_raises_value_error(self):
        """create_event 平台不存在时抛 ValueError（对齐本体 Raises 语义）。"""
        import asyncio

        import astrbot.core.star.context as ctx_mod
        from astrbot.core.platform.astrbot_message import AstrBotMessage
        from astrbot.core.star.star_tools import StarTools

        old_bridge = ctx_mod.get_host_bridge()
        ctx_mod.set_host_bridge(None)
        try:
            context = ctx_mod.Context()
            self.assertIs(StarTools._context, context)
            with self.assertRaises(ValueError):
                asyncio.run(StarTools.create_event(AstrBotMessage(), "no_such_platform"))
        finally:
            ctx_mod.set_host_bridge(old_bridge)

    def test_create_event_uninitialized_raises_value_error(self):
        """StarTools 未初始化时 create_event 抛 ValueError（对齐本体）。"""
        import asyncio

        from astrbot.core.platform.astrbot_message import AstrBotMessage
        from astrbot.core.star.star_tools import StarTools

        StarTools._context = None
        with self.assertRaises(ValueError):
            asyncio.run(StarTools.create_event(AstrBotMessage(), "aiocqhttp"))


if __name__ == "__main__":
    unittest.main()
