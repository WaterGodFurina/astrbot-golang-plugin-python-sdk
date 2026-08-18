"""astrbot.api.util：与 Python 本体对齐的工具命名空间。

会话等待（session_waiter）实现见 `astrbot.core.utils.session_waiter`。
注意：Go 宿主不会自动把“下一条消息”注入等待中的会话，`register_wait()`
默认会挂起至超时（抛 TimeoutError，插件普遍已处理）；需要手动喂入消息时
调用 `await SessionWaiter.trigger(session_id, event)`。
"""
from astrbot.core.utils.session_waiter import (
    SessionController,
    SessionWaiter,
    session_waiter,
)

__all__ = ["SessionController", "SessionWaiter", "session_waiter"]
