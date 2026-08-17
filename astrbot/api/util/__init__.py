"""astrbot.api.util：与 Python 本体对齐的工具命名空间。

Go 宿主兼容运行时暂未实现 session_waiter（会话等待）——提供可 import 的
占位实现，插件能正常加载，调用时抛清晰的 NotImplementedError 而非
ImportError。
"""

__all__ = ["SessionController", "SessionWaiter", "session_waiter"]


class SessionWaiter:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "Go 宿主暂不支持 SessionWaiter（会话等待需宿主推送下一条同会话消息）"
        )


class SessionController:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "Go 宿主暂不支持 SessionController（会话等待需宿主推送下一条同会话消息）"
        )


def session_waiter(*args, **kwargs):
    raise NotImplementedError(
        "Go 宿主暂不支持 session_waiter（会话等待需宿主推送下一条同会话消息）"
    )
