"""skills/skill_manager.py 对齐本体（astrbot-py v4.27.4）的单测。

覆盖点：
- SkillInfo 字段/默认值与本体逐字段一致；
- build_skills_prompt：空列表返回完整模板、sandbox_only 缺省路径用真实技能名；
- 模块级常量/辅助函数 import 面（对齐本体 skill_manager 顶部符号）；
- is_plugin_skill / _get_plugin_skill_dir / _iter_plugin_skill_dirs 经宿主
  ListSkills 的真实现；delete_skill / set_skill_active 对插件/沙盒预置技能
  抛 PermissionError；
- 沙盒缓存共享文件真实现（set/get 往返、is_sandbox_only_skill 判定、
  _remove_skill_from_sandbox_cache）；
- list_workspace_skills：仅认规范 SKILL.md、readonly=True、Path 参数；
- install_skill_from_zip：根/目录双模式、overwrite 语义、__MACOSX 忽略、
  非法输入报错（按本体移植后的行为）。

运行：python3 tests/test_skill_manager_alignment.py
"""
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _BridgeTestCase(unittest.TestCase):
    """提供 patch astrbot.core.star.context.get_host_bridge 的基类。"""

    def _fake_bridge(self, **overrides):
        methods = {
            "ensure_connected": lambda self: True,
            "list_skills": lambda self, active_only=False, runtime="": [],
            "set_skill_active": lambda self, name, active: True,
            "delete_skill": lambda self, name: True,
        }
        methods.update(overrides)
        return type("FakeBridge", (), methods)()

    def _patch_bridge(self, fake):
        import astrbot.core.star.context as ctx_mod

        old = ctx_mod.get_host_bridge
        ctx_mod.get_host_bridge = lambda: fake
        self.addCleanup(lambda: setattr(ctx_mod, "get_host_bridge", old))

    def setUp(self):
        self._old_data_path = os.environ.get("ASTRBOT_DATA_PATH")
        self._data_dir = tempfile.mkdtemp(prefix="sdk_skill_test_")
        os.environ["ASTRBOT_DATA_PATH"] = self._data_dir

    def tearDown(self):
        if self._old_data_path is None:
            os.environ.pop("ASTRBOT_DATA_PATH", None)
        else:
            os.environ["ASTRBOT_DATA_PATH"] = self._old_data_path


class TestSkillInfoAlignment(_BridgeTestCase):
    """SkillInfo 字段与默认值逐字段对齐本体。"""

    def test_defaults_match_upstream(self):
        from astrbot.core.skills.skill_manager import SkillInfo

        s = SkillInfo(name="n", description="d", path="p", active=True)
        self.assertEqual(s.source_type, "local_only")
        self.assertEqual(s.source_label, "local")
        self.assertTrue(s.local_exists)
        self.assertFalse(s.sandbox_exists)
        self.assertEqual(s.plugin_name, "")
        self.assertFalse(s.readonly)
        self.assertFalse(s.preset)

    def test_to_dict_field_names_match_host_json(self):
        from astrbot.core.skills.skill_manager import SkillInfo

        d = SkillInfo(name="a", description="b", path="c", active=True).to_dict()
        self.assertEqual(
            sorted(d.keys()),
            sorted(
                [
                    "name",
                    "description",
                    "path",
                    "active",
                    "source_type",
                    "source_label",
                    "local_exists",
                    "sandbox_exists",
                    "plugin_name",
                    "readonly",
                    "preset",
                ]
            ),
        )


class TestBuildSkillsPromptAlignment(unittest.TestCase):
    """build_skills_prompt 输出对齐本体（含空列表与 sandbox 缺省路径）。"""

    def test_empty_list_returns_full_template(self):
        from astrbot.core.skills.skill_manager import build_skills_prompt

        out = build_skills_prompt([])
        self.assertIn("## Skills", out)
        self.assertIn("### Available skills", out)
        self.assertIn("### Skill rules", out)
        # 示例命令回退占位路径（Linux 下 cat）
        self.assertIn("cat <skills_root>/<skill_name>/SKILL.md", out)

    def test_sandbox_only_default_path_uses_real_skill_name(self):
        from astrbot.core.skills.skill_manager import SkillInfo, build_skills_prompt

        skill = SkillInfo(
            name="weather",
            description="",
            path="",
            active=True,
            source_type="sandbox_only",
            source_label="sandbox_preset",
            local_exists=False,
            sandbox_exists=True,
        )
        out = build_skills_prompt([skill])
        # 对齐本体 _default_sandbox_skill_path：占位符换成真实技能名
        self.assertIn("/workspace/skills/weather/SKILL.md", out)
        self.assertNotIn("/workspace/skills/<skill_name>/SKILL.md", out)
        self.assertIn("Read SKILL.md for details.", out)

    def test_example_command_uses_cat_on_posix(self):
        from astrbot.core.skills.skill_manager import SkillInfo, build_skills_prompt

        out = build_skills_prompt(
            [SkillInfo(name="s", description="d", path="/tmp/s/SKILL.md", active=True)]
        )
        if os.name != "nt":
            self.assertIn("`cat /tmp/s/SKILL.md`", out)


class TestModuleSurface(_BridgeTestCase):
    """模块级常量/辅助函数 import 面（from ... import X 必须可用）。"""

    def test_constants_match_upstream(self):
        from astrbot.core.skills import skill_manager as sm

        self.assertEqual(sm.SKILLS_CONFIG_FILENAME, "skills.json")
        self.assertEqual(sm.SANDBOX_SKILLS_CACHE_FILENAME, "sandbox_skills_cache.json")
        self.assertEqual(sm.DEFAULT_SKILLS_CONFIG, {"skills": {}})
        self.assertEqual(sm.SANDBOX_SKILLS_ROOT, "skills")
        self.assertEqual(sm.SANDBOX_WORKSPACE_ROOT, "/workspace")
        self.assertEqual(sm.WORKSPACE_SKILLS_ROOT, "skills")
        self.assertEqual(sm.WORKSPACE_SKILL_FRONTMATTER_MAX_CHARS, 64 * 1024)

    def test_helper_functions_importable_and_behaved(self):
        from astrbot.core.skills.skill_manager import (
            _default_sandbox_skill_path,
            _is_ignored_zip_entry,
            _normalize_cached_sandbox_skill_path,
            _normalize_skill_markdown_path,
            _normalize_skill_name,
            _parse_frontmatter_description,
        )
        from pathlib import Path

        self.assertEqual(_normalize_skill_name(" My Skill "), "My_Skill")
        self.assertEqual(
            _default_sandbox_skill_path("x"), "/workspace/skills/x/SKILL.md"
        )
        # 非法缓存路径回退缺省
        self.assertEqual(
            _normalize_cached_sandbox_skill_path("x", "../../etc/passwd"),
            "/workspace/skills/x/SKILL.md",
        )
        self.assertEqual(
            _normalize_cached_sandbox_skill_path("x", "/workspace/skills/x/SKILL.md"),
            "/workspace/skills/x/SKILL.md",
        )
        self.assertTrue(_is_ignored_zip_entry("__MACOSX/a"))
        self.assertFalse(_is_ignored_zip_entry("a/b"))

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "sk"
            d.mkdir()
            # 无 SKILL.md → None
            self.assertIsNone(_normalize_skill_markdown_path(d))
            # 旧式 skill.md → 就地重命名为 SKILL.md
            (d / "skill.md").write_text("hi", encoding="utf-8")
            canonical = _normalize_skill_markdown_path(d)
            self.assertIsNotNone(canonical)
            self.assertEqual(Path(canonical).name, "SKILL.md")
            self.assertTrue((d / "SKILL.md").is_file())

        text = "---\nname: n\ndescription:  做什么的 \n---\nbody"
        self.assertEqual(_parse_frontmatter_description(text), "做什么的")
        self.assertEqual(_parse_frontmatter_description("no frontmatter"), "")


class TestPluginSkillDetection(_BridgeTestCase):
    """is_plugin_skill / 私有目录方法 / delete_skill 权限语义。"""

    def test_is_plugin_skill_via_host_list(self):
        from astrbot.core.skills.skill_manager import SkillManager

        fake = self._fake_bridge(
            list_skills=lambda self, active_only=False, runtime="": [
                {
                    "name": "helper",
                    "description": "",
                    "path": "data/plugins/myplug/skills/helper/SKILL.md",
                    "active": True,
                    "source_type": "plugin",
                    "source_label": "myplug",
                    "plugin_name": "myplug",
                    "readonly": True,
                },
                {
                    "name": "local1",
                    "description": "",
                    "path": "data/skills/local1/SKILL.md",
                    "active": True,
                    "source_type": "local_only",
                },
            ]
        )
        self._patch_bridge(fake)
        mgr = SkillManager()
        self.assertTrue(mgr.is_plugin_skill("helper"))
        self.assertFalse(mgr.is_plugin_skill("local1"))
        self.assertFalse(mgr.is_plugin_skill("missing"))

        d = mgr._get_plugin_skill_dir("helper")
        self.assertIsNotNone(d)
        self.assertEqual(str(d).replace("\\", "/"), "data/plugins/myplug/skills/helper")

        tuples = mgr._iter_plugin_skill_dirs()
        self.assertEqual(len(tuples), 1)
        name, plugin_name, skill_dir, preset = tuples[0]
        self.assertEqual((name, plugin_name, preset), ("helper", "myplug", False))

    def test_delete_plugin_skill_raises_permission_error(self):
        from astrbot.core.skills.skill_manager import SkillManager

        deleted = []

        def delete(self, name):
            deleted.append(name)
            return True

        fake = self._fake_bridge(
            list_skills=lambda self, active_only=False, runtime="": [
                {
                    "name": "helper",
                    "description": "",
                    "path": "data/plugins/myplug/skills/helper/SKILL.md",
                    "active": True,
                    "source_type": "plugin",
                    "plugin_name": "myplug",
                }
            ],
            delete_skill=delete,
        )
        self._patch_bridge(fake)
        mgr = SkillManager()
        with self.assertRaises(PermissionError):
            mgr.delete_skill("helper")
        self.assertEqual(deleted, [])
        # 非 plugin 技能正常转发
        mgr.delete_skill("local1")
        self.assertEqual(deleted, ["local1"])


class TestSandboxCacheSharedState(_BridgeTestCase):
    """沙盒缓存按本体真实现（与宿主共享 data/sandbox_skills_cache.json）。"""

    def _mgr(self):
        from astrbot.core.skills.skill_manager import SkillManager

        return SkillManager()  # sandbox cache = $ASTRBOT_DATA_PATH/sandbox_skills_cache.json

    def test_set_then_get_cache_roundtrip(self):
        from astrbot.core.skills import skill_manager as sm

        mgr = self._mgr()
        # 初始无缓存文件
        status = mgr.get_sandbox_skills_cache_status()
        self.assertFalse(status["exists"])
        self.assertEqual(status["count"], 0)
        self.assertFalse(status["ready"])
        self.assertIsNone(status["updated_at"])

        mgr.set_sandbox_skills_cache(
            [
                {"name": "sb1", "description": "d1", "path": "/workspace/skills/sb1/SKILL.md"},
                {"name": "bad name", "description": "x", "path": "p"},  # 非法名被滤除
                {"name": "sb1", "description": "dup", "path": "bad/../p"},  # 去重+路径净化
                "not-a-dict",
            ]
        )
        self.assertTrue(os.path.isfile(mgr.sandbox_skills_cache_path))
        status = mgr.get_sandbox_skills_cache_status()
        self.assertTrue(status["exists"])
        self.assertTrue(status["ready"])
        self.assertEqual(status["count"], 1)
        self.assertIsInstance(status["updated_at"], str)

        cache = mgr._load_sandbox_skills_cache()
        # 同名条目后写覆盖（对齐本体 dict 去重语义）；非法路径回退缺省路径
        self.assertEqual(
            cache["skills"],
            [{"name": "sb1", "description": "dup", "path": "/workspace/skills/sb1/SKILL.md"}],
        )
        self.assertEqual(cache["version"], sm._SANDBOX_SKILLS_CACHE_VERSION)

    def test_is_sandbox_only_skill_reads_shared_cache(self):
        from astrbot.core.skills.skill_manager import SkillManager

        mgr = self._mgr()
        # 本地存在的技能不是 sandbox-only
        local_dir = os.path.join(self._data_dir, "skills", "loc")
        os.makedirs(local_dir, exist_ok=True)
        with open(os.path.join(local_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\ndescription: d\n---\n")
        self.assertFalse(mgr.is_sandbox_only_skill("loc"))

        # 缓存中的名字是 sandbox-only
        mgr.set_sandbox_skills_cache(
            [{"name": "sb1", "description": "", "path": "/workspace/skills/sb1/SKILL.md"}]
        )
        self.assertTrue(mgr.is_sandbox_only_skill("sb1"))
        self.assertFalse(mgr.is_sandbox_only_skill("missing"))
        # 非法名（目录段不合法）直接 False
        self.assertFalse(mgr.is_sandbox_only_skill("../evil"))

    def test_set_active_on_sandbox_only_skill_raises(self):
        from astrbot.core.skills.skill_manager import SkillManager

        mgr = self._mgr()
        mgr.set_sandbox_skills_cache(
            [{"name": "sb1", "description": "", "path": "/workspace/skills/sb1/SKILL.md"}]
        )
        with self.assertRaises(PermissionError):
            mgr.set_skill_active("sb1", True)
        with self.assertRaises(PermissionError):
            mgr.delete_skill("sb1")

    def test_remove_skill_from_sandbox_cache(self):
        mgr = self._mgr()
        mgr.set_sandbox_skills_cache(
            [
                {"name": "a", "description": "", "path": ""},
                {"name": "b", "description": "", "path": ""},
            ]
        )
        mgr._remove_skill_from_sandbox_cache("a")
        names = [s["name"] for s in mgr._load_sandbox_skills_cache()["skills"]]
        self.assertEqual(names, ["b"])
        # 无变化不破坏文件
        mgr._remove_skill_from_sandbox_cache("zzz")
        names = [s["name"] for s in mgr._load_sandbox_skills_cache()["skills"]]
        self.assertEqual(names, ["b"])


class TestListWorkspaceSkills(_BridgeTestCase):
    """list_workspace_skills 对齐本体：仅认规范 SKILL.md、readonly=True。"""

    def _make_workspace(self, root, skill_name, md_name="SKILL.md", content=""):
        skill_dir = os.path.join(root, "skills", skill_name)
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, md_name), "w", encoding="utf-8") as f:
            f.write(content)

    def test_discovers_canonical_skill_md_only(self):
        from astrbot.core.skills.skill_manager import SkillManager

        ws = tempfile.mkdtemp(prefix="ws_")
        self._make_workspace(
            ws,
            "good",
            content="---\nname: good\ndescription: 它很有用\n---\n# t",
        )
        self._make_workspace(ws, "legacy", md_name="skill.md")
        self._make_workspace(ws, "bad name")

        skills = SkillManager().list_workspace_skills(ws)
        self.assertEqual([s.name for s in skills], ["good"])
        s = skills[0]
        self.assertEqual(s.description, "它很有用")
        self.assertEqual(s.source_type, "workspace")
        self.assertEqual(s.source_label, "workspace")
        self.assertTrue(s.readonly)
        self.assertTrue(s.local_exists)
        self.assertTrue(s.active)
        self.assertTrue(s.path.endswith("/SKILL.md"))

    def test_accepts_path_object_and_empty_roots(self):
        from pathlib import Path

        from astrbot.core.skills.skill_manager import SkillManager

        mgr = SkillManager()
        self.assertEqual(mgr.list_workspace_skills(None), [])
        self.assertEqual(mgr.list_workspace_skills(""), [])
        self.assertEqual(mgr.list_workspace_skills("/nonexistent_ws_root"), [])

        ws = tempfile.mkdtemp(prefix="ws2_")
        self._make_workspace(ws, "sk", content="---\ndescription: d\n---\n")
        skills = mgr.list_workspace_skills(Path(ws))  # Path 参数可用
        self.assertEqual(len(skills), 1)


class TestInstallSkillFromZip(_BridgeTestCase):
    """install_skill_from_zip 按本体移植后的行为。"""

    def _make_zip(self, path, entries):
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in entries.items():
                zf.writestr(name, data)

    def _mgr(self):
        from astrbot.core.skills.skill_manager import SkillManager

        return SkillManager()  # skills_root = $ASTRBOT_DATA_PATH/skills

    def test_root_mode_install(self):
        zip_path = os.path.join(self._data_dir, "my-skill.zip")
        self._make_zip(
            zip_path,
            {
                "SKILL.md": "---\nname: my-skill\ndescription: d\n---\n",
                "scripts/run.sh": "echo hi",
                "__MACOSX/._junk": "junk",
            },
        )
        self._patch_bridge(self._fake_bridge())
        mgr = self._mgr()
        installed = mgr.install_skill_from_zip(zip_path)
        self.assertEqual(installed, "my-skill")
        root = os.path.join(self._data_dir, "skills", "my-skill")
        self.assertTrue(os.path.isfile(os.path.join(root, "SKILL.md")))
        self.assertTrue(os.path.isfile(os.path.join(root, "scripts", "run.sh")))
        self.assertFalse(os.path.exists(os.path.join(root, "__MACOSX")))

    def test_root_mode_lowercase_skill_md(self):
        zip_path = os.path.join(self._data_dir, "lower.zip")
        self._make_zip(zip_path, {"skill.md": "---\ndescription: d\n---\n"})
        self._patch_bridge(self._fake_bridge())
        installed = self._mgr().install_skill_from_zip(
            zip_path, skill_name_hint="lower case"
        )
        self.assertEqual(installed, "lower_case")
        self.assertTrue(
            os.path.isfile(
                os.path.join(self._data_dir, "skills", "lower_case", "SKILL.md")
            )
        )

    def test_dir_mode_multiple_skills_returns_joined(self):
        zip_path = os.path.join(self._data_dir, "multi.zip")
        self._make_zip(
            zip_path,
            {
                "alpha/SKILL.md": "---\ndescription: a\n---\n",
                "beta/SKILL.md": "---\ndescription: b\n---\n",
            },
        )
        self._patch_bridge(self._fake_bridge())
        installed = self._mgr().install_skill_from_zip(zip_path)
        # 本体以 set 迭代顶层目录，返回顺序不保证；按集合断言
        self.assertEqual(set(installed.split(", ")), {"alpha", "beta"})
        for name in ("alpha", "beta"):
            self.assertTrue(
                os.path.isfile(os.path.join(self._data_dir, "skills", name, "SKILL.md"))
            )

    def test_overwrite_false_conflict_raises(self):
        zip_path = os.path.join(self._data_dir, "conflict.zip")
        self._make_zip(zip_path, {"alpha/SKILL.md": "---\ndescription: a\n---\n"})
        dest = os.path.join(self._data_dir, "skills", "alpha")
        os.makedirs(dest, exist_ok=True)
        self._patch_bridge(self._fake_bridge())
        mgr = self._mgr()
        with self.assertRaises(FileExistsError):
            mgr.install_skill_from_zip(zip_path, overwrite=False)
        # overwrite=True 覆盖安装成功
        installed = mgr.install_skill_from_zip(zip_path, overwrite=True)
        self.assertEqual(installed, "alpha")

    def test_invalid_inputs_raise(self):
        self._patch_bridge(self._fake_bridge())
        mgr = self._mgr()
        with self.assertRaises(FileNotFoundError):
            mgr.install_skill_from_zip(
                os.path.join(self._data_dir, "missing.zip")
            )
        not_zip = os.path.join(self._data_dir, "not_zip.bin")
        with open(not_zip, "wb") as f:
            f.write(b"definitely not a zip")
        with self.assertRaises(ValueError):
            mgr.install_skill_from_zip(not_zip)

        empty_zip = os.path.join(self._data_dir, "empty.zip")
        self._make_zip(empty_zip, {})
        with self.assertRaises(ValueError):
            mgr.install_skill_from_zip(empty_zip)

        hint_zip = os.path.join(self._data_dir, "hint.zip")
        self._make_zip(hint_zip, {"SKILL.md": "---\ndescription: d\n---\n"})
        with self.assertRaises(ValueError):  # 非法技能名 hint
            mgr.install_skill_from_zip(hint_zip, skill_name_hint="bad/../name")

        no_skill_zip = os.path.join(self._data_dir, "noskill.zip")
        self._make_zip(no_skill_zip, {"readme.txt": "no skill here"})
        with self.assertRaises(ValueError):
            mgr.install_skill_from_zip(no_skill_zip)


if __name__ == "__main__":
    unittest.main()
