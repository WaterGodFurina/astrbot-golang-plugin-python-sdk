"""持久化实体定义（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.db.po` 中插件常用的 `Personality`
（TypedDict）与 `Persona`（本体内为 SQLModel 表）的字段定义。
本模块为纯数据结构，不含数据库访问逻辑——宿主数据由 Go 侧管理。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class Personality(TypedDict):
    """LLM 人格类（字段对齐 Python 本体 po.py 的 Personality）。

    在 v4.0.0 版本及之后，推荐使用 Persona 类。mood_imitation_dialogs
    字段已被废弃，这里保留以兼容旧插件的类型标注。
    """

    prompt: str
    name: str
    begin_dialogs: list[str]
    mood_imitation_dialogs: list[str]
    """情感模拟对话预设。在 v4.0.0 版本及之后，已被废弃。"""
    tools: list[str] | None
    """工具列表。None 表示使用所有工具，空列表表示不使用任何工具"""
    skills: list[str] | None
    """Skills 列表。None 表示使用所有 Skills，空列表表示不使用任何 Skills"""
    custom_error_message: str | None
    """可选的人格自定义报错回复信息。配置后将优先发送给最终用户。"""

    # cache
    _begin_dialogs_processed: list[dict]
    _mood_imitation_dialogs_processed: str


@dataclass
class Persona:
    """人格数据（纯数据结构，字段对齐 Python 本体的 Persona 表）。

    Go 宿主侧的人格由宿主持久化管理，插件仅经此类型做类型标注 /
    数据搬运，不涉及 SQLModel 表结构。
    """

    persona_id: str = ""
    """人格唯一标识"""
    name: str = ""
    """人格名称"""
    system_prompt: str = ""
    """人格的系统提示词"""
    begin_dialogs: list | None = None
    """一组用于开场对话的字符串列表"""
    tools: list | None = None
    """None 表示使用所有工具，空列表表示不使用任何工具，否则为工具名列表"""
    skills: list | None = None
    """None 表示使用所有 Skills，空列表表示不使用任何 Skills，否则为 Skill 名列表"""
    custom_error_message: str | None = None
    """可选的自定义报错回复信息，配置后优先发送给最终用户"""
    folder_id: str | None = None
    """所属文件夹 ID，NULL 表示在根目录"""
    sort_order: int = 0
    """排序顺序"""
