"""computer_tools.fs（Go 宿主兼容运行时，对齐本体 computer_tools/fs.py）。

SDK 薄壳：文件工具类定义对齐本体，真实读写由宿主 sandbox 原生执行。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from astrbot.core.agent.tool import FunctionTool


@dataclass
class FileReadTool(FunctionTool):
    name: str = "astrbot_file_read_tool"
    description: str = (
        "read file content. Supports text, image, and PDF (text extraction), docx and epub files."
    )
    parameters: dict = field(default_factory=dict)


@dataclass
class FileWriteTool(FunctionTool):
    name: str = "astrbot_file_write_tool"
    description: str = "Write UTF-8 text content to a file."
    parameters: dict = field(default_factory=dict)


@dataclass
class FileEditTool(FunctionTool):
    name: str = "astrbot_file_edit_tool"
    description: str = "Editing files."
    parameters: dict = field(default_factory=dict)


@dataclass
class GrepTool(FunctionTool):
    name: str = "astrbot_grep_tool"
    description: str = "Search and read file contents using ripgrep."
    parameters: dict = field(default_factory=dict)


@dataclass
class FileUploadTool(FunctionTool):
    name: str = "astrbot_upload_file"
    description: str = "Upload a host file to the sandbox workspace."
    parameters: dict = field(default_factory=dict)


@dataclass
class FileDownloadTool(FunctionTool):
    name: str = "astrbot_download_file"
    description: str = "Download a sandbox file back to the host."
    parameters: dict = field(default_factory=dict)


# 本体 fs.py 的 FileDownloadTool._remote_basename 等辅助（薄壳保留别名位）。
_remote_basename = staticmethod(lambda path: str(path).replace("\\", "/").split("/")[-1])


__all__ = [
    "FileDownloadTool",
    "FileEditTool",
    "FileReadTool",
    "FileUploadTool",
    "FileWriteTool",
    "GrepTool",
    "_remote_basename",
]