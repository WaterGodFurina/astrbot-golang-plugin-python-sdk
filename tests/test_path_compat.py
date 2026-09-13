"""path_compat._redirect 的非路径参数透传回归（fd 兼容）。

Popen 内部 io.open(fd:int) 与 os.stat(fd) 会经过被 hook 的 builtins.open；
旧实现对 int 调 os.fspath 抛 TypeError，导致带硬编码路径的插件（hook 已装）
任何 subprocess 调用即崩。
"""
from __future__ import annotations

import builtins
import io
import os
import re
import unittest

from astrbot._bridge import path_compat


class TestPathCompatNonPathArgs(unittest.TestCase):
    def setUp(self):
        self._saved = (
            builtins.open, io.open, os.stat, os.lstat, os.listdir, os.scandir,
            path_compat._legacy_pattern, path_compat._real_name, path_compat._hooked,
        )
        # 与 install() 真实构造同构：捕获 plugins[/\\] + 旧目录名 lookahead。
        path_compat._legacy_pattern = re.compile(r"(plugins[/\\])legacydir(?=$|[/\\])")
        path_compat._real_name = "legacydir_python"
        path_compat._hooked = False
        path_compat._install_hooks()

    def tearDown(self):
        (
            builtins.open, io.open, os.stat, os.lstat, os.listdir, os.scandir,
            path_compat._legacy_pattern, path_compat._real_name, path_compat._hooked,
        ) = self._saved

    def test_int_fd_passthrough_via_io_open(self):
        r, w = os.pipe()
        try:
            os.write(w, b"payload")
            os.close(w)
            f = io.open(r, "rb")  # Popen 的内部调用形态：数字 fd 经被 hook 的 open
            try:
                self.assertEqual(f.read(), b"payload")
            finally:
                f.close()
        finally:
            try:
                os.close(r)
            except OSError:
                pass

    def test_redirect_accepts_int_none_bytes(self):
        self.assertEqual(path_compat._redirect(3), 3)
        self.assertEqual(path_compat._redirect(None), None)
        b = b"/tmp/x"
        self.assertEqual(path_compat._redirect(b), b)

    def test_os_stat_int_fd_still_works(self):
        st = os.fstat if hasattr(os, "fstat") else os.stat
        r, w = os.pipe()
        try:
            st(r)  # fstat by fd must not raise after hooking os.stat
        finally:
            os.close(r)
            os.close(w)

    def test_str_redirect_still_applies(self):
        self.assertEqual(
            path_compat._redirect("/app/data/plugins/legacydir/main.py"),
            "/app/data/plugins/legacydir_python/main.py",
        )


if __name__ == "__main__":
    unittest.main()
