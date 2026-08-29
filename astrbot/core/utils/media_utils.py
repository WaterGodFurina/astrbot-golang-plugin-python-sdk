"""媒体解析工具（Go 宿主兼容运行时，对齐本体 utils.media_utils）。

提供 `MediaResolver`（唯一权威定义：`message.components` 与
`provider.entities` 均 re-export 本模块的 MediaResolver，避免同名不同义）、
`MEDIA_MIME_EXTENSIONS` 常量与 `detect_image_mime_type_async` 异步探测。
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import mimetypes
import os
import re
import tempfile

logger = logging.getLogger("astrbot")

# 媒体 MIME → 扩展名映射（对齐本体 media_utils.MEDIA_MIME_EXTENSIONS）
MEDIA_MIME_EXTENSIONS = {
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/flac": ".flac",
    "audio/aac": ".aac",
    "audio/amr": ".amr",
    "audio/silk": ".silk",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/avif": ".avif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}

# 图片格式 → MIME 类型（探测时使用，对齐本体 IMAGE_FORMAT_MIME_TYPES）
IMAGE_FORMAT_MIME_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
    "AVIF": "image/avif",
}


def _download_to_temp(url: str, suffix: str = "") -> str:
    """下载 HTTP(S) URL 到临时文件，返回本地路径（SDK 薄壳实现）。"""
    from astrbot.core.utils.io import _download_to_temp as _impl

    return _impl(url, suffix)


def detect_image_mime_type(
    image_source,
    *,
    default_mime_type: str | None = "image/jpeg",
) -> str | None:
    """检测图片 MIME 类型：支持 bytes / 本地路径 / file:// URI。"""
    data: bytes | None = None
    if isinstance(image_source, bytes):
        data = image_source
    else:
        path = str(image_source)
        if path.startswith("file://"):
            from urllib.parse import unquote, urlparse

            parsed = urlparse(path)
            path = unquote(parsed.path)
        if os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                data = None

    import io

    if data is None:
        return default_mime_type
    try:
        from PIL import Image as PILImage

        with PILImage.open(io.BytesIO(data)) as image:
            image.verify()
            fmt = str(image.format or "").upper()
    except Exception:
        fmt = ""
    return IMAGE_FORMAT_MIME_TYPES.get(fmt, default_mime_type)


async def detect_image_mime_type_async(
    image_source,
    *,
    default_mime_type: str | None = "image/jpeg",
) -> str | None:
    """异步图片 MIME 探测（不阻塞事件循环）。"""
    return await asyncio.to_thread(
        detect_image_mime_type,
        image_source,
        default_mime_type=default_mime_type,
    )


def is_file_uri(s: str) -> bool:
    """判断字符串是否为 file:// URI。"""
    return isinstance(s, str) and s.startswith("file://")


def file_uri_to_path(uri: str) -> str:
    """file:// URI 转本地路径。"""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(uri)
    path = unquote(parsed.path)
    if parsed.netloc and parsed.netloc not in ("", "localhost"):
        path = f"//{parsed.netloc}{path}"
    return path


class ResolvedMediaData:
    """Base64 媒体字节及 payload 所需元数据（对齐本体 ResolvedMediaData）。"""

    base64_data: str
    mime_type: str
    format: str | None = None

    def to_bytes(self) -> bytes:
        """解码 base64 负载（容忍缺失 padding）。"""
        raw = self.base64_data
        padding = len(raw) % 4
        if padding:
            raw += "=" * (4 - padding)
        return base64.b64decode(raw)

    def to_data_url(self) -> str:
        """返回 ``data:<mime>;base64,...`` URL。"""
        return f"data:{self.mime_type};base64,{self.base64_data}"


class MediaResolver:
    """媒体解析器（SDK 权威实现）。

    接受本地路径 / file:// URI / http(s) URL / base64:// 负载 /
    data:data-uri，返回本地路径（to_path）或 base64（to_base64）。
    与 message.components / provider.entities 共用同一类。
    """

    def __init__(
        self,
        media_ref: str = "",
        *,
        media_type: str = "file",
        default_suffix: str | None = None,
        **kwargs,
    ):
        # 兼容位置参数 source（message.components 旧签名）
        self.source = media_ref or kwargs.get("source", "")
        self.media_type = media_type or "file"
        self.default_suffix = default_suffix
        self.media_ref = media_ref or self.source

    async def _resolve_path(self, *, target_format: str | None = None) -> str:
        """解析为本地路径：base64/data-uri 落盘，http(s) 下载，本地原样。"""
        source = self.source
        if source.startswith("base64://"):
            suffix = (
                f".{target_format}"
                if target_format
                else self.default_suffix or ".bin"
            )
            return self._write_base64(source[len("base64://"):], suffix)
        if source.startswith("data:"):
            m = re.match(r"data:[^;]+;base64,(.*)", source, re.S)
            if not m:
                raise ValueError(
                    f"不支持的 data: URI（仅支持 data:*;base64,... 编码）: {source[:64]!r}"
                )
            suffix = f".{target_format}" if target_format else ".bin"
            return self._write_base64(m.group(1), suffix)
        if source.startswith("http://") or source.startswith("https://"):
            suffix = self.default_suffix or ".bin"
            return _download_to_temp(source, suffix)
        if is_file_uri(source):
            return file_uri_to_path(source)
        if os.path.exists(source):
            return source
        raise ValueError(f"媒体源既非 URL/URI 也不是存在的本地文件: {source!r}")

    async def _resolve_path_and_bytes(
        self, *, target_format: str | None = None, cleanup: bool = False
    ) -> tuple[str, bytes]:
        """解析为 (路径, 字节)，cleanup=True 时清理临时文件。"""
        path = await self._resolve_path(target_format=target_format)
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception:
            if cleanup:
                self._try_cleanup(path)
            raise
        if cleanup:
            self._try_cleanup(path)
        return path, data

    @staticmethod
    def _try_cleanup(path: str) -> None:
        """尽力清理解析出的临时文件（临时目录内才删除，不误删用户文件）。"""
        try:
            tmp_dir = os.path.abspath(tempfile.gettempdir())
            if os.path.abspath(path).startswith(tmp_dir) and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    async def to_path(
        self,
        *,
        target_format: str | None = None,
        preserve_mp3: bool = False,
    ) -> str:
        """返回解析后的本地路径（保活临时文件，供平台 SDK 使用）。"""
        return await self._resolve_path(target_format=target_format)

    async def to_bytes(
        self,
        *,
        target_format: str | None = None,
        preserve_mp3: bool = False,
    ) -> bytes:
        """解析媒体为字节并清理临时文件。"""
        _path, data = await self._resolve_path_and_bytes(
            target_format=target_format, cleanup=True
        )
        return data

    async def to_base64(
        self,
        *,
        target_format: str | None = None,
        preserve_mp3: bool = False,
    ) -> str:
        """解析为 base64 字符串（无 data: 前缀）。"""
        path = await self._resolve_path(target_format=target_format)
        try:
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode()
        finally:
            self._try_cleanup(path)

    async def to_base64_data(
        self,
        *,
        target_format: str | None = None,
        preserve_mp3: bool = False,
    ) -> "ResolvedMediaData":
        """异步转 base64 数据（对齐本体返回 ResolvedMediaData）。"""
        path = await self._resolve_path(target_format=target_format)
        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
        finally:
            self._try_cleanup(path)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return ResolvedMediaData(
            base64_data=b64, mime_type=mime, format=target_format or None
        )

    async def to_data_url(
        self,
        *,
        strict: bool = False,
        target_format: str | None = None,
        preserve_mp3: bool = False,
        default_mime_type: str | None = "image/jpeg",
    ) -> str | None:
        """解析为数据 URL（对齐本体 to_data_url 签名与语义）。"""
        if self.media_ref.startswith("data:"):
            return self.media_ref
        if self.media_ref.startswith("base64://"):
            return f"data:{self.media_type};base64,{self.media_ref[len('base64://'):]}"
        resolved = await self.to_base64_data(
            target_format=target_format,
            preserve_mp3=preserve_mp3,
        )
        return resolved.to_data_url() if resolved else None

    def resolve(self, **kwargs) -> dict:
        """解析为可发送形式的 dict（对齐 provider/entities 简版语义）。"""
        return {
            "url": self.media_ref,
            "type": self.media_type,
            "media_type": self.media_type,
            **(kwargs or {}),
        }

    @staticmethod
    def _write_base64(b64: str, suffix: str) -> str:
        try:
            data = base64.b64decode(b64)
        except binascii.Error:
            data = b64.encode()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(data)
        tmp.close()
        return tmp.name


__all__ = [
    "MEDIA_MIME_EXTENSIONS",
    "MediaResolver",
    "detect_image_mime_type",
    "detect_image_mime_type_async",
    "file_uri_to_path",
    "is_file_uri",
]