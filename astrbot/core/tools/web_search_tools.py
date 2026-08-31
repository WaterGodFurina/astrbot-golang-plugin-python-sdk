"""Web 搜索工具（Go 宿主兼容运行时，对齐本体 tools/web_search_tools.py）。

SDK 薄壳：各搜索/网页提取工具类的 name / description / parameters（schema）
均与本体一致并经 ``builtin_tool`` 注册，agent 循环中的真实检索由宿主
web_search 引擎原生执行（internal/pipeline/websearch.go）；
``SearchResult`` / ``normalize_legacy_web_search_config`` 为纯本地对齐实现。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.tools.registry import builtin_tool

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

# 各搜索工具的装饰器 config（对齐本体 web_search_tools.py:27-50）。
_TAVILY_WEB_SEARCH_TOOL_CONFIG = {
    "provider_settings.web_search": True,
    "provider_settings.websearch_provider": "tavily",
}
_BOCHA_WEB_SEARCH_TOOL_CONFIG = {
    "provider_settings.web_search": True,
    "provider_settings.websearch_provider": "bocha",
}
_BRAVE_WEB_SEARCH_TOOL_CONFIG = {
    "provider_settings.web_search": True,
    "provider_settings.websearch_provider": "brave",
}
_FIRECRAWL_WEB_SEARCH_TOOL_CONFIG = {
    "provider_settings.web_search": True,
    "provider_settings.websearch_provider": "firecrawl",
}
_BAIDU_WEB_SEARCH_TOOL_CONFIG = {
    "provider_settings.web_search": True,
    "provider_settings.websearch_provider": "baidu_ai_search",
}
_EXA_WEB_SEARCH_TOOL_CONFIG = {
    "provider_settings.web_search": True,
    "provider_settings.websearch_provider": "exa",
}


@dataclass
class SearchResult:
    """单条搜索结果（对齐本体 web_search_tools.SearchResult 字段）。"""

    title: str
    url: str
    snippet: str
    favicon: str | None = None


@builtin_tool(config=_TAVILY_WEB_SEARCH_TOOL_CONFIG)
@dataclass
class TavilyWebSearchTool(FunctionTool):
    name: str = "web_search_tavily"
    description: str = (
        "A web search tool that uses Tavily to search the web for relevant content. "
        "Ideal for gathering current information, news, and detailed web content analysis."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Required. Search query."},
                "max_results": {
                    "type": "integer",
                    "description": "Optional. The maximum number of results to return. Default is 7. Range is 5-20.",
                },
                "search_depth": {
                    "type": "string",
                    "description": "Optional. The depth of the search, must be one of \"basic\", \"advanced\". Default is \"basic\".",
                },
                "topic": {
                    "type": "string",
                    "description": "Optional. The topic of the search, must be one of \"general\", \"news\". Default is \"general\".",
                },
                "days": {
                    "type": "integer",
                    "description": "Optional. The number of days back from the current date to include in the search results. This only applies when topic is \"news\".",
                },
                "time_range": {
                    "type": "string",
                    "description": "Optional. The time range back from the current date to include in the search results. Must be one of \"day\", \"week\", \"month\", \"year\".",
                },
                "start_date": {
                    "type": "string",
                    "description": "Optional. The start date for the search results in the format YYYY-MM-DD.",
                },
                "end_date": {
                    "type": "string",
                    "description": "Optional. The end date for the search results in the format YYYY-MM-DD.",
                },
            },
            "required": ["query"],
        }
    )


@builtin_tool(config=_TAVILY_WEB_SEARCH_TOOL_CONFIG)
@dataclass
class TavilyExtractWebPageTool(FunctionTool):
    name: str = "tavily_extract_web_page"
    description: str = "Extract the content of a web page using Tavily."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Required. A URL to extract content from.",
                },
                "extract_depth": {
                    "type": "string",
                    "description": "Optional. The depth of the extraction, must be one of \"basic\", \"advanced\". Default is \"basic\".",
                },
            },
            "required": ["url"],
        }
    )


@builtin_tool(config=_BOCHA_WEB_SEARCH_TOOL_CONFIG)
@dataclass
class BochaWebSearchTool(FunctionTool):
    name: str = "web_search_bocha"
    description: str = (
        "A web search tool based on Bocha Search API, used to retrieve web pages related to the user's query."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Required. User's search query.",
                },
                "freshness": {
                    "type": "string",
                    "description": "Optional. Time range of the search. Recommended value is \"noLimit\".",
                },
                "summary": {
                    "type": "boolean",
                    "description": "Optional. Whether to include a text summary for each search result.",
                },
                "include": {
                    "type": "string",
                    "description": "Optional. Domains to include in the search, separated by | or ,.",
                },
                "exclude": {
                    "type": "string",
                    "description": "Optional. Domains to exclude from the search, separated by | or ,.",
                },
                "count": {
                    "type": "integer",
                    "description": "Optional. Number of search results to return. Range: 1-50.",
                },
            },
            "required": ["query"],
        }
    )


@builtin_tool(config=_BRAVE_WEB_SEARCH_TOOL_CONFIG)
@dataclass
class BraveWebSearchTool(FunctionTool):
    name: str = "web_search_brave"
    description: str = "A web search tool based on Brave Search API."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Required. Search query."},
                "count": {
                    "type": "integer",
                    "description": "Optional. Number of results to return. Range: 1-20.",
                },
                "country": {
                    "type": "string",
                    "description": "Optional. Country code for region-specific results, for example \"US\" or \"CN\".",
                },
                "search_lang": {
                    "type": "string",
                    "description": "Optional. Brave language code, for example \"zh-hans\" or \"en\".",
                },
                "freshness": {
                    "type": "string",
                    "description": "Optional. One of \"day\", \"week\", \"month\", \"year\".",
                },
            },
            "required": ["query"],
        }
    )


@builtin_tool(config=_FIRECRAWL_WEB_SEARCH_TOOL_CONFIG)
@dataclass
class FirecrawlWebSearchTool(FunctionTool):
    name: str = "web_search_firecrawl"
    description: str = (
        "A web search tool based on Firecrawl Search API, used to retrieve web pages related to the user's query."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Required. Search query."},
                "limit": {
                    "type": "integer",
                    "description": "Optional. Number of results to return. Range: 1-100. Default is 5.",
                },
                "location": {
                    "type": "string",
                    "description": "Optional. Geographic location for search results.",
                },
                "country": {
                    "type": "string",
                    "description": "Optional. Country code for search results, for example \"US\" or \"CN\".",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Optional. Request timeout in milliseconds.",
                },
            },
            "required": ["query"],
        }
    )


@builtin_tool(config=_FIRECRAWL_WEB_SEARCH_TOOL_CONFIG)
@dataclass
class FirecrawlExtractWebPageTool(FunctionTool):
    name: str = "firecrawl_extract_web_page"
    description: str = "Extract the content of a web page using Firecrawl."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Required. A URL to extract content from.",
                },
                "format": {
                    "type": "string",
                    "description": "Optional. Output format, one of \"markdown\", \"html\", \"rawHtml\", \"summary\". Default is \"markdown\".",
                },
                "only_main_content": {
                    "type": "boolean",
                    "description": "Optional. Whether to extract only the main page content. Default is true.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Optional. Request timeout in milliseconds.",
                },
                "max_age": {
                    "type": "integer",
                    "description": "Optional. Maximum cache age in milliseconds.",
                },
            },
            "required": ["url"],
        }
    )


@builtin_tool(config=_BAIDU_WEB_SEARCH_TOOL_CONFIG)
@dataclass
class BaiduWebSearchTool(FunctionTool):
    name: str = "web_search_baidu"
    description: str = (
        "A web search tool based on Baidu AI Search. Use this for real-time web retrieval when Baidu AI Search is configured."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Required. Search query."},
                "top_k": {
                    "type": "integer",
                    "description": "Optional. Number of web results to return. Maximum 50. Default is 10.",
                },
                "search_recency_filter": {
                    "type": "string",
                    "description": "Optional. One of \"week\", \"month\", \"semiyear\", \"year\".",
                },
                "site": {
                    "type": "string",
                    "description": "Optional. Restrict search to specific sites, separated by commas.",
                },
            },
            "required": ["query"],
        }
    )


@builtin_tool(config=_EXA_WEB_SEARCH_TOOL_CONFIG)
@dataclass
class ExaWebSearchTool(FunctionTool):
    name: str = "web_search_exa"
    description: str = (
        "A web search tool powered by Exa, an AI-native search engine. "
        "Supports keyword and semantic search with domain, date, and category filters."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Required. Search query."},
                "num_results": {
                    "type": "integer",
                    "description": "Optional. Number of results to return. Default is 10.",
                },
                "type": {
                    "type": "string",
                    "description": "Optional. Search type. One of \"auto\", \"keyword\", \"neural\". Default is \"auto\".",
                },
                "category": {
                    "type": "string",
                    "description": "Optional. Category filter. One of \"company\", \"research paper\", \"news\", \"github\", \"tweet\", \"personal site\", \"pdf\", \"linkedin profile\".",
                },
                "include_domains": {
                    "type": "string",
                    "description": "Optional. Comma-separated domains to restrict results to.",
                },
                "exclude_domains": {
                    "type": "string",
                    "description": "Optional. Comma-separated domains to exclude from results.",
                },
                "start_published_date": {
                    "type": "string",
                    "description": "Optional. Start date filter in ISO 8601 format (e.g. 2024-01-01T00:00:00.000Z).",
                },
                "end_published_date": {
                    "type": "string",
                    "description": "Optional. End date filter in ISO 8601 format.",
                },
            },
            "required": ["query"],
        }
    )


@builtin_tool(config=_EXA_WEB_SEARCH_TOOL_CONFIG)
@dataclass
class ExaGetContentsTool(FunctionTool):
    name: str = "exa_get_contents"
    description: str = "Extract the content of a web page using Exa."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Required. A URL to extract content from.",
                },
                "max_characters": {
                    "type": "integer",
                    "description": "Optional. Maximum number of characters to return. Default is 3000.",
                },
            },
            "required": ["url"],
        }
    )


def normalize_legacy_web_search_config(cfg) -> None:
    """规范化旧版 web_search 配置（对齐本体 normalize_legacy_web_search_config）。

    与本体一致的两条迁移规则（纯内存修改）：
    - ``websearch_provider == "default"`` 且 ``web_search`` 开启时关闭
      web_search（default 提供方已不再支持）；
    - 各家 API key 为字符串时转成 ``[value]`` 列表（空串转空列表）。

    本体在改动后调用 ``cfg.save_config()``；SDK 同样尝试调用（宿主
    AstrBotConfig 支持时生效），失败仅告警不抛错。
    """
    provider_settings = cfg.get("provider_settings") if isinstance(cfg, dict) else None
    if not provider_settings:
        return

    changed = False
    if (
        provider_settings.get("websearch_provider") == "default"
        and provider_settings.get("web_search", False)
    ):
        provider_settings["web_search"] = False
        changed = True
        logger.warning(
            "The default websearch provider is no longer supported. "
            "Web search has been disabled and the config was saved.",
        )

    for setting_name in (
        "websearch_tavily_key",
        "websearch_bocha_key",
        "websearch_brave_key",
        "websearch_firecrawl_key",
        "websearch_exa_key",
    ):
        value = provider_settings.get(setting_name)
        if isinstance(value, str):
            provider_settings[setting_name] = [value] if value else []
            changed = True

    if changed:
        try:
            cfg.save_config()
        except Exception as exc:
            logger.warning("save_config failed while normalizing web search config: %s", exc)


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