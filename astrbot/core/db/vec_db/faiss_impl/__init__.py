"""faiss_impl 包（Go 宿主兼容运行时，对齐本体 faiss_impl/__init__.py 导出面）。

本体 `astrbot.core.db.vec_db.faiss_impl.__init__` 惰性导出 FaissVecDB；
SDK 为轻量薄壳，直接 re-export，同时保证本体代码常用的深层路径
`...faiss_impl.vec_db import FaissVecDB` 可用（见 vec_db.py）。
"""
from astrbot.core.db.vec_db.faiss_impl.vec_db import (
    BaseVecDB,
    FaissVecDB,
    Result,
)

__all__ = ["BaseVecDB", "FaissVecDB", "Result"]
