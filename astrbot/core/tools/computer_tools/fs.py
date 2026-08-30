"""computer_tools.fs（Go 宿主兼容运行时，对齐本体 computer_tools/fs.py）。

SDK 薄壳：文件工具类的 name / description / parameters（schema）与本体一致，
真实读写由宿主 sandbox 原生执行。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import FunctionTool


@dataclass
class FileReadTool(FunctionTool):
    name: str = "astrbot_file_read_tool"
    description: str = (
        "read file content. Supports text, image, and PDF (text extraction), docx and epub files."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path of the file to read. If relative, will be in workspace root.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Optional line offset to start reading from. 0-based index.",
                    "minimum": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": "Optional maximum number of lines to read.",
                    "minimum": 1,
                },
            },
            "required": ["path"],
        }
    )


@dataclass
class FileWriteTool(FunctionTool):
    name: str = "astrbot_file_write_tool"
    description: str = "Write UTF-8 text content to a file."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path of the file to write. If relative, will be in workspace root.",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file",
                },
            },
            "required": ["path", "content"],
        }
    )


@dataclass
class FileEditTool(FunctionTool):
    name: str = "astrbot_file_edit_tool"
    description: str = "Editing files."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path of the file to edit. If relative, will be in workspace root.",
                },
                "old": {
                    "type": "string",
                    "description": "The exact old text to replace.",
                },
                "new": {
                    "type": "string",
                    "description": "The replacement text.",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Whether to replace all matches. Defaults to false.",
                },
            },
            "required": ["path", "old", "new"],
        }
    )


@dataclass
class GrepTool(FunctionTool):
    name: str = "astrbot_grep_tool"
    description: str = "Search and read file contents using ripgrep."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The expression pattern to search for in file contents.",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in (rg PATH). If relative, will be in workspace root.",
                },
                "glob": {
                    "type": "string",
                    "description": "Optional glob filter such as `*.py`, `*.{ts,tsx}`.",
                },
                "-A": {
                    "type": "integer",
                    "description": "Number of trailing context lines to include after each match.",
                    "minimum": 0,
                },
                "-B": {
                    "type": "integer",
                    "description": "Number of leading context lines to include before each match.",
                    "minimum": 0,
                },
                "-C": {
                    "type": "integer",
                    "description": "Number of leading and trailing context lines to include around each match.",
                    "minimum": 0,
                },
                "result_limit": {
                    "type": "integer",
                    "description": "Maximum number of result groups returned by the tool. Defaults to 100.",
                    "minimum": 1,
                },
            },
            "required": ["pattern"],
        }
    )


@dataclass
class FileUploadTool(FunctionTool):
    name: str = "astrbot_upload_file"
    description: str = (
        "Transfer a file FROM the host machine INTO the sandbox so that sandbox code can access it. "
        "Use this when the user sends/attaches a file and you need to process it inside the sandbox. "
        "The local_path must point to an existing file on the host filesystem."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "local_path": {
                    "type": "string",
                    "description": "Absolute path to the file on the host filesystem that will be copied into the sandbox.",
                },
            },
            "required": ["local_path"],
        }
    )


@dataclass
class FileDownloadTool(FunctionTool):
    name: str = "astrbot_download_file"
    description: str = (
        "Transfer a file FROM the sandbox OUT to the host and optionally send it to the user. "
        "Use this ONLY when the user asks to retrieve/export a file that was created or modified inside the sandbox."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "remote_path": {
                    "type": "string",
                    "description": "Path of the file inside the sandbox to copy out to the host.",
                },
                "also_send_to_user": {
                    "type": "boolean",
                    "description": "Whether to also send the downloaded file to the user via message. Defaults to true.",
                },
            },
            "required": ["remote_path"],
        }
    )


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