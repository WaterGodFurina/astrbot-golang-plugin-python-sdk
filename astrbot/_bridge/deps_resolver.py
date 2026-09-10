"""Python 依赖懒加载器：import 缺失时按映射表自动 pip 安装。

背景：宿主 venv 此前全量预装 40+ 包导致 bridge 冷启动 1-3 分钟。改为
分层依赖后：宿主只预装核心层（grpcio / protobuf），其余依赖在插件
运行时 import 缺失时按 IMPORT_MAP 映射自动 pip 安装。插件
requirements.txt 的安装逻辑不变（仍由宿主负责）。

用法：astrbot._bridge.server 启动最早期调用 install_import_resolver()
向 sys.meta_path 末尾追加 LazyDepFinder——meta_path 依次查找，轮到它
即说明前面的 finder 都没找到该模块，此时按顶层模块名查 IMPORT_MAP，
命中则 pip 安装后重新查找并返回 spec。

并发模型：bridge 是多线程 gRPC server，且同一 venv 可能被多个 bridge
进程共享，pip 安装以「进程内 threading.Lock + 跨进程文件锁」双重
串行化，避免并发写坏 site-packages。
"""
from __future__ import annotations

import contextlib
import importlib
import importlib.abc
import importlib.util
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time

logger = logging.getLogger("astrbot.deps_resolver")

# 顶层 import 模块名 → pip 包名。绝大多数键即顶层模块名
# （fullname.split(".")[0]）；个别键如 google.protobuf 为二级模块名，
# 查询时先按完整名精确匹配、再按顶层名回退（见 _lookup_pkg）。
IMPORT_MAP: dict[str, str] = {
    # ── 核心层 / 桥接 ──
    "grpc": "grpcio",
    "google.protobuf": "protobuf",
    # ── 通用工具 ──
    "aiosqlite": "aiosqlite",
    "yaml": "pyyaml",
    "packaging": "packaging",
    "attr": "attrs",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "toml": "toml",
    "tomli": "tomli",
    "orjson": "orjson",
    "ujson": "ujson",
    "regex": "regex",
    "tqdm": "tqdm",
    "colorlog": "colorlog",
    "aiofiles": "aiofiles",
    "click": "click",
    "tzdata": "tzdata",
    "psutil": "psutil",
    "magic": "python-magic",
    "serial": "pyserial",
    "Crypto": "pycryptodome",
    "git": "GitPython",
    "deprecated": "Deprecated",
    "docstring_parser": "docstring-parser",
    "tenacity": "tenacity",
    # ── Web / HTTP ──
    "quart": "quart",
    "werkzeug": "werkzeug",
    "jinja2": "jinja2",
    "aiohttp": "aiohttp",
    "httpx": "httpx",
    "requests": "requests",
    "flask": "flask",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "websockets": "websockets",
    "bs4": "beautifulsoup4",
    "lxml": "lxml",
    # ── LLM / MCP / 调度 ──
    "mcp": "mcp",
    "openai": "openai",
    "anthropic": "anthropic",
    "dashscope": "dashscope",
    "pydantic": "pydantic",
    "apscheduler": "apscheduler",
    # ── 平台 SDK ──
    "botpy": "qq-botpy",
    "telegram": "python-telegram-bot",
    # ── 媒体 / 文档 / 图像 ──
    "PIL": "pillow",
    "markdown": "markdown",
    "qrcode": "qrcode",
    "cryptography": "cryptography",
    "pydub": "pydub",
    "mutagen": "mutagen",
    "eyed3": "eyed3",
    "openpyxl": "openpyxl",
    "pypdf": "pypdf",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "cv2": "opencv-python-headless",
    # ── 数据 / 科学计算 / 中文分词 ──
    "matplotlib": "matplotlib",
    "pandas": "pandas",
    "numpy": "numpy",
    "scipy": "scipy",
    "jieba": "jieba",
    "rank_bm25": "rank-bm25",
    "sklearn": "scikit-learn",
    # ── 认证 ──
    "jwt": "pyjwt",
    # ── 存储 / 数据库 ──
    "redis": "redis",
    "pymysql": "pymysql",
    "psycopg2": "psycopg2-binary",
    "sqlalchemy": "SQLAlchemy",
}

# 单次 pip 安装超时（秒）
_INSTALL_TIMEOUT = 300

# 跨进程锁文件：同一 venv 可能被多个 bridge 进程共享
_LOCK_FILE = os.path.join(tempfile.gettempdir(), "astrbot-py-deps.lock")

# 进程内互斥：finder 解析 + pip 安装串行化（gRPC server 多线程）
_resolver_lock = threading.Lock()

# 每个映射包只尝试安装一次（含失败），防止坏包名反复触发 pip 拖垮启动
_attempted: set[str] = set()

# 元路径重入防护：find_spec 内部（invalidate_caches / find_spec 重查）
# 可能再次触发 import，正在解析的名字直接放行返回 None
_resolving: set[str] = set()


@contextlib.contextmanager
def _file_lock():
    """跨进程文件锁：POSIX 用 fcntl.flock，Windows 用 msvcrt.locking。"""
    f = open(_LOCK_FILE, "a+")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    locked = True
                    break
                except OSError:
                    # LK_LOCK 默认 10s 放弃一次，循环重试直到拿到锁
                    time.sleep(0.05)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            locked = True
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        f.close()


def _top_level(fullname: str) -> str:
    """完整模块名 → 顶层模块名（astrbot._bridge.dummy → astrbot）。"""
    return fullname.split(".")[0]


def _lookup_pkg(fullname: str) -> str | None:
    """import 模块名 → pip 包名：先精确匹配（覆盖 google.protobuf 这类
    二级名），再按顶层名回退；未映射返回 None。"""
    return IMPORT_MAP.get(fullname) or IMPORT_MAP.get(_top_level(fullname))


def ensure_import_module(fullname: str) -> bool:
    """确保 fullname 可导入：查 IMPORT_MAP 命中则 pip 安装映射包。

    - 子进程继承当前环境变量（PIP_INDEX_URL pip 原生识别；宿主自定义源
      ASTRBOT_PYPI_INDEX 显式转为 -i 参数）；
    - 带 --disable-pip-version-check，输出进 debug 日志；
    - 拿到跨进程文件锁后先双检（venv 可能已被其他 bridge 进程装好）；
    - 成功返回 True（已 importlib.invalidate_caches()），任何失败返回 False。
    """
    pkg = _lookup_pkg(fullname)
    if pkg is None:
        return False
    with _file_lock():
        # 双检：等锁期间同一 venv 的其他进程可能已完成安装
        try:
            importlib.import_module(fullname)
            return True
        except ImportError:
            pass
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
        index = os.environ.get("ASTRBOT_PYPI_INDEX")
        if index:
            cmd += ["-i", index]
        cmd.append(pkg)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_INSTALL_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "懒加载安装 Python 依赖超时(%ss): %s (import %s)",
                _INSTALL_TIMEOUT, pkg, fullname,
            )
            return False
        except OSError as e:
            logger.warning(
                "懒加载安装 Python 依赖失败（无法启动 pip）: %s (import %s): %s",
                pkg, fullname, e,
            )
            return False
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if proc.returncode != 0:
            logger.warning(
                "懒加载安装 Python 依赖失败: %s (import %s) rc=%s 输出: %s",
                pkg, fullname, proc.returncode, output[-2000:],
            )
            return False
        logger.debug(
            "懒加载安装 Python 依赖完成: %s (import %s) 输出: %s",
            pkg, fullname, output[-2000:],
        )
        importlib.invalidate_caches()
        return True


class LazyDepFinder(importlib.abc.MetaPathFinder):
    """sys.meta_path 末尾的懒加载 Finder。

    被调用即说明前面的 finder（builtin / frozen / path）都没找到
    fullname：查 IMPORT_MAP 命中则 pip 安装，装好后重新 find_spec 并
    返回该 spec；未映射 / 安装失败 / 仍找不到一律返回 None（让 import
    抛出原始 ModuleNotFoundError）。
    """

    def find_spec(self, fullname, path=None, target=None):
        # 元路径重入防护（双重检查都在锁内，覆盖多线程场景）
        if fullname in _resolving:
            return None
        with _resolver_lock:
            if fullname in _resolving:
                return None
            _resolving.add(fullname)
            try:
                return self._resolve(fullname)
            finally:
                _resolving.discard(fullname)

    def _resolve(self, fullname):
        pkg = _lookup_pkg(fullname)
        if pkg is None:
            return None
        if pkg in _attempted:
            # 该映射包已尝试过（含失败）：不再重复 pip，放行让 import 报错
            return None
        _attempted.add(pkg)
        logger.info("懒加载安装 Python 依赖: %s (import %s)", pkg, fullname)
        if not ensure_import_module(fullname):
            logger.warning("懒加载安装 Python 依赖失败: %s (import %s)", pkg, fullname)
            return None
        # 新包落盘后 path finder 的目录缓存可能过期 → 先刷新再重查
        importlib.invalidate_caches()
        try:
            return importlib.util.find_spec(fullname)
        except (ImportError, ValueError):
            return None


# 单例 finder（install/uninstall 以身份比较操作 sys.meta_path）
_finder = LazyDepFinder()


def install_import_resolver() -> None:
    """把 LazyDepFinder 追加到 sys.meta_path 末尾（幂等）。

    必须放在末尾：只有前面的 finder 都找不到时才轮到懒加载安装。
    """
    with _resolver_lock:
        if _finder not in sys.meta_path:
            sys.meta_path.append(_finder)


def uninstall_import_resolver() -> None:
    """移除 LazyDepFinder（测试用，幂等）。"""
    with _resolver_lock:
        try:
            sys.meta_path.remove(_finder)
        except ValueError:
            pass
