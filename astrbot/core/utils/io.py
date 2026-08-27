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


async def download_file(url: str, save_path: str | None = None) -> str:
    """下载 url 到本地文件（Go 宿主兼容运行时）。

    对齐原版 `astrbot.core.utils.io.download_file` 的调用习惯：
    - 未指定 save_path 时下载到系统临时目录，返回临时文件路径；
    - 下载失败不抛异常，返回空字符串（插件可据此判断失败）。
    """
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
