"""HTML 文转图渲染器占位（Go 宿主兼容运行时）。

独立于 astrbot.api 的轻量模块，避免 astrbot.core 与 astrbot.api
之间的循环导入。实例对象与 astrbot.api.html_renderer 语义一致：
Go 宿主无独立 HTML 模板引擎，render_t2i 走宿主桥 text_to_image_async。
"""


class HtmlRenderer:
    """HTML 文转图渲染器占位实现（Go 宿主无独立 HTML 模板引擎）。

    render_t2i 走宿主桥 HostBridge.text_to_image_async（宿主渲染文本为
    图片，返回 base64 PNG）解码为 PNG 字节；return_url=True 时返回
    data:image/png;base64,... 前缀的 URL，否则返回 bytes。渲染失败返回
    None。render_custom_template 因无模板引擎支持，始终返回 None。
    """

    async def render_t2i(self, text: str, return_url: bool = True):
        """将文本渲染为图片，返回 data URL（默认）或 PNG 字节。"""
        import base64

        from astrbot._bridge.host import get_bridge

        try:
            png = await get_bridge().text_to_image_async(text)
        except Exception:
            return None
        if not return_url:
            return png
        return "data:image/png;base64," + base64.b64encode(png).decode()

    async def render_custom_template(
        self, tmpl: str, data: dict, return_url: bool = True
    ):
        """自定义模板渲染：Go 宿主无模板引擎，直接返回 None。"""
        return None

    async def initialize(self) -> None:
        """初始化：无宿主资源需要预热，no-op。"""


html_renderer = HtmlRenderer()  # 对齐原版 astrbot.core.html_renderer 的占位实例
