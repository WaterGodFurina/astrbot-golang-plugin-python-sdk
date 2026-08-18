"""astrbot.core.db —— 数据库实体薄封装（Go 宿主兼容运行时）。

Python 本体 `astrbot.core.db` 是 SQLModel 数据库实体层；Go 宿主的数据
持久化在宿主侧完成，插件一般不需要操作数据库。这里仅提供插件常用
import 路径（如 `from astrbot.core.db.po import Personality`）所需的
纯数据结构，避免插件导入失败。
"""
from astrbot.core.db.po import Persona, Personality

__all__ = ["Persona", "Personality"]
