"""知识库管理器（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.knowledge_base.kb_mgr.KnowledgeBaseManager`。
SDK 降级实现：仅保证 import 与属性可访问，查询返回空结果。
"""


class KnowledgeBaseManager:
    """知识库管理器（SDK 降级）。"""

    def __init__(self) -> None:
        pass

    async def get_index(self, index_id: str | None = None):
        """获取知识库索引（SDK 降级：返回 None）。"""
        return None

    async def query(self, *args, **kwargs) -> list:
        """查询知识库（SDK 降级：返回空列表）。"""
        return []

    async def add_document(self, *args, **kwargs) -> None:
        """添加文档（SDK 降级：no-op）。"""

    async def remove_document(self, *args, **kwargs) -> None:
        """移除文档（SDK 降级：no-op）。"""
