"""网络错误处理工具（对齐 Python 本体 astrbot.core.utils.network_utils）。

httpx 非本 SDK 的强制依赖，故采用可选导入：未安装时仅按常见 Python
网络异常判断。
"""
import logging

logger = logging.getLogger("astrbot")


def is_connection_error(exc: BaseException) -> bool:
    """判断异常是否为连接/网络类错误（沿 __cause__ 链递归检查）。

    httpx 未安装时退化为仅检查标准库网络异常（TimeoutError/OSError/
    ConnectionError）。
    """
    try:
        import httpx
    except ImportError:
        httpx = None

    if httpx is not None and isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.NetworkError,
            httpx.ProxyError,
            httpx.RequestError,
        ),
    ):
        return True

    if isinstance(exc, (TimeoutError, OSError, ConnectionError)):
        return True

    cause = getattr(exc, "__cause__", None)
    if cause is not None and cause is not exc:
        return is_connection_error(cause)

    return False


def log_connection_failure(
    provider_label: str,
    error: Exception,
    proxy: str | None = None,
) -> None:
    """记录连接失败日志（含代理信息）。

    proxy 为空时回退检查 http_proxy/https_proxy 环境变量。
    """
    import os

    error_type = type(error).__name__

    effective_proxy = proxy
    if not effective_proxy:
        effective_proxy = os.environ.get(
            "http_proxy", os.environ.get("https_proxy", "")
        )

    if effective_proxy:
        logger.error(
            f"[{provider_label}] Network or proxy connection failed ({error_type}). "
            f"Proxy: {effective_proxy}; error: {error}"
        )
    else:
        logger.error(
            f"[{provider_label}] Network connection failed ({error_type}): {error}"
        )