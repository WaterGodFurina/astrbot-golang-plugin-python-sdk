"""SessionScopedProxy：把模块级全局 registry 变成「session 优先、全局兜底」。

兼容桥原理（对应方案文档板块 4）：

    有 current PluginSession
        → 读写 session 内持有的独立 registry 实例
    没有 current PluginSession（python-grpc 单插件进程）
        → 回退到模块级全局 registry

这样旧插件代码保持原样：

    from astrbot.core.star.star import star_map, star_registry
    star_map["k"] = v            # 路由到 session 或全局
    star_handlers_registry.append(h)

插件作者无需感知 PluginSession / ContextVar（零侵入）。

本代理只解决「逻辑级插件归属」；不伪造 sys.modules / sys.path 等解释器级
隔离（见方案文档第 8/17 节）。
"""

from __future__ import annotations

from typing import Any, Callable, Iterator


class SessionScopedProxy:
    """把 `resolve()` 解析出的当前对象（session 或全局）的访问透明委托。

    resolve() 返回 None 时（理论上不发生）回退到 fallback。
    """

    __slots__ = ("_resolve", "_fallback", "_name")

    def __init__(self, resolve: Callable[[], Any], fallback: Any, name: str = ""):
        self._resolve = resolve
        self._fallback = fallback
        self._name = name or type(fallback).__name__

    def _current(self) -> Any:
        obj = self._resolve()
        return obj if obj is not None else self._fallback

    # ---- 属性访问 ----
    def __getattr__(self, item: str) -> Any:
        # __slots__ 下 _resolve 存在；此分支只处理代理对象的普通属性委托
        obj = self._current()
        return getattr(obj, item)

    def __setattr__(self, key: str, value: Any) -> None:
        if key in ("_resolve", "_fallback", "_name"):
            object.__setattr__(self, key, value)
            return
        setattr(self._current(), key, value)

    # ---- 映射语义（dict 类 registry：star_map）----
    def __getitem__(self, key: Any) -> Any:
        return self._current()[key]

    def __setitem__(self, key: Any, value: Any) -> None:
        self._current()[key] = value

    def __delitem__(self, key: Any) -> None:
        del self._current()[key]

    def __contains__(self, key: Any) -> bool:
        return key in self._current()

    def get(self, key: Any, default: Any = None) -> Any:
        return self._current().get(key, default)

    def __iter__(self) -> Iterator:
        return iter(self._current())

    def __len__(self) -> int:
        return len(self._current())

    # ---- 方法调用 / 通用委托 ----
    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._current()(*args, **kwargs)

    def __repr__(self) -> str:
        return f"<SessionScopedProxy:{self._name} -> {self._current()!r}>"
