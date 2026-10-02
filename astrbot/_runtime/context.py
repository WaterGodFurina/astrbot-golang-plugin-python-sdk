"""当前 PluginSession 的 ContextVar 与统一访问接口。

共享 Runtime（python-shared）中，多个插件共用一个 Python 解释器。为让
「当前执行上下文属于哪个插件」贯穿整个调用链（RPC → handler → 异步任务 →
嵌套回调），用 contextvars.ContextVar 保存当前 PluginSession：

    token = set_current_session(session)
    try:
        ...
    finally:
        reset_current_session(token)

**必须 set/reset 成对**，否则异步任务 / 事件处理链之间会上下文泄漏。

隔离边界：ContextVar 只解决「逻辑级插件归属」，不能隔离 sys.modules /
sys.path / 模块级全局 / C 扩展状态——那些需要进程级隔离（python-grpc /
python-isolated）。本层不判断「为什么没有 session」（python-grpc / 启动阶段
/ 测试环境都一样），只表达：

    有 session → SessionScoped
    没有 session（None）→ Legacy Global
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from astrbot._runtime.registry import PluginSession


# 唯一事实来源：当前插件的 PluginSession。
# None = 单插件进程 / 无共享 Runtime 上下文 → 业务代码回退到模块级全局。
_current_session: contextvars.ContextVar["PluginSession | None"] = contextvars.ContextVar(
    "astrbot_current_plugin_session", default=None
)


def set_current_session(session: "PluginSession | None") -> contextvars.Token:
    """设置当前插件上下文，返回 Token 供 reset。必须与 reset 成对使用。"""
    return _current_session.set(session)


def reset_current_session(token: contextvars.Token) -> None:
    """恢复此前的插件上下文（与 set_current_session 成对）。"""
    _current_session.reset(token)


def get_current_session() -> "PluginSession | None":
    """返回当前 PluginSession；无共享 Runtime 上下文时为 None（回退全局）。"""
    return _current_session.get()


def require_current_session() -> "PluginSession":
    """返回当前 PluginSession；不存在（单插件进程）时抛 RuntimeError。"""
    session = _current_session.get()
    if session is None:
        raise RuntimeError(
            "require_current_session: 当前不在共享 Runtime 插件上下文中"
            "（无 current_session；单插件进程应使用全局 registry）"
        )
    return session


def get_current_plugin_id() -> str:
    """当前插件 id，由唯一 session 派生（不单独维护第二份 ContextVar，
    避免 session=A、plugin_id=B 的状态分裂）。"""
    session = _current_session.get()
    return session.plugin_id if session is not None else ""


def has_current_session() -> bool:
    return _current_session.get() is not None


def resolve_session_scoped(attr_name: str, fallback: Any) -> Any:
    """从当前 session 取 plugin-scoped 状态；无 session 或字段未设置时回退
    fallback（模块级全局）。

    - 有 session 且字段非 None → session 的独立实例（多插件隔离）
    - 有 session 但字段为 None（尚未初始化）→ fallback（与单插件一致）
    - 无 session（python-grpc）→ fallback
    """
    session = _current_session.get()
    if session is None:
        return fallback
    value = getattr(session, attr_name, None)
    if value is None:
        return fallback
    return value
