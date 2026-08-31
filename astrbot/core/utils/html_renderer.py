"""HTML 文转图渲染器（Go 宿主兼容运行时）。

独立于 astrbot.api 的轻量模块，避免 astrbot.core 与 astrbot.api
之间的循环导入。astrbot.api 侧 re-export 本模块的同名类与实例，
保证 `from astrbot.api import html_renderer` 与
`from astrbot.core import html_renderer` 拿到同一对象（对齐本体）。

Go 宿主无独立 HTML 模板引擎：render_t2i / render_custom_template
均走宿主桥（HostBridge.text_to_image_async / html_render_async，
宿主 internal/t2i 原生实现），渲染失败返回 None。
"""
import base64
import logging

logger = logging.getLogger("astrbot")


class HtmlRenderer:
    """HTML 文转图渲染器实现（宿主桥承载）。"""

    async def render_t2i(self, text: str, return_url: bool = True):
        """将文本渲染为图片，返回 data URL（默认）或 PNG 字节。"""
        from astrbot._bridge.host import get_bridge

        try:
            png = await get_bridge().text_to_image_async(text)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"文本渲染图片失败: {e}")
            return None
        if not return_url:
            return png
        return "data:image/png;base64," + base64.b64encode(png).decode()

    async def render_custom_template(
        self, tmpl: str, data: dict, return_url: bool = True
    ):
        """自定义模板渲染：经宿主 HtmlRender 桥渲染。

        模板内容 tmpl 与渲染数据 data（序列化为 JSON）一并交给宿主，返回
        渲染出的 PNG 图片（return_url=True 时返回 data URL，否则返回 PNG
        字节）；渲染失败返回 None 并告警。
        """
        import json

        from astrbot._bridge.host import get_bridge

        try:
            png = await get_bridge().html_render_async(
                template=tmpl,
                data=json.dumps(data, ensure_ascii=False),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"自定义模板渲染失败: {e}")
            return None
        if not return_url:
            return png
        return "data:image/png;base64," + base64.b64encode(png).decode()

    async def initialize(self) -> None:
        """初始化：无宿主资源需要预热，no-op。"""


html_renderer = HtmlRenderer()  # 对齐原版 astrbot.core.html_renderer 的实例
