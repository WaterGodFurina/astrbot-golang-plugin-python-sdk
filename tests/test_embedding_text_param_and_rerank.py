"""EmbeddingProvider.get_embeddings(text=...) 参数名与 RerankProvider.rerank 签名。

本体 get_embeddings 第一参数名是 text: list[str]（astrbot-py provider.py:332），
SDK 原用 texts，按名传参 text= 会 TypeError；rerank 本体为抽象方法
（provider.py:422-429），SDK 降级补齐签名 + 空列表缺省实现。
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.provider.entities import RerankResult
from astrbot.core.provider.provider import EmbeddingProvider, RerankProvider


def _make_embedding(record=None):
    class P(EmbeddingProvider):
        def __init__(self):
            self.provider_config = {}
            self.model_name = "emb"

        async def get_embeddings(self, text):
            if record is not None:
                record.append(list(text))
            return [[0.1, 0.2] for _ in text]

        def get_dim(self):
            return 2

    return P()


class TestGetEmbeddingsTextParam(unittest.TestCase):
    def test_text_kwarg_accepted(self):
        """get_embeddings(text=[...]) 按名传参不炸（对齐本体参数名）。"""
        p = _make_embedding()
        out = asyncio.run(p.get_embeddings(text=["a", "b"]))
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], [0.1, 0.2])

    def test_text_kwarg_reaches_impl(self):
        """按名传参的 text 到达子类实现（参数名真实对齐而非吞参）。"""
        seen = []
        p = _make_embedding(record=seen)
        asyncio.run(p.get_embeddings(text=["x", "y", "z"]))
        self.assertEqual(seen, [["x", "y", "z"]])

    def test_batch_still_works(self):
        """get_embeddings_batch 继续可用（内部按位置调 get_embeddings）。"""
        p = _make_embedding()
        out = asyncio.run(p.get_embeddings_batch(["a", "b", "c"], batch_size=2))
        self.assertEqual(len(out), 3)


class TestRerankProviderSignature(unittest.TestCase):
    def test_rerank_accepts_kwargs_and_returns_list(self):
        """rerank(query=..., documents=..., top_n=...) 按名传参不炸，返回列表。"""

        class P(RerankProvider):
            def __init__(self):
                self.provider_config = {}
                self.model_name = "rr"

        p = P()
        out = asyncio.run(p.rerank(query="q", documents=["a", "b"], top_n=1))
        self.assertEqual(out, [])

    def test_rerank_result_entity_fields(self):
        """RerankResult 字段面与本体一致（index/relevance_score）。"""
        r = RerankResult(index=3, relevance_score=0.5)
        self.assertEqual(r.index, 3)
        self.assertEqual(r.relevance_score, 0.5)
        self.assertEqual(r.to_dict(), {"index": 3, "relevance_score": 0.5})


class TestProviderTextChatSignature(unittest.TestCase):
    def test_text_chat_accepts_body_kwargs(self):
        """text_chat 按名传 contexts=/model=/tool_calls_result= 不再 TypeError
        （签名对齐本体参数集，宿主桥无通道的参数被安全忽略）。"""

        class P(EmbeddingProvider):
            pass

        from astrbot.core.provider.provider import Provider

        class ChatP(Provider):
            def __init__(self):
                self.provider_config = {}
                self.model_name = "c"

            def _bridge(self):
                class B:
                    async def chat_llm_async(self, prompt, system_prompt, images, sid):
                        return "ok:" + prompt

                return B()

        p = ChatP()
        resp = asyncio.run(
            p.text_chat(
                prompt="hi",
                session_id="s1",
                contexts=[{"role": "user", "content": "ctx"}],
                model="m1",
                tool_calls_result=None,
                audio_urls=[],
                extra_user_content_parts=[],
            )
        )
        self.assertEqual(resp.completion_text, "ctx\nhi")


if __name__ == "__main__":
    unittest.main()
