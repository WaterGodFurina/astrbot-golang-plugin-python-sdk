"""utils.media_utils 公开面对齐本体单测。

覆盖：
- 本体有而原 SDK 缺失的模块级函数存在且为 async：get_media_duration /
  convert_audio_to_opus / convert_audio_to_amr / convert_audio_to_wav /
  convert_audio_format / convert_video_format / ensure_wav / ensure_jpeg /
  extract_video_cover / compress_image
- 本体常量对齐：AUDIO_FORMAT_MIME_TYPES / DEFAULT_MEDIA_SUFFIXES /
  IMAGE_COMPRESS_DEFAULT_*
- is_file_uri / file_uri_to_path 对齐本体语义（大小写不敏感、非字符串）
- resolve_*_to_base64_data 与 MediaResolver 方法的 keyword-only 参数
  （preserve_mp3 / strict / default_mime_type 等，防按位置传参错位）
"""
import asyncio
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import astrbot.core.utils.media_utils as mu  # noqa: E402

_ASYNC_FUNCS = (
    "get_media_duration",
    "convert_audio_format",
    "convert_audio_to_opus",
    "convert_audio_to_amr",
    "convert_audio_to_wav",
    "convert_video_format",
    "ensure_wav",
    "ensure_jpeg",
    "extract_video_cover",
    "compress_image",
    "detect_image_mime_type_async",
    "resolve_image_ref_to_base64_data",
    "resolve_audio_ref_to_base64_data",
    "resolve_media_ref_to_base64_data",
)


class TestMediaUtilsModuleSurface(unittest.TestCase):
    def test_baseline_async_funcs_exist(self):
        for name in _ASYNC_FUNCS:
            fn = getattr(mu, name, None)
            self.assertIsNotNone(fn, f"media_utils.{name} 缺失（本体有）")
            self.assertTrue(
                inspect.iscoroutinefunction(fn),
                f"media_utils.{name} 应为 async 函数",
            )

    def test_baseline_constants_aligned(self):
        self.assertEqual(
            mu.AUDIO_FORMAT_MIME_TYPES["tencent_silk"], "audio/silk"
        )
        self.assertEqual(mu.AUDIO_FORMAT_MIME_TYPES["wav"], "audio/wav")
        self.assertEqual(
            mu.DEFAULT_MEDIA_SUFFIXES,
            {"audio": ".wav", "image": ".bin", "video": ".mp4", "file": ".bin"},
        )
        self.assertEqual(mu.IMAGE_COMPRESS_DEFAULT_MAX_SIZE, 1280)
        self.assertEqual(mu.IMAGE_COMPRESS_DEFAULT_QUALITY, 95)
        self.assertTrue(mu.IMAGE_COMPRESS_DEFAULT_OPTIMIZE)
        self.assertEqual(mu.IMAGE_COMPRESS_DEFAULT_MIN_FILE_SIZE_MB, 1.0)
        self.assertEqual(mu.MEDIA_MIME_EXTENSIONS["image/jpeg"], ".jpg")

    def test_is_file_uri_semantics(self):
        self.assertTrue(mu.is_file_uri("file:///tmp/a.png"))
        self.assertTrue(mu.is_file_uri("FILE:///tmp/a.png"))  # 大小写不敏感
        self.assertFalse(mu.is_file_uri("/tmp/a.png"))
        self.assertFalse(mu.is_file_uri("http://x.com/a.png"))
        self.assertFalse(mu.is_file_uri(None))  # 非字符串返回 False
        self.assertFalse(mu.is_file_uri(123))

    def test_file_uri_to_path(self):
        self.assertEqual(mu.file_uri_to_path("/tmp/plain.png"), "/tmp/plain.png")
        self.assertEqual(mu.file_uri_to_path("file:///tmp/a%20b.png"), "/tmp/a b.png")
        # 非 file: 输入原样返回
        self.assertEqual(mu.file_uri_to_path("https://x/y.png"), "https://x/y.png")

    def test_resolve_funcs_are_keyword_only_after_ref(self):
        for name in (
            "resolve_image_ref_to_base64_data",
            "resolve_audio_ref_to_base64_data",
        ):
            params = inspect.signature(getattr(mu, name)).parameters
            for key in ("strict", "default_mime_type", "preserve_mp3", "target_format"):
                if key in params:
                    self.assertEqual(
                        params[key].kind,
                        inspect.Parameter.KEYWORD_ONLY,
                        f"{name}.{key} 应为 keyword-only（对齐本体）",
                    )

    def test_resolve_media_ref_media_type_is_keyword_only(self):
        params = inspect.signature(mu.resolve_media_ref_to_base64_data).parameters
        self.assertEqual(params["media_type"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(params["strict"].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_media_resolver_methods_keyword_only(self):
        params = inspect.signature(mu.MediaResolver.to_base64_data).parameters
        for key in ("strict", "target_format", "preserve_mp3", "default_mime_type"):
            self.assertEqual(
                params[key].kind,
                inspect.Parameter.KEYWORD_ONLY,
                f"MediaResolver.to_base64_data.{key} 应为 keyword-only",
            )
        params = inspect.signature(mu.MediaResolver.to_path).parameters
        for key in ("target_format", "preserve_mp3"):
            self.assertEqual(params[key].kind, inspect.Parameter.KEYWORD_ONLY)


class TestMediaUtilsBehavior(unittest.IsolatedAsyncioTestCase):
    async def test_get_media_duration_missing_file_returns_none(self):
        # ffprobe 缺失或文件不存在：返回 None 而非抛异常（对齐本体降级）
        result = await mu.get_media_duration("/nonexistent/media.wav")
        self.assertIsNone(result)

    async def test_ensure_wav_missing_file_returns_path(self):
        # 对齐本体：文件不存在（平台竞态）原样返回
        self.assertEqual(
            await mu.ensure_wav("/nonexistent/audio.mp3"), "/nonexistent/audio.mp3"
        )

    async def test_ensure_wav_already_wav_returns_path(self):
        import struct
        import tempfile

        # 写一个 RIFF/WAVE 头的最小文件（magic 探测通过即可）
        fd, path = tempfile.mkstemp(suffix=".wav")
        with os.fdopen(fd, "wb") as f:
            f.write(b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + b"\x00" * 16)
        try:
            self.assertEqual(await mu.ensure_wav(path), path)
        finally:
            os.remove(path)

    async def test_ensure_jpeg_missing_file_returns_path(self):
        self.assertEqual(
            await mu.ensure_jpeg("/nonexistent/img.png"), "/nonexistent/img.png"
        )

    async def test_compress_image_remote_url_returned_as_is(self):
        url = "https://example.com/big.png"
        self.assertEqual(await mu.compress_image(url), url)

    async def test_detect_image_mime_type_bytes(self):
        # 最小 PNG 头
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        )
        self.assertEqual(await mu.detect_image_mime_type_async(png), "image/png")


if __name__ == "__main__":
    unittest.main()
