"""Web 搜索工具（Go 宿主兼容运行时，对齐本体 tools/web_search_tools.py）。

SDK 薄壳：各搜索/网页提取工具类的 name / description / parameters（schema）
均与本体一致，宿主 web_search 引擎原生执行；
`normalize_legacy_web_search_config` 为纯配置规范化纯函数（本地实现）。
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