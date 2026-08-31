"""TTSProvider.get_audio_stream 默认实现（对齐本体：累积文本 → get_audio → 队列）。"""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.provider.provider import TTSProvider


def _make_tts(audio_path: str):
    class FakeTTS(TTSProvider):
        def __init__(self):
            self.provider_config = {}
            self.model_name = "tts"

        async def get_audio(self, text: str) -> str:
            self.calls.append(text)
            return audio_path

    p = FakeTTS()
    p.calls = []
    return p


class TestGetAudioStream(unittest.TestCase):
    def test_accumulates_and_emits_bytes_then_none(self):
        """文本片段累积后一次性合成，输出 (text, bytes) 与 None 结束标记。"""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"RIFF-fake-audio")
            path = f.name
        try:
            p = _make_tts(path)

            async def run():
                text_q: asyncio.Queue = asyncio.Queue()
                audio_q: asyncio.Queue = asyncio.Queue()
                for part in ("hello", " world", None):
                    await text_q.put(part)
                await p.get_audio_stream(text_q, audio_q)
                return audio_q

            audio_q = asyncio.run(run())
            item = audio_q.get_nowait()
            self.assertEqual(item[0], "hello world")
            self.assertEqual(item[1], b"RIFF-fake-audio")
            self.assertIs(audio_q.get_nowait(), None)
            self.assertEqual(p.calls, ["hello world"])
        finally:
            os.unlink(path)

    def test_get_audio_failure_still_puts_none(self):
        """get_audio 失败（返回空路径）→ 跳过音频数据但仍发 None 结束标记。"""
        p = _make_tts("")  # open("") 抛 FileNotFoundError → 被吞掉

        async def run():
            text_q: asyncio.Queue = asyncio.Queue()
            audio_q: asyncio.Queue = asyncio.Queue()
            await text_q.put("hi")
            await text_q.put(None)
            await p.get_audio_stream(text_q, audio_q)
            return audio_q

        audio_q = asyncio.run(run())
        self.assertIs(audio_q.get_nowait(), None)


if __name__ == "__main__":
    unittest.main()
