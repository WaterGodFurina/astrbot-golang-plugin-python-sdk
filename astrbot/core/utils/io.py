"""通用 IO 工具（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.utils.io` 中插件常用的
`remove_dir` / `ensure_dir`。
"""
import os
import shutil


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
