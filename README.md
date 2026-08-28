# Astrbot Python SDK

AstrBot（`github.com/WaterGodFurina/Astrbot-golang`）的 Python 插件 SDK。

> **命名说明**：本仓库同时存在两套名字——Go module path 为
> `github.com/WaterGodFurina/astrbot-golang-plugin-python-sdk`（宿主 go.mod
> require 并 `go list -m` 定位 SDK 目录），Python distribution 名为
> `astrbot-python-sdk`（pip）。两者是同一个仓库。

Python 插件以 **gRPC 子进程**方式接入 Go 宿主（Astrbot-golang），与 Go 插件
（`WaterGodFurina/Astrbot-go-plugin-sdk`）能力等价：命令 / 过滤器 / 钩子 /
LLM 工具 / LLM 请求钩子 / 结果钩子 / HostService 反向调用。

**关键特性：同一份插件代码双端运行**——本 SDK 提供与 Python AstrBot 本体
**同名同构**的 `astrbot` 包。插件源码零修改：

- 在 **Python AstrBot 本体**中运行：import 到真正的 `astrbot` 包
- 在 **Go 宿主**（Astrbot-golang）中以 gRPC 子进程方式运行：import 到本 SDK
  （`PYTHONPATH` 优先注入本 SDK 目录）

## 插件开发

```python
# main.py
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

@register("hello_plugin", "示例插件", "演示", "1.0.0")
class HelloPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context, config)

    @filter.command("hello")
    async def hello(self, event: AstrMessageEvent):
        yield event.plain_result("Hello from Python!")

    @filter.llm_tool(name="py_add")
    async def add(self, event: AstrMessageEvent, a: int, b: int) -> str:
        """加法工具。

        Args:
            a (int): 第一个数
            b (int): 第二个数
        """
        return str(a + b)

    @filter.on_llm_request()
    async def llm_req(self, event: AstrMessageEvent, req):
        req.system_prompt += "你是一个猫娘。"
```

插件包结构（与 Python AstrBot 插件一致）：

```
插件目录/
├── main.py                  # 插件入口（或含 __init__.py 的包）
├── metadata.json / metadata.yaml  # 插件元数据（语言由入口文件自动识别）
├── _conf_schema.json        # 配置 schema（WebUI 配置对话框）
├── requirements.txt         # 依赖（宿主自动 pip 安装到 venv）
└── README.md
```

## 支持的 API

- `from astrbot.api.event import filter, AstrMessageEvent`：全部装饰器
  （`command` / `command_group` / `regex` / `custom_filter` / `permission_type` /
  `event_message_type` / `platform_adapter_type` / `llm_tool` / `on_llm_request` /
  `on_decorating_result` / `on_llm_response` / `on_using_llm_tool` /
  `on_llm_tool_respond` / `on_waiting_llm_request` / `on_agent_begin` /
  `on_agent_done` / `on_astrbot_loaded` / `on_platform_loaded` /
  `on_plugin_loaded` / `on_plugin_unloaded` / `on_plugin_error` /
  `after_message_sent`）
- `from astrbot.api.star import Context, Star, register`
- `from astrbot.api import logger`
- 消息组件：`Plain` / `Image` / `At` / `AtAll` / `Record` / `Video` / `File` /
  `Face` / `Reply` / `Json` / `Nodes` / `Node` / `Forward` / `Poke` 等
- `event.plain_result()` / `event.image_result()` / `event.chain_result()` /
  `event.stop_event()` / `event.request_llm()` / `event.send()` / yield 多轮回复
- `self.context.get_config()` / `send_message()` / `llm_generate()` /
  `get_llm_tool_manager()`

## 运行机制

宿主（Astrbot-golang ≥ 含 Python SDK 支持版本）启动 Python 插件：

1. 检测解释器：`ASTRBOT_PYTHON_BIN` > PATH `python3` > **自动下载
   python-build-standalone**（无系统 Python 时，见宿主文档）
2. 创建 venv 并安装 `grpcio` / `protobuf`（缓存于 `~/.cache/astrbot-go/`）
3. `python3 -m astrbot._bridge.server <插件目录>` 启动子进程
4. go-plugin 握手 + gRPC（`PluginService` + `plugin.GRPCBroker`），
   与 Go 插件走同一 RPC 契约，宿主侧完全透明

## 开发与测试

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install "grpcio>=1.50" "protobuf>=7.35,<8" grpcio-tools
python tests/run_tests.py          # 单元测试
python -m grpc_tools.protoc -Iproto \
  --python_out=astrbot/_bridge/gen --grpc_python_out=astrbot/_bridge/gen \
  proto/plugin.proto proto/goplugin.proto   # 重新生成 gRPC 代码
```

> 注意：`astrbot/_bridge/gen/*.py` 由 **protoc 7.35.1** 生成，生成代码会在
> import 时校验 protobuf 运行时版本——venv 必须安装与 pyproject.toml 一致的
> `protobuf>=7.35,<8`，否则插件在 dependency_check 阶段即 STARTUP_ERROR。

`proto/` 目录：`plugin.proto`（PluginService/HostService，与 Go SDK 共享，
Go 侧 gen 由 Go SDK 仓库的 `buf generate` 生成；本仓库的 Python gen 由上面的
grpc_tools.protoc 生成）、`goplugin.proto`（go-plugin 的 GRPCBroker 服务）。

## 协议与数据路径（P1）

本 SDK 与 Go 宿主之间的事件 / 消息链 / 响应链走 **原生 protobuf data plane**
（0 次 Event JSON 编解码）。插件层 API（`event.message_str` / `sender_id` /
`get_messages()` …）保持不变，不感知底层 wire format：

```
proto SDKEvent（固定字段原生 + repeated Component + metadata_json）
    ↓
AstrMessageEvent.from_proto()   ← P1 新增
    ↓
插件（AstrMessageEvent）
```

- **`event_json` / `chain_json` RPC 字段已移除**（P1，与 Go SDK 同步）；
  `from_event_json` 保留仅用于非 RPC 业务；RPC 入口一律 `from_proto`。
- **协议版本协商**：`Register` 校验 `protocol_version == P1_PROTOCOL_VERSION`
  （`2`，见 `astrbot/_bridge/dispatch.py`）。Host 与 SDK 版本不一致 → 明确
  失败提示升级，无 legacy 回退。
- **组件原生**：`component_from_proto` / `component_to_proto`（`ComponentType`
  为 `(str, enum.Enum)`，序列化取 `.value` 得到 `'Plain'` 而非 `'ComponentType.Plain'`）；
  桥接钩子（aiocqhttp/botpy/telegram）经 `proto_to_event_dict` 转兼容 dict。
- **动态结构保留 JSON**：`metadata_json`、`data_json`、hook `payload_json`、
  工具 `args_json`（扩展点）。
- **二进制路径**：媒体 `base64_data bytes`（内联）或 `BinaryPayload →
  FileReference`（大文件经宿主 Blob store，宿主 TTL/GC）。
- `text_to_image` / `html_render` 优先读 `image_bytes`（免 base64），旧宿主
  回退 `image_base64`。

**版本纪律**：行为不兼容的变更必须 bump `P1_PROTOCOL_VERSION` 与本仓库 tag
（当前 v0.8.0）。

## 宿主如何消费本 Go 模块

宿主 `Astrbot-golang` 的 go.mod `require` 本模块（开发态用本地 `replace`），
运行时在项目根目录执行 `go list -m -f '{{.Dir}}' <module>` 解析 SDK 目录并
注入 PYTHONPATH；`internal/pysdk` 的 `Ensure()` 在模块解析失败时回退 GitHub
tarball 下载（`v<SDKVersion>` tag，SDKVersion 即本仓库 tag）。

## 仓库

- Python SDK（本仓库）：`github.com/WaterGodFurina/astrbot-golang-plugin-python-sdk`
- Go 宿主：`github.com/WaterGodFurina/Astrbot-golang`
- Go 插件 SDK：`github.com/WaterGodFurina/Astrbot-go-plugin-sdk`
- Python AstrBot 参考实现：`github.com/AstrBotDevs/AstrBot`
