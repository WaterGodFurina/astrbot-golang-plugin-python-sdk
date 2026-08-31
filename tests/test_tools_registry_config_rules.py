"""tools/registry.py 内置工具注册表与配置规则对齐单测。

覆盖点（对齐本体 astrbot/core/tools/registry.py）：
- BuiltinToolConfigCondition：equals/in/truthy/custom 操作符、点路径取值、
  缺失键 actual=None（_MISSING 哨兵语义）；
- BuiltinToolConfigRule：conditions 评估与 evaluator 优先级；
- builtin_tool 装饰器：config= 注册规则、同名冲突抛 ValueError；
- get_builtin_tool_config_statuses / get_builtin_tool_config_tags 与本体
  同签名（tool_name + config_entries → list[dict]）；
- send_message_to_user 自定义评估器（wecom_ai_bot / 不支持平台分支）；
- ensure_builtin_tools_loaded 后 get_builtin_tool_class / name 可双向查询。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.tools.registry import (
    BuiltinToolConfigCondition,
    BuiltinToolConfigRule,
    _BUILTIN_TOOL_CONFIG_RULES,
    _builtin_tool_classes_by_name,
    _builtin_tool_names_by_class,
    builtin_tool,
    ensure_builtin_tools_loaded,
    get_builtin_tool_class,
    get_builtin_tool_config_rule,
    get_builtin_tool_config_statuses,
    get_builtin_tool_config_tags,
    get_builtin_tool_name,
    iter_builtin_tool_classes,
)


class TestBuiltinToolConfigCondition(unittest.TestCase):
    def test_equals_operator(self):
        cond = BuiltinToolConfigCondition(key="a.b", operator="equals", expected=True)
        self.assertTrue(cond.evaluate({"a": {"b": True}})["matched"])
        self.assertFalse(cond.evaluate({"a": {"b": False}})["matched"])

    def test_missing_key_actual_is_none(self):
        cond = BuiltinToolConfigCondition(key="a.b", operator="equals", expected=True)
        result = cond.evaluate({})
        self.assertIsNone(result["actual"])
        self.assertFalse(result["matched"])

    def test_in_operator(self):
        cond = BuiltinToolConfigCondition(key="rt", operator="in", expected=("local", "sandbox"))
        self.assertTrue(cond.evaluate({"rt": "local"})["matched"])
        self.assertFalse(cond.evaluate({"rt": "cua"})["matched"])

    def test_truthy_operator(self):
        cond = BuiltinToolConfigCondition(key="flag", operator="truthy")
        self.assertTrue(cond.evaluate({"flag": 1})["matched"])
        # 缺失键时 actual 为 _MISSING 哨兵（object()），bool 为 True——
        # 与本体 registry.evaluate 的既有行为逐字一致（bool(actual)）。
        self.assertTrue(cond.evaluate({})["matched"])

    def test_custom_operator(self):
        cond = BuiltinToolConfigCondition(key="k", operator="custom", expected=True)
        self.assertTrue(cond.evaluate({})["matched"])

    def test_unsupported_operator_raises(self):
        cond = BuiltinToolConfigCondition(key="k", operator="nope")
        with self.assertRaises(ValueError):
            cond.evaluate({})

    def test_json_safe_converts_tuple(self):
        cond = BuiltinToolConfigCondition(key="k", operator="in", expected=("a", "b"))
        result = cond.evaluate({"k": "a"})
        self.assertIsInstance(result["expected"], list)


class TestBuiltinToolConfigRule(unittest.TestCase):
    def test_conditions_evaluate_all(self):
        rule = BuiltinToolConfigRule(
            conditions=(
                BuiltinToolConfigCondition(key="a", operator="equals", expected=1),
                BuiltinToolConfigCondition(key="b", operator="truthy"),
            )
        )
        self.assertEqual(len(rule.evaluate({"a": 1, "b": True})), 2)

    def test_evaluator_has_priority(self):
        rule = BuiltinToolConfigRule(
            conditions=(BuiltinToolConfigCondition(key="a", operator="truthy"),),
            evaluator=lambda config: [{"key": "x", "matched": True}],
        )
        result = rule.evaluate({})
        self.assertEqual(result, [{"key": "x", "matched": True}])


class TestBuiltinToolDecorator(unittest.TestCase):
    def test_config_registers_rule(self):
        @builtin_tool(config={"kb_agentic_mode": True})
        class _Tool:
            name = "registry_rule_tool"

        try:
            rule = get_builtin_tool_config_rule("registry_rule_tool")
            self.assertIsInstance(rule, BuiltinToolConfigRule)
            statuses = get_builtin_tool_config_statuses(
                "registry_rule_tool",
                [{"conf_id": "c", "conf_name": "C", "config": {"kb_agentic_mode": True}}],
            )
            self.assertTrue(statuses[0]["enabled"])
        finally:
            _BUILTIN_TOOL_CONFIG_RULES.pop("registry_rule_tool", None)
            _builtin_tool_classes_by_name.pop("registry_rule_tool", None)
            _builtin_tool_names_by_class.pop(_Tool, None)

    def test_duplicate_name_conflict_raises(self):
        @builtin_tool
        class _ToolA:
            name = "registry_conflict_tool"

        try:
            with self.assertRaises(ValueError):

                @builtin_tool
                class _ToolB:
                    name = "registry_conflict_tool"

        finally:
            _builtin_tool_classes_by_name.pop("registry_conflict_tool", None)
            _builtin_tool_names_by_class.pop(_ToolA, None)


class TestBuiltinToolConfigStatusesSignature(unittest.TestCase):
    """statuses/tags 与本体同签名：两参、返回 list[dict]。"""

    def test_statuses_returns_list_for_matching_entry(self):
        statuses = get_builtin_tool_config_statuses(
            "astr_kb_search",
            [{"conf_id": "c1", "conf_name": "C1", "config": {"kb_agentic_mode": True}}],
        )
        self.assertIsInstance(statuses, list)
        self.assertEqual(len(statuses), 1)
        self.assertTrue(statuses[0]["enabled"])
        self.assertEqual(statuses[0]["conf_id"], "c1")
        self.assertIn("matched_conditions", statuses[0])
        self.assertIn("failed_conditions", statuses[0])

    def test_statuses_disabled_entry(self):
        statuses = get_builtin_tool_config_statuses(
            "astr_kb_search",
            [{"conf_id": "c2", "conf_name": "C2", "config": {}}],
        )
        self.assertFalse(statuses[0]["enabled"])
        self.assertEqual(statuses[0]["failed_conditions"][0]["key"], "kb_agentic_mode")

    def test_tags_filters_enabled_only(self):
        entries = [
            {"conf_id": "on", "conf_name": "On", "config": {"kb_agentic_mode": True}},
            {"conf_id": "off", "conf_name": "Off", "config": {}},
        ]
        tags = get_builtin_tool_config_tags("astr_kb_search", entries)
        self.assertEqual([tag["conf_id"] for tag in tags], ["on"])

    def test_no_rule_returns_empty_list(self):
        self.assertEqual(get_builtin_tool_config_statuses("no_such_tool", []), [])


class TestSendMessageEvaluator(unittest.TestCase):
    def _statuses(self, config):
        return get_builtin_tool_config_statuses("send_message_to_user", [{"conf_id": "p", "conf_name": "P", "config": config}])

    def test_common_platform_supported(self):
        statuses = self._statuses(
            {"platform": [{"enable": True, "type": "aiocqhttp", "id": "x"}]}
        )
        self.assertTrue(statuses[0]["enabled"])

    def test_wecom_not_supported(self):
        statuses = self._statuses(
            {"platform": [{"enable": True, "type": "wecom", "id": "x"}]}
        )
        self.assertFalse(statuses[0]["enabled"])

    def test_wecom_ai_bot_requires_webhook(self):
        no_hook = self._statuses(
            {"platform": [{"enable": True, "type": "wecom_ai_bot", "id": "x"}]}
        )
        self.assertFalse(no_hook[0]["enabled"])
        with_hook = self._statuses(
            {
                "platform": [
                    {
                        "enable": True,
                        "type": "wecom_ai_bot",
                        "id": "x",
                        "msg_push_webhook_url": "https://example/hook",
                    }
                ]
            }
        )
        self.assertTrue(with_hook[0]["enabled"])

    def test_platform_not_list_falls_back(self):
        statuses = self._statuses({"platform": "bad"})
        self.assertFalse(statuses[0]["enabled"])


class TestBuiltinToolsLoaded(unittest.TestCase):
    EXPECTED_TOOL_NAMES = {
        "future_task", "astr_kb_search", "send_message_to_user",
        "get_group_message_history",
        "web_search_tavily", "tavily_extract_web_page", "web_search_bocha",
        "web_search_brave", "web_search_firecrawl", "firecrawl_extract_web_page",
        "web_search_baidu", "web_search_exa", "exa_get_contents",
        "astrbot_cua_screenshot", "astrbot_cua_mouse_click", "astrbot_cua_keyboard_type",
        "astrbot_file_read_tool", "astrbot_file_write_tool", "astrbot_file_edit_tool",
        "astrbot_grep_tool", "astrbot_upload_file", "astrbot_download_file",
        "astrbot_execute_ipython", "astrbot_execute_python",
        "astrbot_execute_shell", "astrbot_shell_session",
        "astrbot_execute_browser", "astrbot_execute_browser_batch",
        "astrbot_run_browser_skill",
        "astrbot_get_execution_history", "astrbot_annotate_execution",
        "astrbot_create_skill_payload", "astrbot_get_skill_payload",
        "astrbot_create_skill_candidate", "astrbot_list_skill_candidates",
        "astrbot_evaluate_skill_candidate", "astrbot_promote_skill_candidate",
        "astrbot_list_skill_releases", "astrbot_rollback_skill_release",
        "astrbot_sync_skill_release",
    }

    def test_all_builtin_tools_registered_after_ensure_loaded(self):
        ensure_builtin_tools_loaded()
        names = {get_builtin_tool_name(cls) for cls in iter_builtin_tool_classes()}
        self.assertEqual(names, self.EXPECTED_TOOL_NAMES)

    def test_get_builtin_tool_class_roundtrip(self):
        for name in self.EXPECTED_TOOL_NAMES:
            cls = get_builtin_tool_class(name)
            self.assertIsNotNone(cls, name)
            self.assertEqual(get_builtin_tool_name(cls), name)


if __name__ == "__main__":
    unittest.main()
