"""go-plugin GRPCBroker（插件侧 server 端）。

宿主作为 gRPC client 调插件 server 的 `plugin.GRPCBroker/StartStream`
（bidi stream）。宿主 `Accept(id)` 时把 `ConnInfo{service_id, network,
address}` Send 过来；插件本地 `dial(id)` 从收到的 ConnInfo 中取地址连接。
（经典模式，宿主未启用 mux，无需处理 knock。）
"""
from __future__ import annotations

import logging
import queue
import threading

import grpc

from .gen import goplugin_pb2, goplugin_pb2_grpc

logger = logging.getLogger("astrbot.broker")

DIAL_TIMEOUT = 8.0


class GRPCBrokerServicer(goplugin_pb2_grpc.GRPCBrokerServicer):
    def __init__(self) -> None:
        # service_id -> (network, address)
        self._conns: dict[int, tuple[str, str]] = {}
        # service_id -> channel（dial 缓存，重连/重建时复用，避免 fd 泄漏）
        self._channels: dict[int, grpc.Channel] = {}
        self._cv = threading.Condition()

    def StartStream(self, request_iterator, context):
        # 收到宿主 Accept 发来的 ConnInfo → 存入表，通知 dial 等待者。
        for info in request_iterator:
            with self._cv:
                self._conns[info.service_id] = (info.network, info.address)
                self._cv.notify_all()
            logger.debug(
                f"broker: host offered service_id={info.service_id} {info.network} {info.address}"
            )
        return iter(())

    def dial(self, service_id: int, timeout: float = DIAL_TIMEOUT) -> grpc.Channel:
        # 锁内检查缓存（并发建连时避免各自创建 channel 互相覆盖、泄漏 fd）
        with self._cv:
            cached = self._channels.get(service_id)
            if cached is not None:
                return cached
            # 条件等待（StartStream 会 notify_all），避免 50ms 忙轮询
            if not self._cv.wait_for(lambda: service_id in self._conns, timeout=timeout):
                raise TimeoutError(
                    f"broker: timeout waiting for connection info (service_id={service_id})"
                )
            network, address = self._conns[service_id]
        if network not in ("tcp", "unix"):
            raise ValueError(f"broker: unsupported network {network!r}")
        if network == "unix":
            channel = grpc.insecure_channel(
                f"unix:{address}",
                options=[("grpc.max_receive_message_length", 128 * 1024 * 1024)],
            )
        else:
            channel = grpc.insecure_channel(
                address,
                options=[("grpc.max_receive_message_length", 128 * 1024 * 1024)],
            )
        # 二次检查：并发路径下可能有别的线程已写入，先建者优先，后建者关闭自己的
        with self._cv:
            existing = self._channels.get(service_id)
            if existing is not None:
                channel.close()
                return existing
            self._channels[service_id] = channel
            return channel

    def close(self, service_id: int) -> None:
        """关闭并移除缓存的 channel（host.py 重连/清理路径调用）。"""
        # pop 持锁（与 dial 的二次检查互斥），避免与并发 dial 竞态：
        # 否则 dial 可能刚命中缓存返回 channel 就被这里 pop 并关闭
        with self._cv:
            channel = self._channels.pop(service_id, None)
        if channel is not None:
            try:
                channel.close()
            except Exception as e:
                logger.debug(f"broker: close channel service_id={service_id} 失败: {e}")


_broker_servicer: GRPCBrokerServicer | None = None


def get_broker() -> GRPCBrokerServicer:
    global _broker_servicer
    if _broker_servicer is None:
        _broker_servicer = GRPCBrokerServicer()
    return _broker_servicer
