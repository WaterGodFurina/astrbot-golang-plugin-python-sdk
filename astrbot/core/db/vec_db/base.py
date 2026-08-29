"""向量数据库薄壳（Go 宿主兼容运行时，对齐本体 db/vec_db/base）。

宿主向量库由 Go 原生实现（nanovec），插件侧无需直接操作向量存储；本包
提供 `Result` / `BaseVecDB` 接口薄壳，保证插件 import 与类型标注可用。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Result:
    """检索结果（对齐本体 base.Result）。"""

    similarity: float = 0.0
    data: dict = field(default_factory=dict)


class BaseVecDB:
    """向量库基类（SDK 降级：全部方法 no-op / 返回空，宿主原生实现）。"""

    async def initialize(self) -> None:
        """初始化向量库（SDK 降级：no-op）。"""

    @abc.abstractmethod
    async def insert(
        self,
        content: str,
        metadata: dict | None = None,
        id: str | None = None,
    ) -> int:
        """插入一条文本与对应向量。"""
        return 0

    @abc.abstractmethod
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
        """批量插入文本与向量。"""
        return 0

    @abc.abstractmethod
    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        fetch_k: int = 20,
        rerank: bool = False,
        metadata_filters: dict | None = None,
    ) -> list[Result]:
        """搜索最相似文档。"""
        return []

    @abc.abstractmethod
    async def delete(self, doc_id: str) -> bool:
        """删除指定文档。"""
        return True

    @abc.abstractmethod
    async def close(self) -> None:
        """关闭向量库。"""


__all__ = ["BaseVecDB", "Result"]