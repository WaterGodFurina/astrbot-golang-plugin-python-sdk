"""_PluginUpdater 插件元数据校验/定位方法对齐单测。

对齐本体 astrbot/core/star/updater.py 的公开方法面：
- validate_plugin_metadata：参数名为 metadata_label（SDK 旧实现为 label，
  按名传参会 TypeError），校验逻辑（desc/description 归一、必需字段、
  非空字符串检查）与本体一致；
- find_plugin_metadata_entry：压缩包成员中定位 metadata.yaml/.yml
  （含带根目录与 Windows 反斜杠路径场景）；
- inspect_plugin_archive / validate_plugin_archive /
  inspect_plugin_directory：纯本地元数据检查（按本体移植的真实现）。

install/update/inspect_plugin_repository/_clone_repository 在 SDK 侧为
签名对齐的降级实现（安装更新由 Go 宿主 internal/plugin 原生处理），
本测试同时覆盖其调用报错为 RuntimeError（而非 AttributeError）。
"""
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.star.updater import (
    PLUGIN_METADATA_FILENAMES,
    _PluginUpdater,
)


def _md_dict(**overrides):
    md = {
        "name": "demo",
        "desc": "演示插件",
        "version": "1.0.0",
        "author": "astrbot",
    }
    md.update(overrides)
    return md


class TestValidatePluginMetadata(unittest.TestCase):
    def test_param_name_is_metadata_label(self):
        """参数名对齐本体 metadata_label，按名传参不 TypeError。"""
        _PluginUpdater.validate_plugin_metadata(
            _md_dict(), metadata_label="metadata.yaml"
        )

    def test_valid_metadata_passes(self):
        _PluginUpdater.validate_plugin_metadata(_md_dict(), "metadata.yaml")

    def test_description_fallback_to_desc(self):
        """本体语义：无 desc 时用 description 兜底。"""
        md = _md_dict()
        del md["desc"]
        md["description"] = "通过 description 提供"
        _PluginUpdater.validate_plugin_metadata(md, "metadata.yaml")

    def test_missing_field_raises_value_error(self):
        md = _md_dict()
        del md["author"]
        with self.assertRaises(ValueError) as ctx:
            _PluginUpdater.validate_plugin_metadata(md, "metadata.yaml")
        self.assertIn("author", str(ctx.exception))

    def test_blank_string_field_raises(self):
        with self.assertRaises(ValueError):
            _PluginUpdater.validate_plugin_metadata(_md_dict(version="   "), "m.yaml")

    def test_non_dict_raises(self):
        with self.assertRaises(ValueError):
            _PluginUpdater.validate_plugin_metadata("not-a-dict", "metadata.yaml")


class TestFindPluginMetadataEntry(unittest.TestCase):
    def test_flat_archive(self):
        entries = ["README.md", "metadata.yaml", "main.py"]
        self.assertEqual(
            _PluginUpdater.find_plugin_metadata_entry(entries), "metadata.yaml"
        )

    def test_yml_fallback(self):
        entries = ["main.py", "metadata.yml"]
        self.assertEqual(
            _PluginUpdater.find_plugin_metadata_entry(entries), "metadata.yml"
        )

    def test_root_dir_prefix(self):
        entries = ["demo/main.py", "demo/metadata.yaml"]
        self.assertEqual(
            _PluginUpdater.find_plugin_metadata_entry(entries), "demo/metadata.yaml"
        )

    def test_windows_backslash_entries(self):
        entries = ["demo\\main.py", "demo\\metadata.yaml"]
        self.assertEqual(
            _PluginUpdater.find_plugin_metadata_entry(entries),
            "demo\\metadata.yaml",
        )

    def test_not_found_returns_none(self):
        self.assertIsNone(
            _PluginUpdater.find_plugin_metadata_entry(["main.py", "README.md"])
        )


class TestInspectPluginArchive(unittest.TestCase):
    def _make_zip(self, entries: dict) -> str:
        fd, path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        with zipfile.ZipFile(path, "w") as z:
            for name, content in entries.items():
                z.writestr(name, content)
        self.addCleanup(os.remove, path)
        return path

    def test_inspect_valid_archive(self):
        yaml_text = "name: demo\ndesc: 演示\nversion: 1.0.0\nauthor: astrbot\n"
        path = self._make_zip(
            {"demo/metadata.yaml": yaml_text, "demo/main.py": "pass"}
        )
        result = _PluginUpdater.inspect_plugin_archive(path)
        self.assertEqual(result["metadata_entry"], "demo/metadata.yaml")
        self.assertEqual(result["metadata"]["name"], "demo")

    def test_validate_archive_returns_entry(self):
        yaml_text = "name: demo\ndesc: 演示\nversion: 1.0.0\nauthor: astrbot\n"
        path = self._make_zip({"metadata.yml": yaml_text})
        self.assertEqual(
            _PluginUpdater.validate_plugin_archive(path), "metadata.yml"
        )

    def test_invalid_archive_raises_value_error(self):
        path = self._make_zip({"main.py": "pass"})
        with self.assertRaises(ValueError):
            _PluginUpdater.inspect_plugin_archive(path)

    def test_bad_zip_raises_value_error(self):
        fd, path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        self.addCleanup(os.remove, path)
        with open(path, "wb") as f:
            f.write(b"definitely not a zip file")
        with self.assertRaises(ValueError):
            _PluginUpdater.inspect_plugin_archive(path)


class TestInspectPluginDirectory(unittest.TestCase):
    def test_inspect_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "metadata.yaml"), "w", encoding="utf-8") as f:
                f.write("name: demo\ndesc: 演示\nversion: 1.0.0\nauthor: astrbot\n")
            result = _PluginUpdater.inspect_plugin_directory(tmp)
            self.assertEqual(result["metadata_entry"], "metadata.yaml")
            self.assertEqual(result["metadata"]["version"], "1.0.0")

    def test_missing_metadata_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                _PluginUpdater.inspect_plugin_directory(tmp)


class TestHostNativeInstallUpdateDegraded(unittest.TestCase):
    def test_constants_surface(self):
        """模块常量 PLUGIN_METADATA_FILENAMES 对齐本体。"""
        self.assertIn("metadata.yaml", PLUGIN_METADATA_FILENAMES)
        self.assertIn("metadata.yml", PLUGIN_METADATA_FILENAMES)

    def test_install_raises_runtime_error(self):
        updater = _PluginUpdater()

        async def _run():
            await updater.install("https://github.com/a/b")

        import asyncio

        with self.assertRaises(RuntimeError):
            asyncio.run(_run())

    def test_update_raises_runtime_error(self):
        updater = _PluginUpdater()

        async def _run():
            await updater.update(type("P", (), {"name": "b", "root_dir_name": "b"})())

        import asyncio

        with self.assertRaises(RuntimeError):
            asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
