"""通用 IO 工具（Go 宿主兼容运行时，对齐本体 utils.io 公开面）。

提供 remove_dir / ensure_dir / download_file / save_temp_img /
download_image_by_url / file_to_base64 / get_local_ip_addresses /
port_checker 与 DownloadFileHTTPError 异常类，签名与语义对齐本体。
"""
import asyncio
import logging
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger("astrbot")


def _safe_url_for_log(url: str) -> str:
    """返回省略查询串与片段的 URL 摘要（对齐本体 _safe_url_for_log）。"""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"}:
        filename = Path(unquote(parsed.path or "")).name
        suffix = f" file={filename!r}" if filename else ""
        return f"{parsed.scheme} URL host={parsed.netloc!r}{suffix} len={len(url)}"
    return f"URL len={len(url)}"


class DownloadFileHTTPError(RuntimeError):
    """下载返回非 200 HTTP 状态时抛出（对齐本体 DownloadFileHTTPError）。"""


def on_error(func, path, exc_info) -> None:
    """shutil.rmtree 的 onerror 回调：只读目录/文件先 chmod 再重试。"""
    import stat

    if not os.access(path, os.W_OK):
        try:
            os.chmod(path, stat.S_IWUSR)
        except OSError:
            pass
        func(path)
    else:
        raise exc_info[1]


def remove_dir(file_path: str) -> bool:
    """递归删除目录/文件/软链（不存在视为成功）。"""
    if not os.path.lexists(file_path):
        return True
    if os.path.isfile(file_path) or os.path.islink(file_path):
        os.remove(file_path)
    else:
        shutil.rmtree(file_path, onerror=on_error)
    return True


def ensure_dir(dir_path) -> None:
    """确保目录存在（对齐本体 ensure_dir）。

    路径处存在非目录文件或损坏符号链接时，先删除再递归创建；失败抛
    RuntimeError。
    """
    p = Path(dir_path)
    if (p.exists() or p.is_symlink()) and not p.is_dir():
        logger.warning(
            f"Path {p} exists but is not a directory; removing it before creating "
            "the directory."
        )
        try:
            if p.is_dir():
                shutil.rmtree(p, onerror=on_error)
            else:
                p.unlink()
        except Exception as e:
            logger.error(f"Failed to remove conflicting path {p}: {e!s}")
            raise RuntimeError(f"Could not remove conflicting path {p}: {e!s}") from e

    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Failed to create directory {p}: {e!s}")
        raise RuntimeError(f"Could not create directory {p}: {e!s}") from e


def _download_to_temp(url: str, suffix: str = "", ssl_context=None) -> str:
    """把 http(s) 媒体下载到临时文件，返回本地路径。

    分块流式写入（64KB/块），避免整段响应一次性载入内存。显式构建
    ProxyHandler opener 读取环境代理（http_proxy/https_proxy/all_proxy 等），
    与 Python 本体 aiohttp trust_env=True 语义对齐——urllib 默认未必走系统代理。
    ``ssl_context`` 非 None 时使用指定 TLS 上下文（证书校验降级重试用）。
    """
    os.makedirs(tempfile.gettempdir(), exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.close()
    handlers = [urllib.request.ProxyHandler()]
    if ssl_context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ssl_context))
    opener = urllib.request.build_opener(*handlers)
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


async def download_file(
    url: str,
    path: str | None = None,
    show_progress: bool = False,
    progress_callback=None,
    allow_insecure_ssl_fallback: bool = True,
) -> str:
    """下载远程文件到本地路径（签名与语义对齐本体 download_file）。

    - HTTP 非 200 抛 :class:`DownloadFileHTTPError`；网络/连接错误原样抛出；
    - TLS 证书校验失败时，``allow_insecure_ssl_fallback=True``（默认，对齐
      本体）会关闭证书校验重试一次，False 则直接抛出；
    - ``path`` 为本地目标路径（本体必填且返回 None；SDK 宽容支持省略并
      返回临时文件路径，按位置传参不受影响）；
    - ``show_progress`` / ``progress_callback`` 为兼容参数，SDK 薄壳不输出进度。
    """
    del show_progress, progress_callback  # 薄壳：进度回调宿主 download 原始路径忽略
    import ssl as _ssl

    save_path = path
    try:
        tmp = await asyncio.to_thread(_download_to_temp, url)
    except urllib.error.HTTPError as e:
        logger.error(
            "Failed to download file from %s. HTTP status code: %s",
            _safe_url_for_log(url),
            e.code,
        )
        raise DownloadFileHTTPError(
            "Failed to download file from "
            f"{_safe_url_for_log(url)}. HTTP status code: {e.code}"
        ) from e
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        if not isinstance(reason, _ssl.SSLError) or not allow_insecure_ssl_fallback:
            raise
        # TLS 证书校验失败：对齐本体，仅在允许时关闭校验重试一次
        logger.warning(
            f"SSL certificate verification failed for {_safe_url_for_log(url)}. "
            "Falling back to unverified connection (CERT_NONE). "
            "This is insecure and exposes the application to man-in-the-middle attacks. "
            "Please investigate and resolve certificate issues."
        )
        insecure_ctx = _ssl.create_default_context()
        insecure_ctx.check_hostname = False
        insecure_ctx.verify_mode = _ssl.CERT_NONE
        try:
            tmp = await asyncio.to_thread(
                _download_to_temp, url, ssl_context=insecure_ctx
            )
        except urllib.error.HTTPError as e2:
            logger.error(
                "Failed to download file from %s. HTTP status code: %s",
                _safe_url_for_log(url),
                e2.code,
            )
            raise DownloadFileHTTPError(
                "Failed to download file from "
                f"{_safe_url_for_log(url)}. HTTP status code: {e2.code}"
            ) from e2
    if save_path is None:
        return tmp
    os.makedirs(os.path.dirname(os.path.abspath(save_path)) or ".", exist_ok=True)
    shutil.move(tmp, save_path)
    return save_path


def file_to_base64(file_path: str) -> str:
    """读取文件并转为 base64:// 前缀的字符串（对齐原版 file_to_base64）。"""
    import base64 as _b64

    with open(file_path, "rb") as f:
        data_bytes = f.read()
    return "base64://" + _b64.b64encode(data_bytes).decode()


def get_local_ip_addresses():
    """列出本机所有 IPv4 地址（对齐原版 get_local_ip_addresses）。

    psutil 不可用时回退 socket 连接探测（到公共地址拿本机 IP）。
    """
    import socket

    try:
        import psutil

        net_interfaces = psutil.net_if_addrs()
        out = []
        for _iface, addrs in net_interfaces.items():
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    out.append(addr.address)
        if out:
            return out
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return [ip]
    except Exception:
        return []


def port_checker(port: int, host: str = "localhost") -> bool:
    """检查端口是否可连接（对齐原版 port_checker）。"""
    import socket

    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sk.settimeout(1)
    try:
        sk.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        try:
            sk.close()
        except Exception:
            pass


async def download_image_by_url(
    url: str,
    post: bool = False,
    post_data: dict | None = None,
    path: str | None = None,
) -> str:
    """下载图片并返回本地路径（对齐原版 download_image_by_url）。

    aiohttp 不可用时回退 urllib（GET；POST 场景不支持时抛异常）。
    """
    try:
        import aiohttp
    except ImportError:
        aiohttp = None

    if aiohttp is not None:
        async with aiohttp.ClientSession(trust_env=True) as session:
            if post:
                resp = await session.post(url, json=post_data)
            else:
                resp = await session.get(url)
            async with resp:
                data = await resp.read()
    else:
        if post:
            raise NotImplementedError("aiohttp 未安装，不支持 POST 下载图片")
        import urllib.request

        opener = urllib.request.build_opener(urllib.request.ProxyHandler())
        data = opener.open(url, timeout=30).read()
        try:
            import io
            from PIL import Image

            data = io.BytesIO(Image.open(io.BytesIO(data)).convert("RGB").tobytes())
        except Exception:
            pass

    if path:
        with open(path, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.getvalue())
        return path
    return save_temp_img(data)


def save_temp_img(img) -> str:
    """把 PIL Image 或 bytes 保存为临时 JPEG 并返回路径（对齐原版 save_temp_img）。"""
    import time
    import uuid

    from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

    temp_dir = get_astrbot_temp_path()
    os.makedirs(temp_dir, exist_ok=True)
    timestamp = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    p = os.path.join(temp_dir, f"io_temp_img_{timestamp}.jpg")

    if hasattr(img, "save") and not isinstance(img, (bytes, bytearray)):
        img.save(p)
    else:
        if hasattr(img, "getvalue"):
            img = img.getvalue()
        with open(p, "wb") as f:
            f.write(img)
    return p
