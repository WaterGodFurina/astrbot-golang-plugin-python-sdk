"""内置工具类 name/description/parameters schema 与本体逐字段对齐单测。

期望值取自本体 astrbot-py v4.27.4 astrbot/core/tools/（只读快照），
覆盖 tools/ 树下全部薄壳工具类：
- name 与本体一字不差（工具名是宿主 Go 原生执行的路由键）；
- description 与本体一致；
- parameters 的 type/properties 键集/required 与本体一致。

重点回归（修复点）：
- shipyard_neo/browser.py 三工具此前 parameters 为空 dict，补齐后应与
  本体 schema 一致（browser.py:42-66 / 100-128 / 165-177）；
- LocalExecuteShellTool 继承 ExecuteShellTool 且 description 覆写
  （本体 shell.py:182-219），不带独立 builtin_tool 注册。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.tools import computer_tools as ct
from astrbot.core.tools import cron_tools, knowledge_base_tools, message_tools
from astrbot.core.tools import web_search_tools as wst
from astrbot.core.tools.computer_tools.shipyard_neo import browser, neo_skills


def _props(tool):
    return set(tool.parameters.get("properties", {}).keys())


def _required(tool):
    return tuple(tool.parameters.get("required", ()))


class TestCronToolsSchema(unittest.TestCase):
    def test_future_task(self):
        t = cron_tools.FutureTaskTool
        self.assertEqual(t.name, "future_task")
        self.assertIn("Manage your future tasks", t.description)
        self.assertEqual(
            _props(t()),
            {"action", "name", "cron_expression", "note", "run_once", "run_at", "job_id"},
        )
        self.assertEqual(_required(t()), ("action",))
        self.assertEqual(
            t().parameters["properties"]["action"]["enum"],
            ["create", "edit", "delete", "list"],
        )


class TestKnowledgeBaseToolsSchema(unittest.TestCase):
    def test_kb_query_tool(self):
        t = knowledge_base_tools.KnowledgeBaseQueryTool
        self.assertEqual(t.name, "astr_kb_search")
        self.assertIn("Query the knowledge base", t.description)
        self.assertEqual(_props(t()), {"query"})
        self.assertEqual(_required(t()), ("query",))


class TestMessageToolsSchema(unittest.TestCase):
    def test_send_message_to_user(self):
        t = message_tools.SendMessageToUserTool
        self.assertEqual(t.name, "send_message_to_user")
        self.assertIn("Send message to the user", t.description)
        self.assertEqual(_props(t()), {"messages", "session"})
        self.assertEqual(_required(t()), ("messages",))
        items = t().parameters["properties"]["messages"]["items"]
        self.assertEqual(
            set(items["properties"].keys()),
            {"type", "text", "path", "url", "mention_user_id"},
        )
        self.assertEqual(items["required"], ["type"])

    def test_get_group_message_history(self):
        t = message_tools.GetGroupMessageHistoryTool
        self.assertEqual(t.name, "get_group_message_history")
        self.assertEqual(_props(t()), {"limit", "before_id", "keyword", "sender"})
        self.assertEqual(_required(t()), ())
        self.assertEqual(
            t().parameters["properties"]["limit"]["default"], 20,
        )


class TestWebSearchToolsSchema(unittest.TestCase):
    def test_tool_names_order(self):
        self.assertEqual(
            wst.WEB_SEARCH_TOOL_NAMES,
            [
                "web_search_tavily",
                "tavily_extract_web_page",
                "web_search_bocha",
                "web_search_brave",
                "web_search_firecrawl",
                "firecrawl_extract_web_page",
                "web_search_baidu",
                "web_search_exa",
                "exa_get_contents",
            ],
        )

    def _assert_query_required(self, tool_cls, props, required=("query",)):
        self.assertEqual(_required(tool_cls()), required)
        self.assertEqual(_props(tool_cls()), props)

    def _assert_url_required(self, tool_cls, props):
        """extract 类工具本体 required 为 ["url"]（本体 web_search_tools.py:692）。"""
        self._assert_query_required(tool_cls, props, required=("url",))

    def test_tavily(self):
        t = wst.TavilyWebSearchTool
        self.assertEqual(t.name, "web_search_tavily")
        self._assert_query_required(
            t,
            {"query", "max_results", "search_depth", "topic", "days",
             "time_range", "start_date", "end_date"},
        )

    def test_tavily_extract(self):
        t = wst.TavilyExtractWebPageTool
        self.assertEqual(t.name, "tavily_extract_web_page")
        self._assert_url_required(t, {"url", "extract_depth"})

    def test_bocha(self):
        t = wst.BochaWebSearchTool
        self.assertEqual(t.name, "web_search_bocha")
        self._assert_query_required(
            t, {"query", "freshness", "summary", "include", "exclude", "count"}
        )

    def test_brave(self):
        t = wst.BraveWebSearchTool
        self.assertEqual(t.name, "web_search_brave")
        self._assert_query_required(
            t, {"query", "count", "country", "search_lang", "freshness"}
        )

    def test_firecrawl(self):
        t = wst.FirecrawlWebSearchTool
        self.assertEqual(t.name, "web_search_firecrawl")
        self._assert_query_required(
            t, {"query", "limit", "location", "country", "timeout"}
        )

    def test_firecrawl_extract(self):
        t = wst.FirecrawlExtractWebPageTool
        self.assertEqual(t.name, "firecrawl_extract_web_page")
        self._assert_url_required(
            t, {"url", "format", "only_main_content", "timeout", "max_age"}
        )

    def test_baidu(self):
        t = wst.BaiduWebSearchTool
        self.assertEqual(t.name, "web_search_baidu")
        self._assert_query_required(
            t, {"query", "top_k", "search_recency_filter", "site"}
        )

    def test_exa(self):
        t = wst.ExaWebSearchTool
        self.assertEqual(t.name, "web_search_exa")
        self._assert_query_required(
            t,
            {"query", "num_results", "type", "category", "include_domains",
             "exclude_domains", "start_published_date", "end_published_date"},
        )

    def test_exa_get_contents(self):
        t = wst.ExaGetContentsTool
        self.assertEqual(t.name, "exa_get_contents")
        self._assert_url_required(t, {"url", "max_characters"})

    def test_search_result_dataclass(self):
        result = wst.SearchResult(title="t", url="u", snippet="s")
        self.assertIsNone(result.favicon)


class TestCuaToolsSchema(unittest.TestCase):
    def test_screenshot(self):
        t = ct.CuaScreenshotTool
        self.assertEqual(t.name, "astrbot_cua_screenshot")
        self.assertEqual(_props(t()), {"send_to_user", "return_image_to_llm"})
        self.assertEqual(_required(t()), ())

    def test_mouse_click(self):
        t = ct.CuaMouseClickTool
        self.assertEqual(t.name, "astrbot_cua_mouse_click")
        self.assertEqual(_props(t()), {"x", "y", "button"})
        self.assertEqual(_required(t()), ("x", "y"))

    def test_keyboard_type(self):
        t = ct.CuaKeyboardTypeTool
        self.assertEqual(t.name, "astrbot_cua_keyboard_type")
        self.assertEqual(_props(t()), {"text"})
        self.assertEqual(_required(t()), ("text",))


class TestFsToolsSchema(unittest.TestCase):
    def test_file_read(self):
        t = ct.FileReadTool
        self.assertEqual(t.name, "astrbot_file_read_tool")
        self.assertEqual(_props(t()), {"path", "offset", "limit"})
        self.assertEqual(_required(t()), ("path",))

    def test_file_write(self):
        t = ct.FileWriteTool
        self.assertEqual(t.name, "astrbot_file_write_tool")
        self.assertEqual(_props(t()), {"path", "content"})
        self.assertEqual(_required(t()), ("path", "content"))

    def test_file_edit(self):
        t = ct.FileEditTool
        self.assertEqual(t.name, "astrbot_file_edit_tool")
        self.assertEqual(_props(t()), {"path", "old", "new", "replace_all"})
        self.assertEqual(_required(t()), ("path", "old", "new"))

    def test_grep(self):
        t = ct.GrepTool
        self.assertEqual(t.name, "astrbot_grep_tool")
        self.assertEqual(
            _props(t()),
            {"pattern", "path", "glob", "-A", "-B", "-C", "result_limit"},
        )
        self.assertEqual(_required(t()), ("pattern",))

    def test_file_upload(self):
        t = ct.FileUploadTool
        self.assertEqual(t.name, "astrbot_upload_file")
        self.assertEqual(_props(t()), {"local_path"})
        self.assertEqual(_required(t()), ("local_path",))

    def test_file_download(self):
        t = ct.FileDownloadTool
        self.assertEqual(t.name, "astrbot_download_file")
        self.assertEqual(_props(t()), {"remote_path", "also_send_to_user"})
        self.assertEqual(_required(t()), ("remote_path",))


class TestPythonAndShellToolsSchema(unittest.TestCase):
    def test_sandbox_python(self):
        t = ct.PythonTool
        self.assertEqual(t.name, "astrbot_execute_ipython")
        self.assertIn("Run codes in an IPython shell", t.description)
        self.assertEqual(_props(t()), {"code", "silent", "timeout"})
        self.assertEqual(_required(t()), ("code",))

    def test_local_python(self):
        t = ct.LocalPythonTool
        self.assertEqual(t.name, "astrbot_execute_python")
        self.assertIn("Execute codes in a Python environment", t.description)
        self.assertEqual(_props(t()), {"code", "silent", "timeout"})
        self.assertEqual(_required(t()), ("code",))

    def test_execute_shell(self):
        t = ct.ExecuteShellTool
        self.assertEqual(t.name, "astrbot_execute_shell")
        self.assertEqual(_props(t()), {"command", "background", "timeout", "env"})
        self.assertEqual(_required(t()), ("command",))

    def test_local_execute_shell_overrides_description(self):
        t = ct.LocalExecuteShellTool
        self.assertEqual(t.name, "astrbot_execute_shell")
        self.assertIn("managed shell session ID", t.description)
        self.assertEqual(
            _props(t()), {"command", "yield_time_ms", "timeout", "env"}
        )
        self.assertEqual(_required(t()), ("command",))

    def test_shell_session(self):
        t = ct.ShellSessionTool
        self.assertEqual(t.name, "astrbot_shell_session")
        self.assertEqual(
            t().parameters["properties"]["action"]["enum"],
            ["list", "poll", "write", "write_line", "interrupt", "terminate"],
        )
        self.assertEqual(_required(t()), ("action",))


class TestShipyardNeoBrowserSchema(unittest.TestCase):
    """修复回归：browser 三工具 parameters 此前为空 dict。"""

    def test_browser_exec(self):
        t = browser.BrowserExecTool
        self.assertEqual(t.name, "astrbot_execute_browser")
        self.assertEqual(
            _props(t()),
            {"cmd", "timeout", "description", "tags", "learn", "include_trace"},
        )
        self.assertEqual(_required(t()), ("cmd",))

    def test_browser_batch_exec(self):
        t = browser.BrowserBatchExecTool
        self.assertEqual(t.name, "astrbot_execute_browser_batch")
        self.assertEqual(
            _props(t()),
            {"commands", "timeout", "stop_on_error", "description", "tags",
             "learn", "include_trace"},
        )
        self.assertEqual(_required(t()), ("commands",))

    def test_run_browser_skill(self):
        t = browser.RunBrowserSkillTool
        self.assertEqual(t.name, "astrbot_run_browser_skill")
        self.assertEqual(
            _props(t()),
            {"skill_key", "timeout", "stop_on_error", "include_trace",
             "description", "tags"},
        )
        self.assertEqual(_required(t()), ("skill_key",))


class TestShipyardNeoSkillsSchema(unittest.TestCase):
    def test_get_execution_history(self):
        t = neo_skills.GetExecutionHistoryTool
        self.assertEqual(t.name, "astrbot_get_execution_history")
        self.assertEqual(
            _props(t()),
            {"exec_type", "success_only", "limit", "offset", "tags",
             "has_notes", "has_description"},
        )
        self.assertEqual(_required(t()), ())

    def test_annotate_execution(self):
        t = neo_skills.AnnotateExecutionTool
        self.assertEqual(t.name, "astrbot_annotate_execution")
        self.assertEqual(
            _props(t()), {"execution_id", "description", "tags", "notes"}
        )
        self.assertEqual(_required(t()), ("execution_id",))

    def test_create_skill_payload(self):
        t = neo_skills.CreateSkillPayloadTool
        self.assertEqual(t.name, "astrbot_create_skill_payload")
        self.assertEqual(_props(t()), {"payload", "kind"})
        self.assertEqual(_required(t()), ("payload",))

    def test_get_skill_payload(self):
        t = neo_skills.GetSkillPayloadTool
        self.assertEqual(t.name, "astrbot_get_skill_payload")
        self.assertEqual(_props(t()), {"payload_ref"})
        self.assertEqual(_required(t()), ("payload_ref",))

    def test_create_skill_candidate(self):
        t = neo_skills.CreateSkillCandidateTool
        self.assertEqual(t.name, "astrbot_create_skill_candidate")
        self.assertEqual(
            _props(t()), {"skill_key", "source_execution_ids", "scenario_key", "payload_ref"}
        )
        self.assertEqual(_required(t()), ("skill_key", "source_execution_ids"))

    def test_list_skill_candidates(self):
        t = neo_skills.ListSkillCandidatesTool
        self.assertEqual(t.name, "astrbot_list_skill_candidates")
        self.assertEqual(_props(t()), {"status", "skill_key", "limit", "offset"})
        self.assertEqual(_required(t()), ())

    def test_evaluate_skill_candidate(self):
        t = neo_skills.EvaluateSkillCandidateTool
        self.assertEqual(t.name, "astrbot_evaluate_skill_candidate")
        self.assertEqual(
            _props(t()), {"candidate_id", "passed", "score", "benchmark_id", "report"}
        )
        self.assertEqual(_required(t()), ("candidate_id", "passed"))

    def test_promote_skill_candidate(self):
        t = neo_skills.PromoteSkillCandidateTool
        self.assertEqual(t.name, "astrbot_promote_skill_candidate")
        self.assertEqual(_props(t()), {"candidate_id", "stage", "sync_to_local"})
        self.assertEqual(_required(t()), ("candidate_id",))

    def test_list_skill_releases(self):
        t = neo_skills.ListSkillReleasesTool
        self.assertEqual(t.name, "astrbot_list_skill_releases")
        self.assertEqual(
            _props(t()), {"skill_key", "active_only", "stage", "limit", "offset"}
        )
        self.assertEqual(_required(t()), ())

    def test_rollback_skill_release(self):
        t = neo_skills.RollbackSkillReleaseTool
        self.assertEqual(t.name, "astrbot_rollback_skill_release")
        self.assertEqual(_props(t()), {"release_id"})
        self.assertEqual(_required(t()), ("release_id",))

    def test_sync_skill_release(self):
        t = neo_skills.SyncSkillReleaseTool
        self.assertEqual(t.name, "astrbot_sync_skill_release")
        self.assertEqual(_props(t()), {"release_id", "skill_key", "require_stable"})
        self.assertEqual(_required(t()), ())


class TestNormalizer(unittest.TestCase):
    def test_normalize_legacy_web_search_config_migrates_keys(self):
        cfg = {
            "provider_settings": {
                "web_search": True,
                "websearch_provider": "default",
                "websearch_tavily_key": "single-key",
                "websearch_bocha_key": "",
            }
        }
        wst.normalize_legacy_web_search_config(cfg)
        self.assertFalse(cfg["provider_settings"]["web_search"])
        self.assertEqual(cfg["provider_settings"]["websearch_tavily_key"], ["single-key"])
        self.assertEqual(cfg["provider_settings"]["websearch_bocha_key"], [])

    def test_normalize_noop_without_provider_settings(self):
        cfg = {}
        wst.normalize_legacy_web_search_config(cfg)
        self.assertEqual(cfg, {})


if __name__ == "__main__":
    unittest.main()
