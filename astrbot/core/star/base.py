"""Star 基类（Go 宿主兼容运行时）。"""
from __future__ import annotations

import json
import logging
from typing import Any

from astrbot.core import html_renderer
from astrbot.core.log import LogManager
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.star.star import StarMetadata, star_map, star_registry
from astrbot.core.utils.command_parser import CommandParserMixin
from astrbot.core.utils.plugin_kv_store import PluginKVStoreMixin

logger = logging.getLogger("astrbot")


class Star(CommandParserMixin, PluginKVStoreMixin):
    """所有插件（Star）的父类，所有插件都应该继承于这个类"""

    author: str
    name: str
    context: Any
    logger: logging.Logger

    def __init__(self, context, config: dict | None = None) -> None:
        self.context = context
        metadata = star_map.get(self.__class__.__module__)
        plugin_name = (metadata.name if metadata else None) or getattr(
            self, "name", None
        )
        self.logger = (
            logging.getLogger(f"astrbot.plugin.{plugin_name}")
            if plugin_name
            else logging.getLogger("astrbot")
        )
        # 无条件设置 self.config（对齐本体）：配置拉取失败（None）时插件
        # 访问 self.config 不再 AttributeError
        self.config = config or {}

    def _get_context_config(self) -> Any:
        get_config = getattr(self.context, "get_config", None)
        if callable(get_config):
            try:
                return get_config()
            except Exception as e:
                logger.debug(f"get_config() failed: {e}")
                return None
        return getattr(self.context, "_config", None)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not star_map.get(cls.__module__):
            metadata = StarMetadata(
                star_cls_type=cls,
                module_path=cls.__module__,
            )
            star_map[cls.__module__] = metadata
            star_registry.append(metadata)
        else:
            star_map[cls.__module__].star_cls_type = cls
            star_map[cls.__module__].module_path = cls.__module__

    async def text_to_image(self, text: str, return_url=True) -> str:
        """将文本转换为图片（宿主 t2i 渲染）。

        返回图片的 base64（data:image/png;base64,...）或本地临时文件路径。
        """
        if not text.strip():
            raise ValueError("text_to_image 的文本不能为空")
        from astrbot._bridge.host import get_bridge

        try:
            # text_to_image 是同步 RPC，经 host.py 的 text_to_image_async
            # （asyncio.to_thread 包装）移出事件循环，避免阻塞常驻 loop。
            png = await get_bridge().text_to_image_async(text, "")
        except Exception as e:
            logger.warning(f"text_to_image 失败: {e}")
            raise
        if not return_url:
            import base64

            return base64.b64encode(png).decode()
        # 返回 data URL，插件可直接作为 Image 组件发送
        import base64

        return "data:image/png;base64," + base64.b64encode(png).decode()

    async def html_render(
        self,
        tmpl: str,
        data: dict,
        return_url=True,
        options: dict | None = None,
    ) -> str:
        """将 HTML/Jinja2 模板渲染为图片（宿主 t2i 优先 + 兜底渲染）。

        返回图片的 base64（data:image/png;base64,...）或本地临时文件路径。
        data/options 支持直接传入已序列化的 JSON 字符串。
        """
        from astrbot._bridge.host import get_bridge

        if isinstance(data, str):
            data_json = data
        else:
            data_json = json.dumps(data, ensure_ascii=False)
        if isinstance(options, str):
            options_json = options
        else:
            options_json = json.dumps(options or {}, ensure_ascii=False)

        try:
            # html_render 是同步 RPC，经 host.py 的 html_render_async
            # （asyncio.to_thread 包装）移出事件循环，避免阻塞常驻 loop。
            png = await get_bridge().html_render_async(tmpl, data_json, options_json)
        except Exception as e:
            logger.warning(f"html_render 失败: {e}")
            raise
        if not return_url:
            import base64

            return base64.b64encode(png).decode()
        # 返回 data URL，插件可直接作为 Image 组件发送
        import base64

        return "data:image/png;base64," + base64.b64encode(png).decode()

    async def initialize(self) -> None:
        """当插件被激活时会调用这个方法"""

    async def terminate(self) -> None:
        """当插件被禁用、重载插件时会调用这个方法"""

    async def shutdown(self) -> None:
        """别名（部分插件实现 shutdown）。"""

    def __del__(self) -> None:
        pass
