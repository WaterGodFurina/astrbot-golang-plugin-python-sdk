"""AstrBot 消息组件（Go 宿主兼容运行时）。

与 Python 本体 `astrbot.core.message.components` API 对齐（普通类实现，
不依赖 pydantic）。插件代码在 Python 本体与 Go 宿主子进程中均可运行。
"""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import enum
import json
import logging
import os
import shutil
import tempfile
import urllib.request
import uuid
from pathlib import Path, PurePosixPath

from astrbot.core.file_token_service import file_token_service  # noqa: F401（re-export）
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path  # noqa: F401（re-export）

logger = logging.getLogger("astrbot")


async def download_file(url: str, save_path: str | None = None) -> str:
    """下载 url 到本地文件（Go 宿主兼容运行时）。

    对齐原版 `astrbot.core.utils.io.download_file` 的调用习惯：
    - 未指定 save_path 时下载到系统临时目录，返回临时文件路径；
    - 下载失败不抛异常，返回空字符串（插件可据此判断失败）。
    """
    try:
        tmp = await asyncio.to_thread(_download_to_temp, url)
    except Exception as e:
        logger.warning(f"download_file 失败: {url!r} ({e})")
        return ""
    if save_path is None:
        return tmp
    os.makedirs(os.path.dirname(os.path.abspath(save_path)) or ".", exist_ok=True)
    shutil.move(tmp, save_path)
    return save_path


class ComponentType(str, enum.Enum):
    # Basic Segment Types
    Plain = "Plain"  # plain text message
    Image = "Image"  # image
    Record = "Record"  # audio
    Video = "Video"  # video
    File = "File"  # file attachment

    # IM-specific Segment Types
    Face = "Face"  # Emoji segment for Tencent QQ platform
    At = "At"  # mention a user in IM apps
    Node = "Node"  # a node in a forwarded message
    Nodes = "Nodes"  # a forwarded message consisting of multiple nodes
    Poke = "Poke"  # a poke message for Tencent QQ platform
    Reply = "Reply"  # a reply message segment
    Forward = "Forward"  # a forwarded message segment
    RPS = "RPS"  # TODO
    Dice = "Dice"  # TODO
    Shake = "Shake"  # TODO
    Share = "Share"
    Contact = "Contact"  # TODO
    Location = "Location"  # TODO
    Music = "Music"
    Json = "Json"
    Unknown = "Unknown"


def is_file_uri(s: str) -> bool:
    return isinstance(s, str) and s.startswith("file://")


def file_uri_to_path(uri: str) -> str:
    from urllib.parse import unquote, urlparse

    parsed = urlparse(uri)
    path = unquote(parsed.path)
    if parsed.netloc and parsed.netloc not in ("", "localhost"):
        path = f"//{parsed.netloc}{path}"
    return path


def _download_to_temp(url: str, suffix: str = "") -> str:
    """把 http(s) 媒体下载到临时文件，返回本地路径。

    分块流式写入（64KB/块），避免整段响应一次性载入内存（大文件/慢
    链接会显著膨胀内存占用）。另：显式构建 ProxyHandler opener 读取
    环境代理（http_proxy/https_proxy/all_proxy 等），与 Python 本体
    aiohttp trust_env=True 语义对齐——urllib 默认未必走系统代理。
    """
    os.makedirs(tempfile.gettempdir(), exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.close()
    # 显式带 ProxyHandler 的 opener：走环境变量代理；无代理环境等同默认行为。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler())
    try:
        with opener.open(url, timeout=30) as resp, open(tmp.name, "wb") as f:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        return tmp.name
    except Exception:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
        raise


_DOWNLOAD_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


def _download_sync(url: str, suffix: str = "") -> str:
    """同步下载封装：事件循环运行时切到线程池执行，避免
    opener.open(timeout=30) 冻结事件循环；无运行中循环时直接下载。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _download_to_temp(url, suffix)
    import concurrent.futures

    logger.warning(
        "File.file 在事件循环内触发了同步下载（最长阻塞 30s），"
        "请改用 await file.get_file()"
    )
    global _DOWNLOAD_EXECUTOR
    if _DOWNLOAD_EXECUTOR is None:
        _DOWNLOAD_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, name_prefix="file-download"
        )
    return _DOWNLOAD_EXECUTOR.submit(_download_to_temp, url, suffix).result()


class MediaResolver:
    """简化版媒体解析：http(s) 下载到临时目录，本地路径原样返回。"""

    def __init__(self, source: str, media_type: str = "", default_suffix: str = ""):
        self.source = source
        self.media_type = media_type
        self.default_suffix = default_suffix

    async def to_path(self, target_format: str | None = None) -> str:
        source = self.source
        if source.startswith("base64://"):
            suffix = f".{target_format}" if target_format else self.default_suffix or ".bin"
            return self._write_base64(source[len("base64://"):], suffix)
        if source.startswith("data:"):
            import re

            m = re.match(r"data:[^;]+;base64,(.*)", source, re.S)
            if m:
                suffix = f".{target_format}" if target_format else ".bin"
                return self._write_base64(m.group(1), suffix)
            # 非 base64 的 data: URI 无法落盘：显式报错，避免下游拿到
            # URI 字符串当文件路径用，在 open() 处才崩溃。
            raise ValueError(
                f"不支持的 data: URI（仅支持 data:*;base64,... 编码）: {source[:64]!r}"
            )
        if source.startswith("http://") or source.startswith("https://"):
            suffix = self.default_suffix or ".bin"
            # http(s) 源下载在子线程执行，避免 opener.open(timeout=30) 阻塞事件循环
            return await asyncio.to_thread(_download_to_temp, source, suffix)
        if is_file_uri(source):
            return file_uri_to_path(source)
        if os.path.exists(source):
            return source
        # 不存在的本地路径原样返回会导致下游 open() 抛 FileNotFoundError，
        # 错误离根因很远：此处显式报错。
        raise ValueError(f"媒体源既非 URL/URI 也不是存在的本地文件: {source!r}")

    async def to_base64(self, target_format: str | None = None) -> str:
        path = await self.to_path(target_format)
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()

    @staticmethod
    def _write_base64(b64: str, suffix: str) -> str:
        import binascii

        try:
            data = base64.b64decode(b64)
        except binascii.Error:
            data = b64.encode()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(data)
        tmp.close()
        return tmp.name


class BaseMessageComponent:
    type: ComponentType = ComponentType.Unknown

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __repr__(self):
        parts = []
        for k, v in self.__dict__.items():
            if v is None:
                continue
            text = str(v)
            if len(text) > 64:
                text = f"{text[:64]}...<{len(text)} chars>"
            parts.append(f"{k}={text!r}")
        return f"{self.__class__.__name__}({', '.join(parts)})"

    def toDict(self):
        data = {}
        for k, v in self.__dict__.items():
            if k == "type" or v is None:
                continue
            if k == "_type":
                k = "type"
            if isinstance(v, BaseMessageComponent):
                v = v.toDict()
            elif isinstance(v, list):
                v = [i.toDict() if isinstance(i, BaseMessageComponent) else i for i in v]
            data[k] = v
        return {"type": self.type.lower(), "data": data}

    async def to_dict(self) -> dict:
        return self.toDict()


class Plain(BaseMessageComponent):
    type: ComponentType = ComponentType.Plain
    text: str

    def __init__(self, text: str, convert: bool = True, **_) -> None:
        super().__init__(text=text)

    def toDict(self) -> dict:
        return {"type": "text", "data": {"text": self.text}}

    async def to_dict(self) -> dict:
        return {"type": "text", "data": {"text": self.text}}


class Face(BaseMessageComponent):
    type: ComponentType = ComponentType.Face
    id: int
    # 兼容字段：部分调用方以 face_id 访问表情 ID
    face_id: int | None = None

    def __init__(self, **_) -> None:
        super().__init__(**_)
        # face_id 缺省时与 id 保持一致，保证两处取值一致
        if self.face_id is None and getattr(self, "id", None) is not None:
            self.face_id = self.id


class Record(BaseMessageComponent):
    type: ComponentType = ComponentType.Record
    file: str | None = ""
    url: str | None = ""
    text: str | None = None
    path: str | None = None

    def __init__(self, file: str | None, **_) -> None:
        super().__init__(file=file, **_)

    @staticmethod
    def fromFileSystem(path, **_):
        if isinstance(path, str) and (
            path.startswith("data:") or path.startswith("base64://")
        ):
            # data URI / base64 内容是媒体数据而非文件系统路径：直接承载，
            # 不做 Path.resolve（会把 base64 中的 "//" 规范化破坏数据）。
            return Record(file=path, **_)
        file_path = Path(path).resolve(strict=False)
        return Record(file=file_path.as_uri(), path=str(file_path), **_)

    @staticmethod
    def fromURL(url: str, **_):
        if url.startswith("http://") or url.startswith("https://"):
            return Record(file=url, **_)
        raise Exception("not a valid url")

    @staticmethod
    def fromBase64(bs64_data: str, **_):
        return Record(file=f"base64://{bs64_data}", **_)

    @staticmethod
    def _decode_file_uri(uri: str) -> str:
        """解码 file:/// URI 为本地文件路径（兼容字段：别名 file_uri_to_path）。"""
        return file_uri_to_path(uri)

    async def _resolve_file_source(self) -> str:
        """选择可用的文件源：file → url → path 三级回退，附带存在性探测。

        NapCat 在 Windows 上可能只给 file 字段一个裸文件名（如 0d2bb1468a87d64414f8e563cc61c33c.amr），
        而真实路径在 url（如 file:///C:/Users/...）或 path（如 C:\\Users\\...）中，
        Image.convert_to_file_path 使用 self.url or self.file，Record 同样需要回退。
        """
        # 1) 优先尝试 file：已包含完整 URI/已知格式或本地存在时直接使用
        if self.file:
            file_exists = False
            try:
                file_exists = os.path.exists(self.file)
            except OSError:
                pass
            if (
                is_file_uri(self.file)
                or self.file.startswith("http")
                or self.file.startswith("base64://")
                or self.file.startswith("data:")
                or file_exists
            ):
                return self.file

        # 2) 尝试 url（可能是 file:///、http 链接或本地路径）
        if self.url:
            url_exists = False
            decoded_url_exists = False
            try:
                url_exists = os.path.exists(self.url)
            except OSError:
                pass
            if is_file_uri(self.url):
                try:
                    decoded_url_exists = os.path.exists(file_uri_to_path(self.url))
                except OSError:
                    pass
            if (
                is_file_uri(self.url)
                or self.url.startswith("http")
                or self.url.startswith("data:")
                or url_exists
                or decoded_url_exists
            ):
                return self.url

        # 3) 尝试 path（可能是 Windows 绝对路径如 C:\Users\...）
        if self.path:
            try:
                if os.path.exists(self.path):
                    return self.path
            except OSError:
                pass

        # 4) 最后裸返回 file，即使不可用也要让调用方看到原始内容
        return self.file or self.url or ""

    async def convert_to_file_path(self) -> str:
        """将语音统一转换为本地文件路径（网络 URL 会自动下载）。"""
        file_source = await self._resolve_file_source()
        if not file_source:
            raise ValueError("No valid file or URL provided")
        return await MediaResolver(file_source, media_type="audio", default_suffix=".wav").to_path()

    async def convert_to_base64(self) -> str:
        """将语音统一转换为 base64 编码（不以 base64:// 或 data: 开头）。"""
        file_source = await self._resolve_file_source()
        if not file_source:
            raise ValueError("No valid file or URL provided")
        return await MediaResolver(file_source, media_type="audio", default_suffix=".wav").to_base64()

    async def register_to_file_service(self) -> str:
        """将语音注册到文件服务。

        Go 宿主运行时无文件服务基础设施，降级为直接返回原 url（或本地文件路径），不抛异常。
        """
        if self.url:
            return self.url
        try:
            return await self.convert_to_file_path()
        except Exception:
            return self.file or ""


class Video(BaseMessageComponent):
    type: ComponentType = ComponentType.Video
    file: str
    url: str | None = ""
    cover: str | None = ""
    path: str | None = ""

    def __init__(self, file: str, **_) -> None:
        super().__init__(file=file, **_)

    @staticmethod
    def fromFileSystem(path, **_):
        if isinstance(path, str) and (
            path.startswith("data:") or path.startswith("base64://")
        ):
            # data URI / base64 内容是媒体数据而非文件系统路径：直接承载，
            # 不做 Path.resolve（会把 base64 中的 "//" 规范化破坏数据）。
            return Video(file=path, **_)
        file_path = Path(path).resolve(strict=False)
        return Video(file=file_path.as_uri(), path=str(file_path), **_)

    @staticmethod
    def fromURL(url: str, **_):
        if url.startswith("http://") or url.startswith("https://"):
            return Video(file=url, **_)
        raise Exception("not a valid url")

    @staticmethod
    def fromBase64(base64_data: str, **_):
        return Video(file=f"base64://{base64_data}", **_)

    async def _resolve_file_source(self) -> str:
        """选择可用的文件源：file → url → path 三级回退，附带存在性探测。"""
        for candidate in (self.file, self.url):
            if not candidate:
                continue
            candidate_exists = False
            try:
                candidate_exists = os.path.exists(candidate)
            except OSError:
                pass
            if (
                is_file_uri(candidate)
                or candidate.startswith("http")
                or candidate.startswith("base64://")
                or candidate.startswith("data:")
                or candidate_exists
            ):
                return candidate

        if self.path:
            try:
                if os.path.exists(self.path):
                    return self.path
            except OSError:
                pass

        return self.file or self.url or ""

    async def convert_to_file_path(self) -> str:
        """将视频统一转换为本地文件路径（网络 URL 会自动下载）。"""
        file_source = await self._resolve_file_source()
        if not file_source:
            raise ValueError("No valid file or URL provided")

        if is_file_uri(file_source):
            return file_uri_to_path(file_source)
        if file_source.startswith(("http://", "https://", "base64://", "data:")):
            return await MediaResolver(file_source, media_type="video", default_suffix=".mp4").to_path()
        try:
            if os.path.exists(file_source):
                return os.path.abspath(file_source)
        except OSError:
            pass
        raise ValueError(f"not a valid file: {file_source}")

    async def convert_to_base64(self) -> str:
        """将视频统一转换为 base64 编码（不以 base64:// 或 data: 开头）。"""
        file_source = await self._resolve_file_source()
        if not file_source:
            raise ValueError("No valid file or URL provided")
        return await MediaResolver(file_source, media_type="video", default_suffix=".mp4").to_base64()

    async def register_to_file_service(self) -> str:
        """将视频注册到文件服务。

        Go 宿主运行时无文件服务基础设施，降级为直接返回原 url（或本地文件路径），不抛异常。
        """
        if self.url:
            return self.url
        try:
            return await self.convert_to_file_path()
        except Exception:
            return self.file or ""

    async def to_dict(self):
        """需要和 toDict 区分开，toDict 是同步方法。

        Go 宿主运行时无文件服务，降级为兼容实现：http 源直接透传，其余原样返回 file 字段。
        """
        return {
            "type": "video",
            "data": {
                "file": self.file or "",
            },
        }


class At(BaseMessageComponent):
    type: ComponentType = ComponentType.At
    qq: int | str
    name: str | None = ""

    def __init__(self, **_) -> None:
        super().__init__(**_)

    def toDict(self):
        return {"type": "at", "data": {"qq": str(self.qq)}}


class AtAll(At):
    qq: str = "all"

    def __init__(self, **_) -> None:
        super().__init__(**_)


class RPS(BaseMessageComponent):
    type: ComponentType = ComponentType.RPS

    def __init__(self, **_) -> None:
        super().__init__(**_)


class Dice(BaseMessageComponent):
    type: ComponentType = ComponentType.Dice

    def __init__(self, **_) -> None:
        super().__init__(**_)


class Shake(BaseMessageComponent):
    type: ComponentType = ComponentType.Shake

    def __init__(self, **_) -> None:
        super().__init__(**_)


class Share(BaseMessageComponent):
    type: ComponentType = ComponentType.Share
    url: str
    title: str
    content: str | None = ""
    image: str | None = ""

    def __init__(self, **_) -> None:
        super().__init__(**_)


class Contact(BaseMessageComponent):
    type: ComponentType = ComponentType.Contact
    _type: str
    id: int | None = 0

    def __init__(self, **_) -> None:
        super().__init__(**_)


class Location(BaseMessageComponent):
    type: ComponentType = ComponentType.Location
    lat: float
    lon: float
    title: str | None = ""
    content: str | None = ""

    def __init__(self, **_) -> None:
        super().__init__(**_)


class Music(BaseMessageComponent):
    type: ComponentType = ComponentType.Music
    _type: str
    id: int | None = 0
    url: str | None = ""
    audio: str | None = ""
    title: str | None = ""
    content: str | None = ""
    image: str | None = ""

    def __init__(self, **_) -> None:
        super().__init__(**_)


class Image(BaseMessageComponent):
    type: ComponentType = ComponentType.Image
    file: str | None = ""
    _type: str | None = ""
    url: str | None = ""
    path: str | None = ""

    def __init__(self, file: str | None = "", **_) -> None:
        super().__init__(file=file, **_)

    @staticmethod
    def fromURL(url: str, **_):
        if url.startswith("http://") or url.startswith("https://"):
            return Image(file=url, **_)
        raise Exception("not a valid url")

    @staticmethod
    def fromFileSystem(path, **_):
        if isinstance(path, str) and (
            path.startswith("data:") or path.startswith("base64://")
        ):
            # data URI / base64 内容是媒体数据而非文件系统路径：直接承载，
            # 不做 Path.resolve——resolve 会把 base64 中的 "//" 当作路径分隔
            # 符规范化（// → /），破坏媒体数据（帮助图片等长图必现）。
            return Image(file=path, **_)
        file_path = Path(path).resolve(strict=False)
        return Image(file=file_path.as_uri(), path=str(file_path), **_)

    @staticmethod
    def fromBase64(base64: str, **_):
        return Image(f"base64://{base64}", **_)

    @staticmethod
    def fromBytes(byte: bytes):
        return Image.fromBase64(base64.b64encode(byte).decode())

    @staticmethod
    def fromIO(IO):
        return Image.fromBytes(IO.read())

    async def convert_to_file_path(self) -> str:
        """将图片统一转换为本地文件路径（网络 URL 会自动下载）。"""
        url = self.url or self.file
        if not url:
            raise ValueError("No valid file or URL provided")
        return await MediaResolver(url, media_type="image").to_path()

    async def convert_to_base64(self) -> str:
        """将图片统一转换为 base64 编码（不以 base64:// 或 data: 开头）。"""
        url = self.url or self.file
        if not url:
            raise ValueError("No valid file or URL provided")
        return await MediaResolver(url, media_type="image").to_base64()

    async def register_to_file_service(self) -> str:
        """将图片注册到文件服务。

        Go 宿主运行时无文件服务基础设施，降级为：有 url 直接返回 url，否则返回本地文件路径，不抛异常。
        """
        if self.url:
            return self.url
        try:
            return await self.convert_to_file_path()
        except Exception:
            return self.file or ""


class Reply(BaseMessageComponent):
    type: ComponentType = ComponentType.Reply
    id: str | int
    # 注意：类级可变默认值会被所有实例共享——__init__ 里必须为每个实例
    # 重建 chain（引用 BaseMessageComponent.__init__ 的默认值语义）。
    chain: list["BaseMessageComponent"] | None = None
    sender_id: int | None | str = 0
    sender_nickname: str | None = ""
    time: int | None = 0
    message_str: str | None = ""
    text: str | None = ""
    qq: int | None = 0
    seq: int | None = 0

    def __init__(self, **_) -> None:
        super().__init__(**_)
        # 实例级 chain：避免共享类属性 list 导致跨实例数据串扰
        if self.chain is None:
            self.chain = []

    def toDict(self):
        return {"type": "reply", "data": {"id": str(self.id)}}


class Poke(BaseMessageComponent):
    type: ComponentType = ComponentType.Poke
    _type: str | int = "126"
    id: int | str | None = 0
    qq: int | str | None = 0

    def __init__(self, poke_type: str | int | None = None, **_) -> None:
        legacy_type = _.pop("type", None)
        if poke_type is None:
            poke_type = legacy_type
        if poke_type in (None, "", "poke", "Poke"):
            poke_type = "126"
        super().__init__(_type=str(poke_type), **_)

    def target_id(self) -> str | None:
        for value in (self.id, self.qq):
            if value is None:
                continue
            text = str(value).strip()
            if text and text != "0":
                return text
        return None

    def toDict(self):
        target_id = self.target_id()
        data = {"type": str(self._type or "126")}
        if target_id:
            data["id"] = target_id
        return {"type": "poke", "data": data}


class Forward(BaseMessageComponent):
    type: ComponentType = ComponentType.Forward
    id: str
    # 兼容字段：部分调用方以 forward 字段承载转发消息 ID
    forward: str | None = None

    def __init__(self, **_) -> None:
        super().__init__(**_)
        # forward 缺省时与 id 保持一致
        if self.forward is None and getattr(self, "id", None) is not None:
            self.forward = self.id


class Node(BaseMessageComponent):
    type: ComponentType = ComponentType.Node
    id: int | None = 0
    name: str | None = ""
    uin: str | None = "0"
    content: list["BaseMessageComponent"] = []
    seq: str | list | None = ""
    time: int | None = 0

    def __init__(self, content: list["BaseMessageComponent"], **_) -> None:
        if isinstance(content, Node):
            content = [content]
        super().__init__(content=content, **_)

    async def to_dict(self):
        data_content = []
        for comp in self.content:
            if isinstance(comp, (Image, Record)):
                try:
                    bs64 = await comp.convert_to_base64()
                    data_content.append({"type": comp.type.lower(), "data": {"file": f"base64://{bs64}"}})
                except Exception:
                    data_content.append(comp.toDict())
            elif isinstance(comp, (Plain, File, Node, Nodes)):
                data_content.append(await comp.to_dict())
            else:
                data_content.append(comp.toDict())
        return {"type": "node", "data": {"user_id": str(self.uin), "nickname": self.name, "content": data_content}}


class Nodes(BaseMessageComponent):
    type: ComponentType = ComponentType.Nodes
    nodes: list[Node]

    def __init__(self, nodes: list[Node], **_) -> None:
        super().__init__(nodes=nodes, **_)

    def toDict(self):
        return {"messages": [{"type": "node", "data": {"user_id": str(n.uin), "nickname": n.name, "content": [c.toDict() for c in n.content]}} for n in self.nodes]}

    async def to_dict(self) -> dict:
        return {"messages": [await n.to_dict() for n in self.nodes]}


class Json(BaseMessageComponent):
    type: ComponentType = ComponentType.Json
    data: dict

    def __init__(self, data: str | dict, **_) -> None:
        if isinstance(data, str):
            data = json.loads(data)
        super().__init__(data=data, **_)


class Unknown(BaseMessageComponent):
    type: ComponentType = ComponentType.Unknown
    text: str


def _sanitize_file_component_name(name: str | None) -> str:
    if not name:
        return "file"
    normalized = str(name).replace("\\", "/")
    basename = PurePosixPath(normalized).name.replace("\x00", "").strip()
    for char in ':*?"<>|':
        basename = basename.replace(char, "_")
    if basename in {"", ".", ".."}:
        return "file"
    return basename


class File(BaseMessageComponent):
    type: ComponentType = ComponentType.File
    name: str | None = ""
    file_: str | None = ""
    url: str | None = ""

    def __init__(self, name: str, file: str = "", url: str = "") -> None:
        super().__init__(name=name, file_=file, url=url)
        self._file_cache: str | None = None

    @property
    def file(self) -> str:
        # 远程文件下载结果缓存到实例属性：循环里多次访问 f.file 不会
        # 重复下载、也不会遗留多个临时文件。
        if self._file_cache is not None:
            return self._file_cache
        if self.file_:
            path = file_uri_to_path(self.file_) if is_file_uri(self.file_) else self.file_
            if os.path.exists(path):
                self._file_cache = os.path.abspath(path)
                return self._file_cache
            if self.file_.startswith("http"):
                self._file_cache = _download_sync(self.file_)
                return self._file_cache
            self._file_cache = os.path.abspath(path)
            return self._file_cache
        if self.url and (self.url.startswith("http://") or self.url.startswith("https://")):
            self._file_cache = _download_sync(self.url)
            return self._file_cache
        return self.file_ or self.url or ""

    @file.setter
    def file(self, value: str) -> None:
        """设置 file 属性，向前兼容：传入 http(s) 链接时写入 url，否则写入 file_。"""
        self._file_cache = None
        if value.startswith("http://") or value.startswith("https://"):
            self.url = value
        else:
            self.file_ = value

    async def get_file(self, allow_return_url: bool = False) -> str:
        """异步获取文件。请注意在使用后清理下载的文件，以免占用过多空间。

        Args:
            allow_return_url: 是否允许以 http 下载链接的形式返回，这允许您自行控制是否需要下载文件。
            注意，如果为 True，也可能返回文件路径。
        Returns:
            str: 文件路径或者 http 下载链接

        """
        if allow_return_url and self.url:
            return self.url

        if self.file_:
            path = file_uri_to_path(self.file_) if is_file_uri(self.file_) else self.file_
            if os.path.exists(path):
                return os.path.abspath(path)

        if self.url:
            if is_file_uri(self.url):
                return file_uri_to_path(self.url)
            if self.url.startswith("http://") or self.url.startswith("https://"):
                self.file_ = await asyncio.to_thread(_download_to_temp, self.url)
                return os.path.abspath(self.file_)

        return self.file_ or self.url or ""

    async def register_to_file_service(self) -> str:
        """将文件注册到文件服务。

        Go 宿主运行时无文件服务基础设施，降级为：有 url 直接返回 url，否则返回本地文件路径，不抛异常。
        """
        if self.url:
            return self.url
        try:
            return await self.get_file()
        except Exception:
            return self.file_ or ""

    async def _download_file(self) -> None:
        """下载文件到 AstrBot 临时目录，并更新 self.file_。

        对齐原版 File._download_file 语义：文件名含来源文件名与随机后缀，
        经模块级 download_file 下载（失败时 download_file 返回空串）。
        """
        if not self.url:
            raise ValueError("Download failed: No URL provided in File component.")
        download_dir = Path(get_astrbot_temp_path())
        download_dir.mkdir(parents=True, exist_ok=True)
        if self.name:
            safe_name = _sanitize_file_component_name(self.name)
            name = Path(safe_name).stem
            ext = Path(safe_name).suffix
            filename = f"fileseg_{name}_{uuid.uuid4().hex[:8]}{ext}"
        else:
            filename = f"fileseg_{uuid.uuid4().hex}"
        file_path = download_dir / filename
        await download_file(self.url, str(file_path))
        self.file_ = str(file_path.resolve())

    def toDict(self):
        data = {"name": self.name or "file"}
        if self.file_:
            data["file"] = self.file_
        if self.url:
            data["url"] = self.url
        return {"type": "file", "data": data}

    async def to_dict(self) -> dict:
        return self.toDict()


# 组件类型注册表：键为 OneBot 段类型小写名，值为对应组件类。
# 与 Python 本体 components.py 模块级 dict 对齐。
ComponentTypes = {
    # Basic Message Segments
    "plain": Plain,
    "text": Plain,
    "image": Image,
    "record": Record,
    "video": Video,
    "file": File,
    # IM-specific Message Segments
    "face": Face,
    "at": At,
    "rps": RPS,
    "dice": Dice,
    "shake": Shake,
    "share": Share,
    "contact": Contact,
    "location": Location,
    "music": Music,
    "reply": Reply,
    "poke": Poke,
    "forward": Forward,
    "node": Node,
    "nodes": Nodes,
    "json": Json,
    "unknown": Unknown,
}
