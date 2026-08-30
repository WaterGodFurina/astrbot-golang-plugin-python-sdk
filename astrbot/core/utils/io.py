"""通用 IO 工具（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.utils.io` 中插件常用的
`remove_dir` / `ensure_dir` / `download_file`。
"""
import asyncio
import logging
import os
import shutil
import tempfile
import urllib.request


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


def ensure_dir(path) -> None:
    """确保目录存在（递归创建，已存在不报错）。"""
    os.makedirs(path, exist_ok=True)


def _download_to_temp(url: str, suffix: str = "") -> str:
    """把 http(s) 媒体下载到临时文件，返回本地路径。

    分块流式写入（64KB/块），避免整段响应一次性载入内存。显式构建
    ProxyHandler opener 读取环境代理（http_proxy/https_proxy/all_proxy 等），
    与 Python 本体 aiohttp trust_env=True 语义对齐——urllib 默认未必走系统代理。
    """
    os.makedirs(tempfile.gettempdir(), exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.close()
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


async def download_file(
    url: str,
    path: str | None = None,
    show_progress: bool = False,
    progress_callback=None,
) -> str:
    """下载 url 到本地文件（Go 宿主兼容运行时，签名对齐本体）。

    对齐原版 `astrbot.core.utils.io.download_file`：
    - ``path`` 为本地目标路径；未指定时下载到系统临时目录，返回临时文件路径；
    - ``show_progress`` / ``progress_callback`` 与本体一致（SDK 薄壳不输出
      进度，download 时忽略，仅保证签名兼容）；
    - 下载失败不抛异常，返回空字符串（插件可据此判断失败）。
    """
    del show_progress, progress_callback  # 薄壳：进度回调宿主 download 原始路径忽略
    save_path = path
    try:
        tmp = await asyncio.to_thread(_download_to_temp, url)
    except Exception as e:
        logging.getLogger("astrbot").warning(f"download_file 失败: {url!r} ({e})")
        return ""
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
