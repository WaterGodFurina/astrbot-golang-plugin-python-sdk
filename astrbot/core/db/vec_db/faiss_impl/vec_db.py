"""Faiss 向量数据库（Go 宿主兼容运行时，对齐本体 db/vec_db/faiss_impl/vec_db）。

宿主向量库用 Go nanovec 原生实现，Python 侧无 faiss；`FaissVecDB` 为
接口薄壳（继承 BaseVecDB），保证插件按本体路径
`from astrbot.core.db.vec_db.faiss_impl import FaissVecDB` 或
`from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB`
可 import 与类型标注。数据操作降级为 no-op / 返回空，由宿主原生执行。
"""
from __future__ import annotations

from typing import Any

from astrbot.core.db.vec_db.base import BaseVecDB, Result  # noqa: F401


class FaissVecDB(BaseVecDB):
    """Faiss 向量库薄壳（宿主 Go nanovec 原生实现，本类仅占位）。"""

    def __init__(
        self,
        doc_store_path: str,
        index_store_path: str,
        embedding_provider: Any = None,
        rerank_provider: Any = None,
    ) -> None:
        """构造签名对齐本体（本体 vec_db.py:18-34）。

        本体同时构建 DocumentStorage/EmbeddingStorage 实例（并调用
        embedding_provider.get_dim() 计算索引维度）；SDK 宿主侧向量库由
        宿主知识库管理器持有，这里仅以 None 占位同名属性，避免插件访问
        `vec_db.document_storage` / `vec_db.embedding_storage` 时抛
        AttributeError（真实操作由宿主 nanovec 完成）。
        """
        self.doc_store_path = doc_store_path
        self.index_store_path = index_store_path
        self.embedding_provider = embedding_provider
        self.rerank_provider = rerank_provider
        # 同名属性占位（本体为 DocumentStorage/EmbeddingStorage 实例）
        self.document_storage = None
        self.embedding_storage = None

    async def initialize(self) -> None:
        """初始化（SDK 降级：no-op，宿主 nanovec 原生初始化）。"""

    async def insert(
        self,
        content: str,
        metadata: dict | None = None,
        id: str | None = None,
    ) -> int:
        """插入一条文本与向量（SDK 降级：返回 0，宿主原生执行）。"""
        return 0

    async def insert_batch(
        self,
        contents: list[str],
        metadatas: list[dict] | None = None,
        ids: list[str] | None = None,
        batch_size: int = 32,
        tasks_limit: int = 3,
        max_retries: int = 3,
        progress_callback=None,
        embedding_contents: list[str] | None = None,
    ) -> list[int]:
        """批量插入文本与向量（SDK 降级：返回 []，宿主原生执行）。

        签名与本体一致（本体 vec_db.py:59-69）：返回本次写入的内部 int
        ID 列表（list[int]），降级时返回空列表。
        """
        return []

    async def retrieve(
        self,
        query: str,
        k: int = 5,
        fetch_k: int = 20,
        rerank: bool = False,
        metadata_filters: dict | None = None,
    ) -> list[Result]:
        """搜索最相似文档（SDK 降级：返回 []，宿主原生执行）。

        参数名与本体一致（本体 vec_db.py:215-222）：第二参为 ``k``（返回
        的最相似文档数量），``fetch_k`` 为按 metadata 过滤前从索引取回的
        数量，``rerank`` 需构造时提供 rerank_provider（未提供且 rerank 为
        True 时不抛异常，与本体语义一致）。
        """
        return []

    async def delete(self, doc_id: str) -> None:
        """删除一条文档块 chunk（SDK 降级：no-op，宿主原生执行）。

        返回形态对齐本体（本体 vec_db.py:313-323）：返回 None。
        """

    async def count_documents(self, metadata_filter: dict | None = None) -> int:
        """计算文档数量（SDK 降级：返回 0，宿主 nanovec 原生统计）。"""
        return 0

    async def delete_documents(self, metadata_filters: dict) -> None:
        """按元数据过滤器删除文档（SDK 降级：no-op，宿主原生执行）。"""

    async def close(self) -> None:
        """关闭向量库（SDK 降级：no-op）。"""


__all__ = ["BaseVecDB", "FaissVecDB", "Result"]
