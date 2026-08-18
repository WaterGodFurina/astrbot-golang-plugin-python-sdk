"""插件元数据常量与更新器（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.star.updater`：提供
`PLUGIN_METADATA_FILENAMES` 与 `_PluginUpdater`。
SDK 降级：更新逻辑由 Go 宿主侧完成，这里仅保证 import 与调用不报错。
"""

PLUGIN_METADATA_FILENAMES = ("metadata.yaml", "metadata.yml")

__all__ = ["PLUGIN_METADATA_FILENAMES"]


class _PluginUpdater:
    """插件安装/更新器（SDK 降级：交由宿主处理）。"""

    def __init__(self, verify: str | bool | None = None) -> None:
        self.verify = verify

    def get_plugin_store_path(self) -> str:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_path

        return get_astrbot_plugin_path()

    @staticmethod
    def validate_plugin_metadata(metadata: dict, label: str = "metadata.yaml") -> None:
        """校验插件元数据（SDK 降级：只检查必要字段存在性）。"""
        for field_name in ("name", "author", "desc", "version"):
            if not metadata.get(field_name):
                raise ValueError(f"{label} 缺少必要字段: {field_name}")
