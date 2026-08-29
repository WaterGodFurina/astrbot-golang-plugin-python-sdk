"""向量数据库（Go 宿主兼容运行时，对齐本体 db/vec_db/faiss_impl）。

宿主向量库用 Go nanovec 原生实现，Python 侧无 faiss；`FaissVecDB` 为
接口薄壳（继承 BaseVecDB），保证插件 `from astrbot.core.db.vec_db.
faiss_impl import FaissVecDB` 可 import 与类型标注。
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
        """构造占位（宿主原生向量库由宿主知识库管理器持有）。"""
        self.doc_store_path = doc_store_path
        self.index_store_path = index_store_path
        self.embedding_provider = embedding_provider
        self.rerank_provider = rerank_provider

    async def initialize(self) -> None:
        """初始化（SDK 降级：no-op，宿主 nanovec 原生初始化）。"""

    async def insert(
        self,
        content: str,
        metadata: dict | None = None,
        id: str | None = None,
    ) -> int:
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
    ) -> int:
        return 0

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        fetch_k: int = 20,
        rerank: bool = False,
        metadata_filters: dict | None = None,
    ) -> list[Result]:
        return []

    async def delete(self, doc_id: str) -> bool:
        return True

    async def close(self) -> None:
        """关闭向量库（SDK 降级：no-op）。"""


__all__ = ["BaseVecDB", "FaissVecDB", "Result"]