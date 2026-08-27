"""插件硬编码路径兼容层（Go 宿主兼容运行时）。

Python AstrBot 本体中插件目录名为 data/plugins/<name>（无语言后缀），插件
源码常硬编码该路径访问自身数据文件（如 SensitiveLexicon.json）。Go 宿主
统一按 <name>_<language> 安装（如 astrbot_plugin_qqadmin_python），导致
源码里硬编码的旧路径失效（FileNotFoundError）。

本模块在插件加载时：
1. 正则扫描插件源码，检测硬编码指向旧目录名（<name>）的路径字符串；
2. 命中时对 builtins.open / os.stat / os.lstat / os.listdir / os.scandir
   做段级路径重定向（plugins/<name>/ → plugins/<name>_<lang>/）；
3. 仅替换路径中的目录段，其余部分（文件名/子路径）原样保留。

特性：
- 不依赖符号链接/junction（Windows / Termux / 容器均无权限问题）；
- 无静态映射表：按源码实际检测，不维护表、不存在 Go/Python 重名冲突；
- 仅在检测到硬编码旧路径时才安装 hook（无命中则零开销）；
- 幂等：插件重载时重复 install 只更新段映射，不重复 hook。
"""
from __future__ import annotations

import builtins
import io
import os
import re
from typing import Optional

# 语言后缀：插件稳定 ID = sanitizePluginName(name) + "_" + language
_LANG_SUFFIXES = ("_python", "_go")

# 匹配源码字符串字面量中指向插件代码目录的路径（plugins/ 分隔，支持
# 绝对/相对路径与 / 或 \\ 分隔符）。注意：只匹配 plugins/（代码目录，
# Go 宿主布局为 <name>_<lang>），不匹配 plugins_data/（数据目录，Go 宿主
# 布局为 <name>，本就无语言后缀，重定向反而破坏真实数据目录）。
_PATH_LITERAL_RE = re.compile(
    r"""(?P<quote>['"])(?P<path>[^'"]*plugins[/\\](?P<name>[A-Za-z0-9_\-]+)[/\\])"""
)

# 匹配运行时构造插件路径的目录名常量（无语言后缀）：插件常写
# `_plugin_name = "astrbot_plugin_qqadmin"` 再
# `Path(get_astrbot_plugin_path()) / _plugin_name` 拼数据路径，或
# `self.plugin_dir = Path(...) / "astrbot_plugin_qqadmin"`。这类引用
# 没有 plugins/ 前缀上下文，字符串扫描必须单独检测，否则 hook 不安装、
# 运行时 Path.open()/stat() 无法被重定向。
_PLUGIN_NAME_LITERAL_RE = re.compile(
    r"""(?:plugin_name|plugin_dir|_plugin_name)(?::\s*[A-Za-z_][A-Za-z0-9_\[\]\.]*)?\s*=\s*(?:Path\()?["'](?P<name>[A-Za-z0-9_\-]+)["']"""
)

# 当前插件的目录段重定向（None = 未激活）
_legacy_pattern: Optional[re.Pattern] = None
_real_name: str = ""

# 已安装的 hook（幂等标记）
_hooked = False

_orig_open = builtins.open
_orig_stat = os.stat
_orig_lstat = os.lstat
_orig_listdir = os.listdir
_orig_scandir = os.scandir


def _strip_lang_suffix(name: str) -> str:
    for suffix in _LANG_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def _scan_hardcoded_dirs(plugin_dir: str, legacy_name: str) -> bool:
    """扫描插件源码，检测是否存在硬编码指向旧目录名的引用。

    两类命中：
    1. 路径字符串字面量（plugins/<legacy_name>/...）——_PATH_LITERAL_RE；
    2. 运行时拼路径的目录名常量（plugin_name/plugin_dir = "<legacy_name>"，
       常配合 get_astrbot_plugin_path() 拼 Path）——_PLUGIN_NAME_LITERAL_RE。
    """
    for root, _dirs, files in os.walk(plugin_dir):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    src = f.read()
            except OSError:
                continue
            for m in _PATH_LITERAL_RE.finditer(src):
                if m.group("name") == legacy_name:
                    return True
            for m in _PLUGIN_NAME_LITERAL_RE.finditer(src):
                if m.group("name") == legacy_name:
                    return True
    return False


def _redirect(p: str) -> str:
    if _legacy_pattern is None or not p:
        return p
    p = os.fspath(p)
    return _legacy_pattern.sub(lambda m: m.group(1) + _real_name, p)


def _install_hooks() -> None:
    global _hooked
    if _hooked:
        return

    def _hooked_open(file, mode="r", buffering=-1, encoding=None,
                     errors=None, newline=None, closefd=True, opener=None):
        return _orig_open(_redirect(file), mode, buffering, encoding,
                          errors, newline, closefd, opener)

    def _hooked_stat(path, *args, **kwargs):
        return _orig_stat(_redirect(path), *args, **kwargs)

    def _hooked_lstat(path, *args, **kwargs):
        return _orig_lstat(_redirect(path), *args, **kwargs)

    def _hooked_listdir(path="."):
        return _orig_listdir(_redirect(path))

    def _hooked_scandir(path="."):
        return _orig_scandir(_redirect(path))

    builtins.open = _hooked_open
    # pathlib 内部直接引用 io.open（CPython C 实现），builtins.open 与之
    # 不同名时 Path.open()/read_text()/write_text() 不会经过 builtins.open，
    # 需一并替换 io.open 才能覆盖 pathlib 读写路径。
    io.open = _hooked_open
    os.stat = _hooked_stat
    os.lstat = _hooked_lstat
    os.listdir = _hooked_listdir
    os.scandir = _hooked_scandir
    _hooked = True


def install(plugin_dir: str) -> None:
    """为插件进程安装硬编码路径重定向（幂等，可重复调用）。

    插件目录名为 <name>_<lang> 且源码中检测到硬编码的 <name> 旧路径时
    才安装；否则为 no-op，不引入任何运行时开销。
    """
    global _legacy_pattern, _real_name
    real = os.path.realpath(plugin_dir)
    real_name = os.path.basename(real)
    legacy_name = _strip_lang_suffix(real_name)
    if legacy_name == real_name:
        # 目录名无语言后缀（非 Go 宿主布局），无兼容需求。
        return
    if not _scan_hardcoded_dirs(real, legacy_name):
        # 源码未硬编码旧路径，不安装 hook。
        return
    # 段级重定向：plugins/<legacy_name>[/] → plugins/<real_name>[/]（兼容两种
    # 分隔符）。只重定向 plugins/（代码目录），plugins_data/ 数据目录本就
    # 无语言后缀，重定向会破坏真实数据目录。
    _legacy_pattern = re.compile(
        rf"(plugins[/\\]){re.escape(legacy_name)}(?=$|[/\\])"
    )
    _real_name = real_name
    _install_hooks()