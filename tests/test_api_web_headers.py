"""api/web.py 请求头/上传文件对齐单测。

覆盖：
- PluginRequest.headers 大小写不敏感 get（对齐本体 starlette.Headers 语义，
  原 SDK 全小写化导致 request.headers.get("X-Token") 静默返回 None）
- PluginMultiDict.get 重复键取最后一个值（对齐本体）
- PluginUploadFile 提供 .filename/.content_type/.read/.save/.headers，
  插件访问不炸
- api.web.__all__ 导出面与本体对齐
"""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.api.web import (  # noqa: E402
    PluginMultiDict,
    PluginRequest,
    PluginUploadFile,
)


class TestHeadersCaseInsensitive(unittest.TestCase):
    def _make_request(self, headers: dict) -> PluginRequest:
        return PluginRequest(method="GET", path="/x", headers=headers)

    def test_get_mixed_case(self):
        req = self._make_request({"X-Token": "abc", "Content-Type": "application/json"})
        self.assertEqual(req.headers.get("X-Token"), "abc")
        self.assertEqual(req.headers.get("x-token"), "abc")
        self.assertEqual(req.headers.get("X-TOKEN"), "abc")
        self.assertEqual(req.headers["content-type"], "application/json")

    def test_contains_and_missing_default(self):
        req = self._make_request({"X-Token": "abc"})
        self.assertIn("x-token", req.headers)
        self.assertNotIn("nope", req.headers)
        self.assertIsNone(req.headers.get("nope"))
        self.assertEqual(req.headers.get("nope", "d"), "d")

    def test_original_case_preserved_for_iteration(self):
        req = self._make_request({"X-Token": "abc"})
        self.assertEqual(list(req.headers.keys()), ["X-Token"])

    def test_content_type_property(self):
        req = self._make_request({"content-type": "text/plain"})
        self.assertEqual(req.content_type, "text/plain")


class TestPluginMultiDict(unittest.TestCase):
    def test_get_returns_last_value_for_duplicate_keys(self):
        md = PluginMultiDict[str]([("a", "1"), ("b", "2"), ("a", "3")])
        # 对齐本体：重复键取最后一个值
        self.assertEqual(md.get("a"), "3")
        self.assertEqual(md.get("b"), "2")
        self.assertIsNone(md.get("missing"))
        self.assertEqual(md.get("missing", "d"), "d")

    def test_getlist_and_multi_items(self):
        md = PluginMultiDict[str]([("a", "1"), ("a", "3")])
        self.assertEqual(md.getlist("a"), ["1", "3"])
        self.assertEqual(md.multi_items(), [("a", "1"), ("a", "3")])


class TestPluginUploadFileCompat(unittest.TestCase):
    def test_basic_attrs(self):
        f = PluginUploadFile("a.png", "image/png", b"1234")
        self.assertEqual(f.filename, "a.png")
        self.assertEqual(f.content_type, "image/png")
        self.assertEqual(f.content_length, 4)
        # 对齐本体：headers 属性存在且可 get（宿主不携带 per-file 头，为空）
        self.assertIsNone(f.headers.get("content-length"))

    def test_read_and_save(self):
        async def run():
            f = PluginUploadFile("a.txt", "text/plain", b"hello world")
            self.assertEqual(await f.read(5), b"hello")
            self.assertEqual(await f.read(), b" world")
            with tempfile.TemporaryDirectory() as td:
                dest = os.path.join(td, "out.txt")
                await f.save(dest)
                with open(dest, "rb") as fh:
                    self.assertEqual(fh.read(), b"hello world")

        asyncio.run(run())

    def test_request_files_roundtrip(self):
        async def run():
            f = PluginUploadFile("a.txt", "text/plain", b"xyz")
            req = PluginRequest(method="POST", path="/u", files=[("file", f)])
            files = await req.files()
            self.assertIs(files.get("file"), f)

        asyncio.run(run())


class TestWebExports(unittest.TestCase):
    def test_all_covers_upstream_names(self):
        import astrbot.api.web as web

        expected = {
            "PluginMultiDict",
            "PluginRequest",
            "PluginRequestProxy",
            "PluginUploadFile",
            "bind_request_context",
            "error_response",
            "file_response",
            "json_response",
            "request",
            "stream_response",
        }
        missing = expected - set(web.__all__)
        self.assertFalse(missing, f"__all__ 缺少本体导出名: {missing}")


if __name__ == "__main__":
    unittest.main()
