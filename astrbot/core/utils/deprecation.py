"""轻量 `deprecated` 装饰器（对齐 Python 本体使用的 `deprecated` 库常用签名）。

Go 宿主兼容运行时不依赖第三方 `deprecated` 包，这里提供等价行为的轻量
实现，避免插件 `from astrbot.core.utils.deprecation import deprecated`
导入失败。支持三种用法：

- `@deprecated`（裸用，直接装饰函数/类）；
- `@deprecated("弃用原因")` 或 `@deprecated(reason="...", version="...")`；
- 被装饰对象为类时，在其被实例化时发出 `DeprecationWarning`。

被装饰的函数/方法/类在调用（实例化）时发出告警后透传原行为，不改变
原对象的功能与返回值。
"""
from __future__ import annotations

import functools
import inspect
import warnings
from typing import Any, Callable, TypeVar

_F = TypeVar("_F")


def _make_message(
    obj: Any,
    reason: str,
    version: str | None,
    action: str | None,
) -> str:
    """构造告警文案：{对象限定名} (since v{version}) is deprecated[: {reason}]。"""
    name = getattr(obj, "__qualname__", None) or getattr(obj, "__name__", None) or repr(obj)
    prefix = str(name) if name else "This callable"
    if version:
        prefix = f"{prefix} (since v{version})"
    suffix = f": {reason}" if reason else ""
    if action:
        suffix = f"{suffix}. {action}"
    return f"{prefix} is deprecated{suffix}"


def deprecated(
    reason: str = "",
    version: str | None = None,
    action: str | None = None,
    category: type[Warning] = DeprecationWarning,
    stacklevel: int = 2,
) -> Callable[[_F], _F]:
    """轻量 `deprecated` 装饰器。

    Args:
        reason: 弃用原因说明。
        version: 弃用起始版本（可选，仅用于提示信息）。
        action: 弃用后的替代指引（可选，仅用于提示信息）。
        category: 告警类别，默认 DeprecationWarning。
        stacklevel: warnings.warn 的栈层级。
    """

    def decorator(obj: _F) -> _F:
        message = _make_message(obj, reason, version, action)

        if inspect.isclass(obj):
            # 类：包装其 __init__，实例化时发出告警（对齐 deprecated 库行为）。
            orig_init = obj.__init__
            if orig_init is object.__init__:

                def _wrapped_init(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[misc]
                    warnings.warn(message, category, stacklevel=stacklevel)
                    super(type(self), self).__init__(*args, **kwargs)

            else:

                @functools.wraps(orig_init)  # type: ignore[arg-type]
                def _wrapped_init(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[misc]
                    warnings.warn(message, category, stacklevel=stacklevel)
                    orig_init(self, *args, **kwargs)  # type: ignore[misc]

            obj.__init__ = _wrapped_init  # type: ignore[method-assign]
            return obj

        if inspect.iscoroutinefunction(obj):

            @functools.wraps(obj)
            async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
                warnings.warn(message, category, stacklevel=stacklevel)
                return await obj(*args, **kwargs)  # type: ignore[misc]

            return _async_wrapper  # type: ignore[return-value]

        @functools.wraps(obj)
        def _wrapper(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
            warnings.warn(message, category, stacklevel=stacklevel)
            return obj(*args, **kwargs)  # type: ignore[misc]

        return _wrapper  # type: ignore[return-value]

    # 裸用：`@deprecated` 直接作用于被装饰对象（此时 reason 是函数/类本身）。
    if callable(reason) and not isinstance(reason, str):
        target = reason
        return decorator(target)
    return decorator