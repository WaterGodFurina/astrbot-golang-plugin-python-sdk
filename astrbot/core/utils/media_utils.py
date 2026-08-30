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
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

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


def _is_temp_file(path: str) -> bool:
    """判断路径是否位于系统临时目录（as_path 退出时只清理解析出的临时文件）。"""
    try:
        tmp_abs = os.path.abspath(tempfile.gettempdir())
        return os.path.abspath(path).startswith(tmp_abs)
    except OSError:
        return False


def media_mime_for_path(path: str, fallback: str | None = None) -> str:
    """按扩展名（MEDIA_MIME_EXTENSIONS）推断媒体 MIME，失败回退
    ``application/octet-stream``。供 ResolvedMediaFile.as_path 组装。"""
    if path:
        ext = (Path(path).suffix or "").lower()
        for mime, suffix in MEDIA_MIME_EXTENSIONS.items():
            if suffix == ext:
                return mime
    guessed = mimetypes.guess_type(path)
    if guessed and guessed[0]:
        return guessed[0]
    if fallback and fallback.startswith("."):
        return media_mime_for_ext(fallback)
    return "application/octet-stream"


def media_mime_for_ext(ext: str) -> str:
    """``.wav``/``.mp3`` 等后缀转 MIME（audio 默认 wav，未知返回 octet-stream）。"""
    if not ext:
        return "application/octet-stream"
    normalized = ext.lower()
    if not normalized.startswith("."):
        normalized = f".{normalized}"
    for mime, suffix in MEDIA_MIME_EXTENSIONS.items():
        if suffix == normalized:
            return mime
    return "audio/wav" if normalized in (".wav", ".wave", ".silk") else "application/octet-stream"


@dataclass
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


@dataclass
class ResolvedMediaFile:
    """媒体引用解析为本地路径（对齐本体 ResolvedMediaFile）。

    SDK 薄壳：to_path 语义落地到临时/本地文件，as_path/open 退出后自动清理
    cleanup_paths（宿主 Go 侧转码能力原生，音频 target_format 在此不做格式
    转换，仅透传解析路径）。
    """

    source_ref: str | None = None
    media_type: str = "file"
    path: Path | None = None
    mime_type: str | None = None
    format: str | None = None
    cleanup_paths: list[Path] = field(default_factory=list)

    def read_bytes(self) -> bytes:
        """读取解析出的本地文件字节。"""
        if self.path is None:
            raise OSError("resolved media path is unavailable")
        return self.path.read_bytes()

    def to_base64(self) -> str:
        """读取文件并返回裸 base64。"""
        return base64.b64encode(self.read_bytes()).decode("utf-8")

    def to_data_url(self) -> str:
        """读取文件并返回 data URL。"""
        mime_type = self.mime_type or "application/octet-stream"
        return f"data:{mime_type};base64,{self.to_base64()}"

    def open(self, mode: str = "rb"):
        """打开解析出的本地文件。"""
        if self.path is None:
            raise OSError("resolved media path is unavailable")
        return self.path.open(mode)

    def detach(self) -> None:
        """as_path 退出时保留临时文件（对齐本体 detach 语义）。"""
        self.cleanup_paths.clear()

    def cleanup(self) -> None:
        """清理 resolver 拥有的临时文件。"""
        for p in self.cleanup_paths:
            try:
                if p.exists() and os.path.abspath(p).startswith(
                    os.path.abspath(tempfile.gettempdir())
                ):
                    p.unlink()
            except OSError:
                pass


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

    async def _resolve_file(
        self, *, target_format: str | None = None
    ) -> ResolvedMediaFile:
        """解析为 ResolvedMediaFile（含 cleanup_paths，供 as_path/open 清理）。"""
        path = await self._resolve_path(target_format=target_format)
        mime = media_mime_for_path(path, fallback=self.default_suffix)
        fmt = (Path(path).suffix or "").lstrip(".").lower() or None
        cleanup_paths = []
        if _is_temp_file(path):
            cleanup_paths.append(Path(path))
        return ResolvedMediaFile(
            source_ref=self.source,
            media_type=self.media_type,
            path=Path(path),
            mime_type=mime,
            format=fmt,
            cleanup_paths=cleanup_paths,
        )

    async def to_path(
        self,
        *,
        target_format: str | None = None,
        preserve_mp3: bool = False,
    ) -> str:
        """返回解析后的本地路径（保活临时文件，供平台 SDK 使用）。"""
        return await self._resolve_path(target_format=target_format)

    @asynccontextmanager
    async def as_path(
        self,
        *,
        target_format: str | None = None,
        preserve_mp3: bool = False,
    ) -> AsyncIterator[ResolvedMediaFile]:
        """解析为本地文件并在此退出后清理临时文件（对齐本体 as_path 语义）。"""
        resolved = await self._resolve_file(target_format=target_format)
        try:
            yield resolved
        finally:
            resolved.cleanup()

    @staticmethod
    def _try_cleanup(path: str) -> None:
        """尽力清理解析出的临时文件（临时目录内才删除，不误删用户文件）。"""
        try:
            tmp_dir = os.path.abspath(tempfile.gettempdir())
            if os.path.abspath(path).startswith(tmp_dir) and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    @asynccontextmanager
    async def open(
        self,
        mode: str = "rb",
        *,
        target_format: str | None = None,
        preserve_mp3: bool = False,
    ):
        """解析为本地文件并作为文件对象在上下文中打开（对齐本体 open 语义）。"""
        async with self.as_path(
            target_format=target_format, preserve_mp3=preserve_mp3
        ) as resolved:
            with resolved.open(mode) as file_obj:
                yield file_obj

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
        strict: bool = False,
        target_format: str | None = None,
        preserve_mp3: bool = False,
        default_mime_type: str | None = "image/jpeg",
    ) -> ResolvedMediaData | None:
        """异步转 base64 数据（对齐本体 to_base64_data 签名与语义）。

        解析失败时（文件不可读/不存在的本地引用）：strict=False 返回 None，
        strict=True 抛异常。image 分支优先按字节探测 MIME，探测不出时
        使用 default_mime_type。
        """
        path = await self._resolve_path(target_format=target_format)
        try:
            with open(path, "rb") as f:
                media_bytes = f.read()
        except OSError:
            if strict:
                raise
            return None
        finally:
            self._try_cleanup(path)

        if self.media_type == "image":
            mime_type = await detect_image_mime_type_async(
                media_bytes, default_mime_type=None
            )
            if not mime_type:
                if self.media_ref.startswith("base64://") or self.default_suffix in (
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp",
                    ".gif",
                ):
                    mime_type = default_mime_type or "image/jpeg"
                elif strict:
                    raise ValueError(
                        f"Invalid image file: {describe_media_ref(self.media_ref)}"
                    )
                else:
                    return None
            return ResolvedMediaData(
                base64_data=base64.b64encode(media_bytes).decode("utf-8"),
                mime_type=mime_type,
            )
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return ResolvedMediaData(
            base64_data=base64.b64encode(media_bytes).decode("utf-8"),
            mime_type=mime,
            format=target_format or None,
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
    "ResolvedMediaData",
    "ResolvedMediaFile",
    "describe_media_ref",
    "detect_image_mime_type",
    "detect_image_mime_type_async",
    "file_uri_to_path",
    "is_file_uri",
    "media_mime_for_ext",
    "media_mime_for_path",
    "resolve_audio_ref_to_base64_data",
    "resolve_image_ref_to_base64_data",
    "resolve_media_ref_to_base64_data",
]


def describe_media_ref(media_ref: object | None) -> str:
    """描述媒体引用类型（对齐原版 describe_media_ref）。

    返回媒体引用的可读描述（URL / base64:// / data: / file:// / 本地路径 /
    bytes / None），供日志与调试使用。
    """
    if media_ref is None:
        return "none"
    if isinstance(media_ref, bytes):
        return f"bytes({len(media_ref)}B)"
    ref = str(media_ref)
    if ref.startswith("data:"):
        return "data-uri"
    if ref.startswith("base64://"):
        return "base64-uri"
    if ref.startswith("http://") or ref.startswith("https://"):
        return "http-url"
    if is_file_uri(ref):
        return "file-uri"
    if os.path.isfile(ref):
        return "local-file"
    return "unknown"


async def resolve_image_ref_to_base64_data(
    image_ref: str,
    strict: bool = False,
    default_mime_type: str | None = "image/jpeg",
) -> ResolvedMediaData | None:
    """把图片引用解析为 base64 数据（对齐原版语义）。

    ``strict=False`` 时解析失败返回 None（不抛异常）；``strict=True`` 时
    解析失败抛 ValueError。
    """
    try:
        data = await MediaResolver(
            image_ref, media_type="image", default_suffix=".bin"
        ).to_base64_data(strict=strict, default_mime_type=default_mime_type)
    except Exception:
        if strict:
            raise
        return None
    if data is None or not data.base64_data:
        if strict:
            raise ValueError(f"Invalid image: {describe_media_ref(image_ref)}")
        return None
    if not data.mime_type or data.mime_type == "application/octet-stream":
        data.mime_type = default_mime_type or "image/jpeg"
    return data


async def resolve_audio_ref_to_base64_data(
    audio_ref: str,
    preserve_mp3: bool = False,
    target_format: str | None = None,
) -> ResolvedMediaData:
    """把音频引用解析为 base64 数据（对齐原版语义）。

    音频默认转 WAV；preserve_mp3=True 且原源为 MP3 时保持不变。
    Go 宿主对音频转码原生支持，target_format 在 SDK 侧仅作透传（不做格式
    转换），但接口签名与本体对齐。
    """
    audio_data = await MediaResolver(
        audio_ref,
        media_type="audio",
        default_suffix=".wav",
    ).to_base64_data(
        target_format=target_format,
        preserve_mp3=preserve_mp3,
        strict=True,
    )
    if audio_data is None:
        raise ValueError(f"Invalid audio data: {describe_media_ref(audio_ref)}")
    return audio_data


async def resolve_media_ref_to_base64_data(
    media_ref: str,
    media_type: str,
    strict: bool = False,
) -> ResolvedMediaData | None:
    """把媒体引用解析为 base64 数据（对齐原版语义）。

    - image → resolve_image_ref_to_base64_data
    - audio → resolve_audio_ref_to_base64_data
    - 其它按 media_type 直接解析
    """
    if media_type == "image":
        return await resolve_image_ref_to_base64_data(media_ref, strict=strict)
    if media_type == "audio":
        return await resolve_audio_ref_to_base64_data(media_ref)
    try:
        return await MediaResolver(media_ref, media_type=media_type).to_base64_data(
            strict=strict
        )
    except Exception:
        if strict:
            raise
        return None