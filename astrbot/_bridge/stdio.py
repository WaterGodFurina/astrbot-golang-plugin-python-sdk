"""go-plugin GRPCStdio（插件侧 server 端，日志流镜像）。

go-plugin 宿主在握手完成后自动连接插件的 `plugin.GRPCStdio/StreamStdio`
（见 hashicorp/go-plugin grpc_client.go newGRPCStdioClient）。之前 Python
bridge 未实现该服务 → 宿主 stdio client 收到 UNIMPLEMENTED "Method not
found"，日志流同步失效。

实现：StreamStdio 保持连接并返回空流（host 侧 Reader 拿不到 stdio 数据，
但插件 stdout/stderr 实际已由宿主经子进程管道直读——runtime.go 里
`cfg.Stderr = stderrParser`；此处只补服务存在性，避免 Method not found）。
"""
from __future__ import annotations

import logging
import time

import grpc

from .gen import goplugin_pb2, goplugin_pb2_grpc

logger = logging.getLogger("astrbot.stdio")


class GRPCStdioServicer(goplugin_pb2_grpc.GRPCStdioServicer):
    """Go-plugin stdout/stderr 镜像服务（保活空流实现）。

    插件进程的 stdout/stderr 已由宿主经 go-plugin 子进程管道直接读取
    （runtime.go cfg.Stderr = stderrParser / cmd.Stdout）；本服务仅需
    可被连接（消除 Method not found），保持流打开即可。
    """

    def StreamStdio(self, request, context):  # noqa: N802
        del request  # 宿主用 google.protobuf.Empty 请求，SDK 侧忽略
        # unary_stream 响应必须是 generator：保活流周期性 yield 空数据块，
        # 保持宿主 stdioClient.Recv() 活跃；连接断开时 GeneratorExit 退出。
        try:
            while True:
                if not context.is_active():
                    return
                yield goplugin_pb2.StdioData()
                time.sleep(1.0)
        except GeneratorExit:
            raise
        except Exception:
            return


def register_grpc_stdio(server: grpc.Server) -> None:
    """注册 stdio 服务到插件 gRPC server。"""
    goplugin_pb2_grpc.add_GRPCStdioServicer_to_server(GRPCStdioServicer(), server)


__all__ = ["GRPCStdioServicer", "register_grpc_stdio"]