"""Web 搜索工具（Go 宿主兼容运行时，对齐本体 tools/web_search_tools.py）。

SDK 薄壳：各类搜索/网页提取工具类名与 name 对齐本体，宿主 web_search 引擎
原生执行；`normalize_legacy_web_search_config` 为纯配置规范化函数（本地实现）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from astrbot.core.agent.tool import FunctionTool

logger = logging.getLogger("astrbot")

WEB_SEARCH_TOOL_NAMES = [
    "web_search_tavily",
    "tavily_extract_web_page",
    "web_search_bocha",
    "web_search_brave",
    "web_search_firecrawl",
    "firecrawl_extract_web_page",
    "web_search_baidu",
    "web_search_exa",
    "exa_get_contents",
]

_QUERY_SCHEMA: dict = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
}
_URL_SCHEMA: dict = {
    "type": "object",
    "properties": {"url": {"type": "string"}},
    "required": ["url"],
}
_URLS_SCHEMA: dict = {
    "type": "object",
    "properties": {"urls": {"type": "array", "items": {"type": "string"}}},
    "required": ["urls"],
}


@dataclass
class TavilyWebSearchTool(FunctionTool):
    name: str = "web_search_tavily"
    description: str = "Search the web using Tavily."
    parameters: dict = field(default_factory=lambda: dict(_QUERY_SCHEMA))


@dataclass
class TavilyExtractWebPageTool(FunctionTool):
    name: str = "tavily_extract_web_page"
    description: str = "Extract the content of a web page using Tavily."
    parameters: dict = field(default_factory=lambda: dict(_URL_SCHEMA))


@dataclass
class BochaWebSearchTool(FunctionTool):
    name: str = "web_search_bocha"
    description: str = "Search the web using Bocha."
    parameters: dict = field(default_factory=lambda: dict(_QUERY_SCHEMA))


@dataclass
class BraveWebSearchTool(FunctionTool):
    name: str = "web_search_brave"
    description: str = "Search the web using Brave."
    parameters: dict = field(default_factory=lambda: dict(_QUERY_SCHEMA))


@dataclass
class FirecrawlWebSearchTool(FunctionTool):
    name: str = "web_search_firecrawl"
    description: str = "Search the web using Firecrawl."
    parameters: dict = field(default_factory=lambda: dict(_QUERY_SCHEMA))


@dataclass
class FirecrawlExtractWebPageTool(FunctionTool):
    name: str = "firecrawl_extract_web_page"
    description: str = "Extract the content of a web page using Firecrawl."
    parameters: dict = field(default_factory=lambda: dict(_URL_SCHEMA))


@dataclass
class BaiduWebSearchTool(FunctionTool):
    name: str = "web_search_baidu"
    description: str = "Search the web using Baidu."
    parameters: dict = field(default_factory=lambda: dict(_QUERY_SCHEMA))


@dataclass
class ExaWebSearchTool(FunctionTool):
    name: str = "web_search_exa"
    description: str = "Search the web using Exa."
    parameters: dict = field(default_factory=lambda: dict(_QUERY_SCHEMA))


@dataclass
class ExaGetContentsTool(FunctionTool):
    name: str = "exa_get_contents"
    description: str = "Get the contents of web pages using Exa."
    parameters: dict = field(default_factory=lambda: dict(_URLS_SCHEMA))


def normalize_legacy_web_search_config(cfg) -> None:
    """规范化旧版 web_search 配置（对齐本体 normalize_legacy_web_search_config）。

    Go 宿主配置由宿主侧 web_search 引擎读取，SDK 侧空实现保证调用不报错。
    """
    _ = cfg
    return None


__all__ = [
    "BaiduWebSearchTool",
    "BochaWebSearchTool",
    "BraveWebSearchTool",
    "ExaGetContentsTool",
    "ExaWebSearchTool",
    "FirecrawlExtractWebPageTool",
    "FirecrawlWebSearchTool",
    "TavilyExtractWebPageTool",
    "TavilyWebSearchTool",
    "WEB_SEARCH_TOOL_NAMES",
    "normalize_legacy_web_search_config",
]