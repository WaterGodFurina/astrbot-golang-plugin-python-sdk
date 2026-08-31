"""api 与 core 导出同对象校验（防同名不同义）。

覆盖：
- astrbot.api.html_renderer 与 astrbot.core.html_renderer 为同一实例
  （本体即如此；原 SDK 存在两套 HtmlRenderer 类）
- astrbot.api.BaseFunctionToolExecutor 与
  astrbot.core.agent.tool_executor.BaseFunctionToolExecutor 为同一类
- HtmlRenderer.render_custom_template 走宿主桥（不再是恒 None 的占位）
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestApiCoreSameObject(unittest.TestCase):
    def test_html_renderer_same_instance(self):
        from astrbot.api import HtmlRenderer as ApiHtmlRenderer, html_renderer as api_hr
        from astrbot.core import html_renderer as core_hr
        from astrbot.core.utils.html_renderer import HtmlRenderer as CoreHtmlRenderer

        self.assertIs(api_hr, core_hr)
        self.assertIs(ApiHtmlRenderer, CoreHtmlRenderer)

    def test_base_function_tool_executor_same_class(self):
        from astrbot.api import BaseFunctionToolExecutor as ApiExec
        from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor as CoreExec

        self.assertIs(ApiExec, CoreExec)

    def test_render_custom_template_delegates_to_host_bridge(self):
        from astrbot.api import html_renderer

        calls = {}

        class FakeBridge:
            async def html_render_async(self, template="", data=""):
                calls["template"] = template
                calls["data"] = data
                return b"png-bytes"

        import astrbot._bridge.host as host_mod

        original = host_mod._bridge
        host_mod._bridge = FakeBridge()
        try:
            out = asyncio.run(
                html_renderer.render_custom_template("<p>hi</p>", {"k": "中文"})
            )
        finally:
            host_mod._bridge = original
        self.assertEqual(out, "data:image/png;base64," + __import__("base64").b64encode(b"png-bytes").decode())
        self.assertEqual(calls["template"], "<p>hi</p>")
        # data 以 JSON 传给宿主
        self.assertIn("中文", calls["data"])


if __name__ == "__main__":
    unittest.main()
