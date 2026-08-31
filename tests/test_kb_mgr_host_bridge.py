"""kb_mgr.py 宿主桥真实现（KBRetrieve / KBUploadFromURL / KBListKBs）的单测。

覆盖点：
- retrieve：结果解析（context_text/results）、参数透传、无结果 → None、
  kb_names 空 → {}、全不可用 → ValueError、宿主不可用 → None（降级）、
  旧宿主桥（无 kb_* 方法）→ None（降级）；
- upload_from_url：chunk_size/chunk_overlap 透传、宿主不可用 → no-op；
- list_kbs / get_kb / get_kb_by_name：kbs_json 解析为 dict 列表、客户端
  按 kb_id/kb_name 过滤、宿主不可用回退本地占位表。

运行：python3 tests/test_kb_mgr_host_bridge.py
"""
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


def _mgr():
    from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager

    return KnowledgeBaseManager()


class TestKBRetrieveBridge(_BridgeTestCase):
    """retrieve 真实现：解析 / 本体语义 / 降级。"""

    async def test_retrieve_parses_results_and_passes_params(self):
        calls = {}

        def kb_retrieve(self, query="", kb_names=None, top_k_fusion=20, top_m_final=5):
            calls["args"] = (query, list(kb_names or []), top_k_fusion, top_m_final)
            return {
                "context_text": "CTX",
                "results": [{"doc_name": "a.md", "score": 0.9}],
            }

        def kb_list_kbs(self):
            return [{"kb_id": "k1", "kb_name": "kb1"}]

        self._patch_bridge(
            self._fake_bridge(kb_retrieve=kb_retrieve, kb_list_kbs=kb_list_kbs)
        )
        out = await _mgr().retrieve("q", ["kb1"], top_k_fusion=7, top_m_final=3)
        self.assertEqual(out["context_text"], "CTX")
        self.assertEqual(out["results"], [{"doc_name": "a.md", "score": 0.9}])
        self.assertEqual(calls["args"], ("q", ["kb1"], 7, 3))

    async def test_retrieve_no_results_returns_none(self):
        """检索已执行但无结果（有可用 KB）→ None（本体语义）。"""
        self._patch_bridge(
            self._fake_bridge(
                kb_retrieve=lambda self, query="", kb_names=None,
                top_k_fusion=20, top_m_final=5: {"context_text": "", "results": []},
                kb_list_kbs=lambda self: [{"kb_id": "k1", "kb_name": "kb1"}],
            )
        )
        self.assertIsNone(await _mgr().retrieve("q", ["kb1"]))

    async def test_retrieve_empty_kb_names_no_results_returns_empty_dict(self):
        """kb_names 为空且无不可用项（宿主无 KB）→ {}（本体语义）。"""
        self._patch_bridge(
            self._fake_bridge(
                kb_retrieve=lambda self, query="", kb_names=None,
                top_k_fusion=20, top_m_final=5: {"context_text": "", "results": []},
                kb_list_kbs=lambda self: [],
            )
        )
        self.assertEqual(await _mgr().retrieve("q", []), {})

    async def test_retrieve_all_unavailable_raises_value_error(self):
        """指定的知识库全部不可用（宿主 KBListKBs 中不存在）→ ValueError。"""
        self._patch_bridge(
            self._fake_bridge(
                kb_retrieve=lambda self, query="", kb_names=None,
                top_k_fusion=20, top_m_final=5: {"context_text": "", "results": []},
                kb_list_kbs=lambda self: [{"kb_id": "k1", "kb_name": "kb1"}],
            )
        )
        with self.assertRaises(ValueError):
            await _mgr().retrieve("q", ["missing"])

    async def test_retrieve_partial_unavailable_filters_names(self):
        """部分不可用：仅用可用的 kb_names 继续检索。"""
        calls = {}

        def kb_retrieve(self, query="", kb_names=None, top_k_fusion=20, top_m_final=5):
            calls["kb_names"] = list(kb_names or [])
            return {"context_text": "CTX", "results": [{"doc_name": "a"}]}

        self._patch_bridge(
            self._fake_bridge(
                kb_retrieve=kb_retrieve,
                kb_list_kbs=lambda self: [{"kb_name": "kb1"}],
            )
        )
        out = await _mgr().retrieve("q", ["kb1", "ghost"])
        self.assertEqual(out["results"], [{"doc_name": "a"}])
        self.assertEqual(calls["kb_names"], ["kb1"])

    async def test_retrieve_host_unavailable_returns_none(self):
        self._patch_bridge(None)
        self.assertIsNone(await _mgr().retrieve("q", ["kb1"]))

    async def test_retrieve_old_bridge_without_kb_methods_degrades(self):
        """旧宿主桥（无 kb_* 方法）→ None，不抛 AttributeError。"""
        self._patch_bridge(self._fake_bridge())
        self.assertIsNone(await _mgr().retrieve("q", ["kb1"]))


class TestKBUploadBridge(_BridgeTestCase):
    """upload_from_url 真实现：透传 / 降级。"""

    async def test_upload_from_url_forwards_with_chunk_params(self):
        calls = {}

        def kb_upload_from_url(self, kb_id="", url="", chunk_size=512, chunk_overlap=50):
            calls.update(kb_id=kb_id, url=url, chunk_size=chunk_size,
                         chunk_overlap=chunk_overlap)
            return True

        self._patch_bridge(self._fake_bridge(kb_upload_from_url=kb_upload_from_url))
        await _mgr().upload_from_url("k1", "http://x/a.md", chunk_size=256,
                                     chunk_overlap=16)
        self.assertEqual(calls, {"kb_id": "k1", "url": "http://x/a.md",
                                 "chunk_size": 256, "chunk_overlap": 16})

    async def test_upload_from_url_accepts_progress_callback_without_calling(self):
        """progress_callback 保留参数但 SDK 侧无法跨进程回调，不触发。"""
        fired = []

        def cb(current, total):
            fired.append((current, total))

        self._patch_bridge(
            self._fake_bridge(
                kb_upload_from_url=lambda self, kb_id="", url="",
                chunk_size=512, chunk_overlap=50: True
            )
        )
        await _mgr().upload_from_url("k1", "http://x/a.md", progress_callback=cb)
        self.assertEqual(fired, [])

    async def test_upload_from_url_host_unavailable_is_noop(self):
        self._patch_bridge(None)
        await _mgr().upload_from_url("k1", "http://x/a.md")  # 不抛即通过


class TestKBListBridge(_BridgeTestCase):
    """list_kbs / get_kb / get_kb_by_name 真实现。"""

    async def test_list_kbs_parses_kbs_json_dicts(self):
        kbs = [
            {"kb_id": "k1", "kb_name": "kb1", "description": "d", "doc_count": 2},
            {"kb_id": "k2", "kb_name": "kb2"},
        ]
        self._patch_bridge(self._fake_bridge(kb_list_kbs=lambda self: kbs))
        out = await _mgr().list_kbs()
        self.assertEqual(out, kbs)
        for kb in out:
            self.assertIsInstance(kb, dict)

    async def test_get_kb_filters_by_kb_id(self):
        self._patch_bridge(
            self._fake_bridge(
                kb_list_kbs=lambda self: [
                    {"kb_id": "k1", "kb_name": "kb1"},
                    {"kb_id": "k2", "kb_name": "kb2"},
                ]
            )
        )
        kb = await _mgr().get_kb("k2")
        self.assertEqual(kb["kb_name"], "kb2")
        self.assertIsNone(await _mgr().get_kb("missing"))

    async def test_get_kb_by_name_filters_by_kb_name(self):
        self._patch_bridge(
            self._fake_bridge(
                kb_list_kbs=lambda self: [
                    {"kb_id": "k1", "kb_name": "kb1"},
                    {"kb_id": "k2", "kb_name": "kb2"},
                ]
            )
        )
        kb = await _mgr().get_kb_by_name("kb1")
        self.assertEqual(kb["kb_id"], "k1")
        self.assertIsNone(await _mgr().get_kb_by_name("ghost"))

    async def test_list_kbs_host_unavailable_falls_back_local(self):
        """宿主不可用 → 回退本地占位表（当前恒空，不抛）。"""
        self._patch_bridge(None)
        self.assertEqual(await _mgr().list_kbs(), [])
        self.assertIsNone(await _mgr().get_kb("k1"))
        self.assertIsNone(await _mgr().get_kb_by_name("kb1"))


if __name__ == "__main__":
    unittest.main()
