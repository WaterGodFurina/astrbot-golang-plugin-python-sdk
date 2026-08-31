"""媒体解析工具（Go 宿主兼容运行时，对齐本体 utils.media_utils）。

提供 `MediaResolver`（唯一权威定义：`message.components` 与
`provider.entities` 均 re-export 本模块的 MediaResolver，避免同名不同义）、
`MEDIA_MIME_EXTENSIONS` 等常量、引用物化与 MIME 探测，并移植本体的媒体
转码/探测/压缩公开函数（get_media_duration / convert_audio_* /
convert_video_format / ensure_wav / ensure_jpeg / extract_video_cover /
compress_image）。ffmpeg 未安装时转码函数按本体语义抛异常；tencent_silk
转写依赖 tencent_record_helper（SDK 未内置）时 ensure_wav 降级走 ffmpeg。
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import io
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, TypeAlias
from urllib.parse import unquote, urlparse, urlsplit
from urllib.request import url2pathname

from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
from astrbot.core.utils.datetime_utils import generate_timestamp_id

logger = logging.getLogger("astrbot")

# 图片压缩缺省参数（对齐本体 IMAGE_COMPRESS_DEFAULT_*）
IMAGE_COMPRESS_DEFAULT_MAX_SIZE = 1280
IMAGE_COMPRESS_DEFAULT_QUALITY = 95
IMAGE_COMPRESS_DEFAULT_OPTIMIZE = True
IMAGE_COMPRESS_DEFAULT_MIN_FILE_SIZE_MB = 1.0

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

# 音频格式 → MIME 类型（对齐本体 AUDIO_FORMAT_MIME_TYPES）
AUDIO_FORMAT_MIME_TYPES = {
    "aac": "audio/aac",
    "amr": "audio/amr",
    "flac": "audio/flac",
    "mp3": "audio/mp3",
    "ogg": "audio/ogg",
    "opus": "audio/opus",
    "silk": "audio/silk",
    "tencent_silk": "audio/silk",
    "wav": "audio/wav",
}

# 媒体家族 → 缺省临时文件后缀（对齐本体 DEFAULT_MEDIA_SUFFIXES）
DEFAULT_MEDIA_SUFFIXES = {
    "audio": ".wav",
    "image": ".bin",
    "video": ".mp4",
    "file": ".bin",
}

MediaRefStr: TypeAlias = str
"""MediaResolver 接受的媒体引用串（本地路径 / file URI / http(s) URL /
base64:// 负载 / data URI / 旧版裸 base64）。"""


def _download_to_temp(url: str, suffix: str = "") -> str:
    """下载 HTTP(S) URL 到临时文件，返回本地路径（SDK 薄壳实现）。"""
    from astrbot.core.utils.io import _download_to_temp as _impl

    return _impl(url, suffix)


def _sniff_image_format(data: bytes) -> str:
    """按文件头 magic 字节嗅探图片格式（PIL 不可用时的降级探测）。

    返回 IMAGE_FORMAT_MIME_TYPES 的键（大写格式名），未识别返回 ""。
    """
    if not isinstance(data, bytes) or len(data) < 12:
        return ""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "GIF"
    if data.startswith(b"BM"):
        return "BMP"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "ICO"
    return ""


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
        # PIL 缺失或打开失败：按文件头 magic 兜底探测（覆盖常见格式），
        # 探测不出时回落 default_mime_type（本体无 PIL 环境不存在，
        # SDK 宿主依赖未必含 Pillow，此处为增强降级）。
        fmt = _sniff_image_format(data)
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


def is_file_uri(value: object) -> bool:
    """判断值是否为 file: URI（对齐本体 is_file_uri）。

    仅当值为字符串且解析出的 URI scheme 为 ``file``（大小写不敏感）时
    返回 True；非字符串返回 False。
    """
    if not isinstance(value, str):
        return False
    try:
        return urlsplit(value).scheme.lower() == "file"
    except ValueError:
        return False


def file_uri_to_path(file_uri: MediaRefStr) -> str:
    """file: URI 转本地路径（对齐本体 file_uri_to_path）。

    处理远端主机（UNC）、Windows 盘符与旧版本 ``file:////path`` 形式；
    非 file: 输入原样返回。
    """
    if not is_file_uri(file_uri):
        return file_uri

    parsed = urlparse(file_uri)
    netloc = parsed.netloc or ""
    path = parsed.path or ""
    if netloc and netloc.lower() != "localhost":
        if len(netloc) == 2 and netloc[1] == ":" and netloc[0].isalpha():
            return str(Path(url2pathname(f"{netloc}{path}")))
        return str(Path(url2pathname(f"//{netloc}{path}")))

    path = url2pathname(path)
    if len(path) >= 4 and path[0] == "/" and path[2] == ":" and path[1].isalpha():
        path = path[1:]
    elif os.name != "nt" and path.startswith("//"):
        # 旧版本 AstrBot 对 POSIX 绝对路径生成 file:////path
        path = "/" + path.lstrip("/")
    return str(Path(path))


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
            strict=strict,
            target_format=target_format,
            preserve_mp3=preserve_mp3,
            default_mime_type=default_mime_type,
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
    "AUDIO_FORMAT_MIME_TYPES",
    "DEFAULT_MEDIA_SUFFIXES",
    "IMAGE_COMPRESS_DEFAULT_MAX_SIZE",
    "IMAGE_COMPRESS_DEFAULT_MIN_FILE_SIZE_MB",
    "IMAGE_COMPRESS_DEFAULT_OPTIMIZE",
    "IMAGE_COMPRESS_DEFAULT_QUALITY",
    "IMAGE_FORMAT_MIME_TYPES",
    "MEDIA_MIME_EXTENSIONS",
    "MediaRefStr",
    "MediaResolver",
    "ResolvedMediaData",
    "ResolvedMediaFile",
    "compress_image",
    "convert_audio_format",
    "convert_audio_to_amr",
    "convert_audio_to_opus",
    "convert_audio_to_wav",
    "convert_video_format",
    "describe_media_ref",
    "detect_image_mime_type",
    "detect_image_mime_type_async",
    "ensure_jpeg",
    "ensure_wav",
    "extract_video_cover",
    "file_uri_to_path",
    "get_media_duration",
    "is_file_uri",
    "media_mime_for_ext",
    "media_mime_for_path",
    "resolve_audio_ref_to_base64_data",
    "resolve_image_ref_to_base64_data",
    "resolve_media_ref_to_base64_data",
]


def describe_media_ref(media_ref: object | None) -> str:
    """返回媒体引用的 log 安全描述（对齐本体 describe_media_ref）。

    避免把签名 URL 查询串、token 与 base64 负载内容写进日志。
    """
    if not media_ref:
        return "<empty media ref>"
    if not isinstance(media_ref, str):
        return f"media ref type={type(media_ref).__name__}"

    ref_len = len(media_ref)
    if media_ref.startswith("data:"):
        header, _, payload = media_ref.partition(",")
        mime_type = header[5:].split(";", 1)[0] or "unknown"
        return f"data URI mime={mime_type!r} payload_len={len(payload)}"

    if media_ref.startswith("base64://"):
        return f"base64 media payload_len={len(media_ref.removeprefix('base64://'))}"

    parsed = urlparse(media_ref)
    if parsed.scheme in {"http", "https"}:
        filename = Path(unquote(parsed.path or "")).name
        suffix = f" file={filename!r}" if filename else ""
        return f"{parsed.scheme} URL host={parsed.netloc!r}{suffix} len={ref_len}"

    if is_file_uri(media_ref):
        filename = Path(file_uri_to_path(media_ref)).name
        return f"file URI name={filename!r} len={ref_len}"

    media_path_exists = False
    try:
        media_path_exists = Path(media_ref).exists()
    except OSError:
        pass
    if not media_path_exists:
        compact = "".join(media_ref.split())
        if compact:
            try:
                _decode_base64_media_payload(
                    compact,
                    error_message="invalid bare base64 media payload",
                    validate=True,
                )
            except ValueError:
                pass
            else:
                return f"bare base64 media payload_len={len(compact)}"

    return f"local media path name={Path(media_ref).name!r} len={ref_len}"


async def resolve_image_ref_to_base64_data(
    image_ref: MediaRefStr,
    *,
    strict: bool = False,
    default_mime_type: str | None = "image/jpeg",
) -> ResolvedMediaData | None:
    """把图片引用解析为 base64 数据（签名与语义对齐本体）。

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
    audio_ref: MediaRefStr,
    *,
    preserve_mp3: bool = False,
    target_format: str | None = None,
) -> ResolvedMediaData:
    """把音频引用解析为 base64 数据（签名与语义对齐本体）。

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
    media_ref: MediaRefStr,
    *,
    media_type: str,
    strict: bool = False,
) -> ResolvedMediaData | None:
    """把媒体引用解析为 base64 数据（签名与语义对齐本体）。

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

def _get_audio_magic_type(audio_path: str) -> str:
    """按文件头 magic bytes 识别音频格式（对齐本体 _get_audio_magic_type）。

    返回 wav/amr/ogg/opus/flac/mp3/mp4/silk 之一，无法识别返回空串。
    """
    try:
        with open(audio_path, "rb") as f:
            header = f.read(64)
    except FileNotFoundError:
        logger.warning("WAV probe file not found: %s", audio_path)
        return ""
    except Exception as e:
        logger.warning("WAV probe failed: %s, error: %s", audio_path, e)
        return ""

    if len(header) < 12:
        return ""

    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "wav"

    if header[:4] == b"#!AM":
        return "amr"

    if header[:4] == b"OggS":
        if b"OpusHead" in header:
            return "opus"
        return "ogg"

    if header[:3] == b"fLa":
        return "flac"

    if header[:3] == b"ID3" or header[:2] == b"\xff\xfb":
        return "mp3"

    if header[:4] == b"ftyp" and b"mp4" in header[:8]:
        return "mp4"

    if header.startswith(b"#!SILK_V3"):
        return "silk"

    # 腾讯 SILK：前导 \x02 字节 + #!SILK_V3
    if header.startswith(b"\x02#!SILK_V3"):
        return "silk"

    return ""


def _temp_media_path(media_type: str, suffix: str) -> Path:
    """在 AstrBot 临时目录下生成唯一的媒体物化路径（对齐本体）。"""
    temp_dir = Path(get_astrbot_temp_path())
    temp_dir.mkdir(parents=True, exist_ok=True)
    safe_media_type = "".join(
        char if char.isalnum() or char in {"_", "-"} else "_" for char in media_type
    )
    return temp_dir / f"media_{safe_media_type}_{generate_timestamp_id()}{suffix}"


async def get_media_duration(file_path: str) -> int | None:
    """用 ffprobe 探测媒体时长（对齐本体 get_media_duration）。

    Returns:
        时长（毫秒）；ffprobe 不可用或探测失败返回 None（不抛异常）。
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            file_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode == 0 and stdout:
            duration_seconds = float(stdout.decode().strip())
            duration_ms = int(duration_seconds * 1000)
            logger.debug("Media duration detected: %sms", duration_ms)
            return duration_ms
        logger.warning("Failed to get media duration: %s", file_path)
        return None

    except FileNotFoundError:
        logger.warning(
            "ffprobe is not installed or not in PATH. "
            "Install ffmpeg: https://ffmpeg.org/"
        )
        return None
    except Exception as e:
        logger.warning("Error while probing media duration: %s", e)
        return None


async def convert_audio_format(
    audio_path: str,
    output_format: str = "amr",
    output_path: str | None = None,
) -> str:
    """用 ffmpeg 把音频转换为指定格式（对齐本体 convert_audio_format）。

    支持 amr（电话音质参数）/ogg/opus/wav 等；ffmpeg 不可用或转换失败抛
    Exception（对齐本体语义，调用方应捕获）。
    """
    source_path = Path(audio_path)
    if source_path.suffix.lower() == f".{output_format}" and (
        not source_path.exists() or _get_audio_magic_type(audio_path) == output_format
    ):
        return audio_path

    if output_path is None:
        temp_dir = Path(get_astrbot_temp_path())
        temp_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(
            temp_dir / f"media_audio_{generate_timestamp_id()}.{output_format}"
        )

    args = ["ffmpeg", "-y", "-i", audio_path]
    if output_format == "amr":
        args.extend(
            [
                "-ac",
                "1",
                "-ar",
                "8000",
                "-ab",
                "12.2k",
                "-af",
                (
                    "highpass=f=310:poles=2,"
                    "lowpass=f=3720:poles=2,"
                    "equalizer=f=3150:width_type=h:width=1000:g=7.5,"
                    "loudnorm=I=-18.5:TP=-1.5:LRA=6,"
                    "aresample=8000"
                ),
            ]
        )
    elif output_format == "ogg":
        args.extend(["-acodec", "libopus", "-ac", "1", "-ar", "16000"])
    elif output_format == "opus":
        args.extend(["-acodec", "libopus", "-ac", "1", "-ar", "16000"])
    args.append(output_path)

    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            if output_path and os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError as e:
                    logger.warning(
                        "Failed to remove failed audio output file: %s",
                        e,
                    )
            error_msg = stderr.decode() if stderr else "unknown error"
            raise Exception(f"ffmpeg conversion failed: {error_msg}")
        logger.debug(
            "Audio converted successfully: %s -> %s",
            audio_path,
            output_path,
        )
        return output_path
    except FileNotFoundError:
        raise Exception("ffmpeg not found")


async def convert_audio_to_opus(audio_path: str, output_path: str | None = None) -> str:
    """把音频转换为 Opus 格式（对齐本体 convert_audio_to_opus）。"""
    return await convert_audio_format(
        audio_path=audio_path,
        output_format="opus",
        output_path=output_path,
    )


async def convert_audio_to_amr(audio_path: str, output_path: str | None = None) -> str:
    """把音频转换为 AMR 格式（对齐本体 convert_audio_to_amr）。"""
    return await convert_audio_format(
        audio_path=audio_path,
        output_format="amr",
        output_path=output_path,
    )


async def convert_audio_to_wav(audio_path: str, output_path: str | None = None) -> str:
    """把音频转换为 WAV 格式（对齐本体 convert_audio_to_wav）。"""
    return await convert_audio_format(
        audio_path=audio_path,
        output_format="wav",
        output_path=output_path,
    )


async def convert_video_format(
    video_path: str, output_format: str = "mp4", output_path: str | None = None
) -> str:
    """用 ffmpeg 转换视频格式（对齐本体 convert_video_format）。

    ffmpeg 不可用或转换失败抛 Exception（对齐本体语义）。
    """
    if video_path.lower().endswith(f".{output_format}"):
        return video_path

    if output_path is None:
        temp_dir = get_astrbot_temp_path()
        os.makedirs(temp_dir, exist_ok=True)
        output_path = os.path.join(
            temp_dir,
            f"media_video_{generate_timestamp_id()}.{output_format}",
        )

    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            output_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            if output_path and os.path.exists(output_path):
                try:
                    os.remove(output_path)
                    logger.debug(
                        "Removed failed %s output file: %s",
                        output_format,
                        output_path,
                    )
                except OSError as e:
                    logger.warning(
                        "Failed to remove failed %s output file: %s",
                        output_format,
                        output_path,
                        e,
                    )

            error_msg = stderr.decode() if stderr else "unknown error"
            logger.error("ffmpeg video conversion failed: %s", error_msg)
            raise Exception(f"ffmpeg conversion failed: {error_msg}")

        logger.debug(
            "Video converted successfully: %s -> %s",
            video_path,
            output_path,
        )
        return output_path

    except FileNotFoundError:
        logger.error(
            "ffmpeg is not installed or not in PATH. "
            "Install ffmpeg: https://ffmpeg.org/"
        )
        raise Exception("ffmpeg not found")
    except Exception as e:
        logger.error("Error while converting video format: %s", e)
        raise


async def ensure_wav(audio_path: str, output_path: str | None = None) -> str:
    """确保音频路径指向 WAV（对齐本体 ensure_wav）。

    文件已是 WAV 直接返回；不存在（平台竞态）原样返回以便上层重试；
    tencent silk 优先走 tencent_record_helper（SDK 未内置时降级为 ffmpeg
    直接转码并告警）；其余格式经 ffmpeg 转 WAV（ffmpeg 缺失抛异常）。
    """
    if not audio_path:
        return audio_path

    if not os.path.exists(audio_path):
        # 文件尚不可用（如 napcat 竞态）：原样返回，交由上层重试逻辑处理
        return audio_path

    audio_type = _get_audio_magic_type(audio_path)
    if audio_type == "wav":
        return audio_path

    if audio_type == "silk":
        if output_path is None:
            output_path = str(_temp_media_path("audio", ".wav"))
        try:
            from astrbot.core.utils.tencent_record_helper import tencent_silk_to_wav

            return await tencent_silk_to_wav(audio_path, output_path)
        except ImportError:
            logger.warning(
                "tencent_record_helper 不可用，silk 音频降级为 ffmpeg 直接转 WAV"
            )

    return await convert_audio_to_wav(audio_path, output_path)


async def ensure_jpeg(image_path: str, output_path: str | None = None) -> str:
    """确保图片为 JPEG 兼容的静态图（对齐本体 ensure_jpeg，纯 PIL 实现）。

    已是 .jpg/.jpeg 后缀的 JPEG、带透明通道或动图原样返回；其余静态图
    转 JPEG（Pillow 打开失败原样抛出）。空路径/文件不存在在对齐本体的
    顺序下原样返回（先判定、再导入 PIL）。
    """
    if not image_path:
        return image_path

    source_path = Path(image_path)
    if not source_path.exists():
        return image_path

    from PIL import Image as PILImage

    with PILImage.open(source_path) as opened_img:
        image_format = str(opened_img.format or "").upper()
        image_has_alpha = opened_img.mode in {"RGBA", "LA"} or (
            opened_img.mode == "P" and "transparency" in opened_img.info
        )
        image_is_animated = (
            getattr(opened_img, "is_animated", False)
            or getattr(
                opened_img,
                "n_frames",
                1,
            )
            > 1
        )

    if image_format == "JPEG" and source_path.suffix.lower() in {".jpg", ".jpeg"}:
        return image_path

    if image_has_alpha or image_is_animated:
        return image_path

    if output_path is None:
        temp_dir = Path(get_astrbot_temp_path())
        temp_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(temp_dir / f"media_image_{generate_timestamp_id()}.jpg")
    jpeg_output_path = output_path

    try:
        if image_format == "JPEG":
            await asyncio.to_thread(shutil.copyfile, source_path, jpeg_output_path)
            return jpeg_output_path
    except Exception:
        if output_path and os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError as e:
                logger.warning(
                    "Failed to remove failed image output file: %s",
                    e,
                )
        raise

    def convert_image_to_jpeg() -> str:
        converted_img: PILImage.Image | None = None

        with PILImage.open(image_path) as opened_img:
            try:
                working_img: PILImage.Image = opened_img
                if opened_img.mode != "RGB":
                    converted_img = opened_img.convert("RGB")
                    working_img = converted_img

                working_img.save(
                    jpeg_output_path,
                    "JPEG",
                    quality=IMAGE_COMPRESS_DEFAULT_QUALITY,
                    subsampling=0,
                )
                return jpeg_output_path
            finally:
                if converted_img is not None:
                    converted_img.close()

    try:
        return await asyncio.to_thread(convert_image_to_jpeg)
    except Exception:
        if output_path and os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError as e:
                logger.warning(
                    "Failed to remove failed image output file: %s",
                    e,
                )
        raise


async def extract_video_cover(
    video_path: str,
    output_path: str | None = None,
) -> str:
    """从视频抽取 JPEG 封面帧（对齐本体 extract_video_cover）。

    ffmpeg 不可用或抽取失败抛 Exception（对齐本体语义）。
    """
    if output_path is None:
        temp_dir = Path(get_astrbot_temp_path())
        temp_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(temp_dir / f"media_cover_{generate_timestamp_id()}.jpg")

    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-ss",
            "00:00:00",
            "-frames:v",
            "1",
            output_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            if output_path and os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError as e:
                    logger.warning(
                        "Failed to remove failed video cover file: %s",
                        e,
                    )
            error_msg = stderr.decode() if stderr else "unknown error"
            raise Exception(f"ffmpeg extract cover failed: {error_msg}")
        return output_path
    except FileNotFoundError:
        raise Exception("ffmpeg not found")


def _compress_image_sync(
    source: bytes | Path,
    temp_dir: Path,
    max_size: int,
    quality: int,
    optimize: bool,
) -> str | None:
    """同步图片压缩（经 asyncio.to_thread 调用，对齐本体 _compress_image_sync）。

    Returns:
        压缩后的图片路径；动图/无需压缩返回 None（保留原图）。
    """
    from PIL import Image as PILImage

    fp = io.BytesIO(source) if isinstance(source, bytes) else source
    with PILImage.open(fp) as opened_img:
        converted_img: PILImage.Image | None = None

        try:
            if (
                getattr(opened_img, "is_animated", False)
                or getattr(opened_img, "n_frames", 1) > 1
            ):
                return None

            working_img = opened_img
            image_has_alpha = opened_img.mode in {"RGBA", "LA"} or (
                opened_img.mode == "P" and "transparency" in opened_img.info
            )
            output_format = "PNG" if image_has_alpha else "JPEG"
            output_suffix = ".png" if image_has_alpha else ".jpg"

            if image_has_alpha and opened_img.mode != "RGBA":
                converted_img = opened_img.convert("RGBA")
                working_img = converted_img
            elif not image_has_alpha and opened_img.mode != "RGB":
                converted_img = opened_img.convert("RGB")
                working_img = converted_img
            assert working_img is not None

            if max(working_img.size) > max_size:
                working_img.thumbnail((max_size, max_size), PILImage.Resampling.LANCZOS)

            save_path = (
                temp_dir / f"compressed_{generate_timestamp_id()}{output_suffix}"
            )
            save_kwargs: dict[str, int | bool] = {"optimize": optimize}
            if output_format == "JPEG":
                save_kwargs["quality"] = quality
            working_img.save(save_path, output_format, **save_kwargs)
            logger.debug(f"Image compressed successfully: {save_path}")
            return str(save_path)
        finally:
            if converted_img is not None:
                converted_img.close()


async def compress_image(
    url_or_path: str,
    max_size: int = IMAGE_COMPRESS_DEFAULT_MAX_SIZE,
    quality: int = IMAGE_COMPRESS_DEFAULT_QUALITY,
) -> str:
    """压缩较大的用户上传图片（对齐本体 compress_image，纯 PIL 实现）。

    远程 URL 原样返回；小于阈值且尺寸不超限的原图原样返回；压缩失败
    或动图返回原路径（不抛异常）。
    """
    def _exceeds_max_size(source: bytes | Path) -> bool:
        try:
            from PIL import Image as PILImage

            fp = io.BytesIO(source) if isinstance(source, bytes) else source
            with PILImage.open(fp) as opened_img:
                return max(opened_img.size) > max_size
        except Exception:  # noqa: BLE001
            return False

    max_size = max(int(max_size), 1)
    quality = min(max(int(quality), 1), 100)
    optimize = IMAGE_COMPRESS_DEFAULT_OPTIMIZE
    min_file_size_bytes = int(IMAGE_COMPRESS_DEFAULT_MIN_FILE_SIZE_MB * 1024 * 1024)
    image_source: bytes | Path | None = None

    # 远程图片跳过压缩，原样返回
    if url_or_path.startswith("http"):
        return url_or_path
    elif url_or_path.startswith("data:image"):
        _header, encoded = url_or_path.split(",", 1)
        image_source = _decode_base64_media_payload(
            encoded,
            error_message="invalid image data URI payload",
        )
        if len(image_source) < min_file_size_bytes and not _exceeds_max_size(
            image_source
        ):
            return url_or_path
    else:
        local_path = Path(url_or_path)
        if not local_path.exists():
            return url_or_path
        if local_path.stat().st_size < min_file_size_bytes and not _exceeds_max_size(
            local_path
        ):
            return url_or_path
        image_source = local_path

    if image_source is None:
        return url_or_path

    temp_dir = Path(get_astrbot_temp_path())
    temp_dir.mkdir(parents=True, exist_ok=True)

    # 阻塞的图片处理放入线程执行
    compressed_path = await asyncio.to_thread(
        _compress_image_sync,
        image_source,
        temp_dir,
        max_size,
        quality,
        optimize,
    )
    return compressed_path or url_or_path


def _decode_base64_media_payload(
    payload: str,
    *,
    error_message: str,
    validate: bool = False,
) -> bytes:
    """解码 base64 负载并容忍缺失 padding（对齐本体 _decode_base64_payload）。"""
    payload = "".join(payload.split())
    missing_padding = len(payload) % 4
    if missing_padding:
        payload += "=" * (4 - missing_padding)

    try:
        return base64.b64decode(payload, validate=validate)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(error_message) from exc
