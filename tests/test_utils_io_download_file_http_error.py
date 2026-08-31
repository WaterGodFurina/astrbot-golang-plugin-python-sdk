"""utils.io download_file 对齐本体单测。

覆盖：
- DownloadFileHTTPError 异常类存在且为 RuntimeError 子类（原 SDK 缺类，
  插件 `except DownloadFileHTTPError` 会 NameError）
- download_file 签名含 allow_insecure_ssl_fallback（本体 v4.27.4 默认
  True；原 SDK 缺参，按名传参会 TypeError）
- HTTP 非 200 抛 DownloadFileHTTPError（对齐本体 _raise_for_download_status，
  原 SDK 静默返回空串）
- ensure_dir 对路径冲突（同名文件）的清理语义
"""
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.utils.io import (  # noqa: E402
    DownloadFileHTTPError,
    download_file,
    ensure_dir,
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/ok":
            self.send_response(200)
            self.send_header("Content-Length", "5")
            self.end_headers()
            self.wfile.write(b"hello")
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *args):  # 静默访问日志
        pass


def _start_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class TestDownloadFileHTTPError(unittest.TestCase):
    def test_exception_class_exists_and_is_runtime_error(self):
        self.assertTrue(issubclass(DownloadFileHTTPError, RuntimeError))

    def test_download_file_signature_has_allow_insecure_ssl_fallback(self):
        import inspect

        params = inspect.signature(download_file).parameters
        self.assertIn("allow_insecure_ssl_fallback", params)
        self.assertTrue(params["allow_insecure_ssl_fallback"].default)

    def test_download_file_http_404_raises_download_file_http_error(self):
        server = _start_server()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            import asyncio

            with self.assertRaises(DownloadFileHTTPError):
                asyncio.run(download_file(f"{base}/missing", "/tmp/should_not_exist"))
        finally:
            server.shutdown()

    def test_download_file_success_writes_file(self):
        server = _start_server()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            import asyncio
            import tempfile

            target = os.path.join(tempfile.gettempdir(), "sdk_io_dl_ok.bin")
            result = asyncio.run(download_file(f"{base}/ok", target))
            self.assertEqual(result, target)
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"hello")
            os.remove(target)
        finally:
            server.shutdown()


class TestEnsureDir(unittest.TestCase):
    def test_ensure_dir_removes_conflicting_file(self):
        import tempfile

        base = tempfile.mkdtemp()
        conflict = os.path.join(base, "sub")
        with open(conflict, "w") as f:
            f.write("not a dir")
        ensure_dir(conflict)
        self.assertTrue(os.path.isdir(conflict))

    def test_ensure_dir_idempotent(self):
        import tempfile

        base = tempfile.mkdtemp()
        ensure_dir(base)
        ensure_dir(base)  # 已存在不报错
        self.assertTrue(os.path.isdir(base))


if __name__ == "__main__":
    unittest.main()
