"""mcp 兼容层（嵌入式最小实现）。

Python 插件常 `from mcp.types import CallToolResult, TextContent` 构造工具
返回（如 Bing 搜索插件的 FunctionTool.run）。宿主 venv 不安装完整 mcp 包，
这里提供插件实际用到的最小类型；宿主管线（HandleTool）识别这些对象的
content[].text 提取结果文本。
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextContent:
    type: str = "text"
    text: str = ""


@dataclass
class ImageContent:
    type: str = "image"
    data: str = ""
    mimeType: str = "image/png"


@dataclass
class CallToolResult:
    content: list[Any] = field(default_factory=list)
    isError: bool = False
    structuredContent: dict | None = None
