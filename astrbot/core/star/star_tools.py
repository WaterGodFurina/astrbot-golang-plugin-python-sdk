"""Star 工具（移植自 Python AstrBot star_tools，数据目录走宿主约定）。"""

import inspect
import os
from pathlib import Path

from astrbot.core.star.star import star_map


class StarTools:
    @classmethod
    def get_data_dir(cls, plugin_name: str | None = None) -> Path:
        """返回插件数据目录（绝对路径，自动创建）。

        宿主约定：<data>/plugins_data/<plugin_name>（ASTRBOT_PLUGIN_DATA_DIR，
        与 Go 插件的统一数据根一致，卸载时可整体清理）。plugin_name 缺省时
        从调用栈解析调用者插件名（对齐 Python 本体语义）。
        """
        if not plugin_name:
            frame = inspect.currentframe()
            module = None
            if frame:
                frame = frame.f_back
                module = inspect.getmodule(frame)
            if module is not None:
                metadata = star_map.get(module.__name__)
                if metadata is not None:
                    plugin_name = metadata.name
        if not plugin_name:
            raise ValueError("无法解析插件名")

        base = os.environ.get("ASTRBOT_PLUGIN_DATA_DIR")
        if not base:
            base = os.path.join(os.environ.get("ASTRBOT_DATA_PATH", "data"), "plugins_data")
        data_dir = Path(base) / plugin_name
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise RuntimeError(f"创建插件数据目录失败: {e}") from e
        return data_dir
