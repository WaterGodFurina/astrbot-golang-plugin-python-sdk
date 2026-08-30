"""LLM 函数工具（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.agent.tool` 的对外入口与富 API：插件以

    from astrbot.core.agent.tool import FunctionTool, ToolSet, ToolSchema

方式继承定义工具子类（普通 dataclass，无需 pydantic 校验），或直接用
ToolSet 管理工具集合（增删查、轻量/参数子集、OpenAI/Anthropic/Google
schema 转换、合并与迭代）。

插件常见用法（与本体兼容）：
- 类属性式子类（dataclass 子类化 + `async def run`）：

    @dataclass
    class MyTool(FunctionTool):
        name: str = "my_tool"
        description: str = "..."
        parameters: dict = field(default_factory=lambda: {...})

        async def run(self, event, **kwargs) -> str: ...

- 或 `__init__` 传参 + 覆写 `call()`（listen_music_python 即此用法）：

    class FindMusicTool(FunctionTool):
        def __init__(self, plugin):
            super().__init__(name="find_music", description="...", parameters={...})
        async def call(self, context, **kwargs): ...

- 用 ToolSet 组装/转换 schema：

    ts = ToolSet(); ts.add_tool(t); ts.openai_schema(); ts.merge(other)
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable

from astrbot.core.agent.run_context import ContextWrapper  # noqa: F401  权威定义见 run_context.py（避免同名不同义）
from astrbot.core.agent.run_context import NoContext, TContext  # noqa: F401  对齐本体 run_context 导出
from astrbot.core.message.message_event_result import MessageEventResult
from astrbot.core.utils.deprecation import deprecated

ParametersType = dict[str, Any]


@dataclass
class ToolExecResult:
    """工具执行结果（对齐本体 ToolExecResult：is_success / result / error）。

    本体将 ToolExecResult 定义为 `str | mcp.CallToolResult` 类型别名，SDK
    不使用 mcp，故用普通 dataclass 承载同样的语义：成功标记 + 结果内容 +
    错误信息。
    """

    is_success: bool = False
    """工具执行是否成功"""
    result: Any = None
    """工具执行结果内容"""
    error: str | None = None
    """错误信息（失败时非空，可空）"""


@dataclass
class ToolSchema:
    """工具 schema（简单版，不对 JSON Schema 做严格校验）。

    构造时若 parameters 缺 type/properties 则补默认值
    （type → "object"、properties → {}），避免下游 schema 转换出错；
    name 缺省时默认空字符串。
    """

    name: str = ""
    """工具名"""
    description: str = ""
    """工具描述"""
    parameters: ParametersType = field(default_factory=dict)
    """工具参数，JSON Schema 格式（{"type": "object", "properties": {...}}）"""

    def __post_init__(self) -> None:
        if not isinstance(self.parameters, dict):
            self.parameters = {}
        self.parameters.setdefault("type", "object")
        self.parameters.setdefault("properties", {})

    def validate_parameters(self) -> "ToolSchema":
        """校验 parameters 是否为合法 JSON Schema（对齐本体 ToolSchema 方法）。

        jsonschema 为可选依赖：缺失时仅做基础类型校验，不抛错。
        """
        try:
            import jsonschema

            jsonschema.validate(
                self.parameters, jsonschema.Draft202012Validator.META_SCHEMA
            )
        except ImportError:
            pass
        return self


@dataclass
class FunctionTool(ToolSchema):
    """LLM 函数工具（普通 dataclass，插件可子类化）。

    字段全部带默认值：插件子类既可整类覆写（dataclass 子类化），也可在
    `__init__` 中传参覆写，均不破坏既有子类。
    """

    handler: Callable | None = None
    """实现工具功能的异步可调用对象（优先级最高）"""
    handler_module_path: str | None = None
    """handler 所在模块路径。

    handler 被 functools.partial 包裹后 __module__ 会丢失，故单独保留此字段
    （MCP 来源的工具此字段为空）。
    """
    active: bool = True
    """工具是否启用（AstrBot 专用字段，ToolSet.add_tool 据此做优先级取舍）"""
    is_background_task: bool = False
    """声明为后台任务：立即返回任务标识，真实工作异步继续"""

    def __repr__(self) -> str:
        return f"FuncTool(name={self.name}, parameters={self.parameters}, description={self.description})"

    async def call(self, context: ContextWrapper, **kwargs) -> Any:
        """执行工具调用（handler 字段优先级最高）。

        Args:
            context: 运行期上下文包装（ContextWrapper，可为空 contexts/wrapped）。
        """
        raise NotImplementedError(
            "FunctionTool.call() 必须由子类实现或设置 handler。"
        )


@dataclass
class ToolSet:
    """函数工具集合（对齐 Python 本体 tool.py 的富 API）。

    提供增删查、按 name 去重（active 优先级）、轻量/参数子集转换、
    OpenAI / Anthropic / Google schema 转换、合并与迭代。
    """

    tools: list[FunctionTool] = field(default_factory=list)

    def empty(self) -> bool:
        """工具集合是否为空。"""
        return len(self.tools) == 0

    def add_tool(self, tool: FunctionTool) -> None:
        """添加工具到集合。

        若已存在同名工具：
        - 优先保留 active=True 的那个；
        - 若两者 active 状态相同，用新工具覆盖旧工具。
        """
        for i, existing_tool in enumerate(self.tools):
            if existing_tool.name == tool.name:
                # getattr 兜底 True：兼容未定义 active 字段的工具（如 mock）
                existing_active = bool(getattr(existing_tool, "active", True))
                new_active = bool(getattr(tool, "active", True))
                # 新工具启用，或旧工具停用 → 覆盖
                if new_active or not existing_active:
                    self.tools[i] = tool
                return
        self.tools.append(tool)

    def remove_tool(self, name: str) -> None:
        """按 name 移除工具。"""
        self.tools = [tool for tool in self.tools if tool.name != name]

    def get_tool(self, name: str) -> FunctionTool | None:
        """按 name 获取工具，未找到返回 None。"""
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None

    def get_light_tool_set(self) -> "ToolSet":
        """返回只含 name/description（空参数）的轻量工具集合（跳过停用工具）。"""
        light_tools = []
        for tool in self.tools:
            if hasattr(tool, "active") and not tool.active:
                continue
            light_tools.append(
                FunctionTool(
                    name=tool.name,
                    parameters={"type": "object", "properties": {}},
                    description=tool.description,
                    handler=None,
                )
            )
        return ToolSet(light_tools)

    def get_param_only_tool_set(self) -> "ToolSet":
        """返回只含 name/parameters（无 description）的工具集合（跳过停用工具）。"""
        param_tools = []
        for tool in self.tools:
            if hasattr(tool, "active") and not tool.active:
                continue
            params = (
                copy.deepcopy(tool.parameters)
                if tool.parameters
                else {"type": "object", "properties": {}}
            )
            param_tools.append(
                FunctionTool(
                    name=tool.name,
                    parameters=params,
                    description="",
                    handler=None,
                )
            )
        return ToolSet(param_tools)

    @deprecated(reason="Use add_tool() instead", version="4.0.0")
    def add_func(
        self,
        name: str,
        func_args: list,
        desc: str,
        handler: Callable[..., Any],
    ) -> None:
        """（兼容入口）按 [{"name":..., "type":..., "description":...}] 添加工具。"""
        params: dict = {"type": "object", "properties": {}}
        for param in func_args:
            params["properties"][param["name"]] = {
                "type": param["type"],
                "description": param["description"],
            }
        self.add_tool(
            FunctionTool(
                name=name,
                parameters=params,
                description=desc,
                handler=handler,
            )
        )

    @deprecated(reason="Use remove_tool() instead", version="4.0.0")
    def remove_func(self, name: str) -> None:
        """（兼容入口）按 name 移除工具。"""
        self.remove_tool(name)

    @deprecated(reason="Use get_tool() instead", version="4.0.0")
    def get_func(self, name: str) -> FunctionTool | None:
        """（兼容入口）按 name 获取工具。"""
        return self.get_tool(name)

    @property
    def func_list(self) -> list[FunctionTool]:
        """底层工具列表（兼容旧字段名）。"""
        return self.tools

    def openai_schema(self, omit_empty_parameter_field: bool = False) -> list[dict]:
        """转换为 OpenAI API function calling schema（list[dict]）。

        omit_empty_parameter_field=True 时，parameters 为空（无 properties）
        则省略 parameters 字段。
        """
        result = []
        for tool in self.tools:
            func_def: dict = {"type": "function", "function": {"name": tool.name}}
            if tool.description:
                func_def["function"]["description"] = tool.description
            if tool.parameters is not None:
                if (
                    tool.parameters and tool.parameters.get("properties")
                ) or not omit_empty_parameter_field:
                    func_def["function"]["parameters"] = tool.parameters
            result.append(func_def)
        return result

    def anthropic_schema(self) -> list[dict]:
        """转换为 Anthropic API 格式（list[dict]）。"""
        result = []
        for tool in self.tools:
            input_schema: dict = {"type": "object"}
            if tool.parameters:
                input_schema["properties"] = tool.parameters.get("properties", {})
                input_schema["required"] = tool.parameters.get("required", [])
            tool_def: dict = {"name": tool.name, "input_schema": input_schema}
            if tool.description:
                tool_def["description"] = tool.description
            result.append(tool_def)
        return result

    def google_schema(self) -> dict:
        """转换为 Google GenAI API 格式（dict，含 function_declarations）。"""

        def convert_schema(schema: dict) -> dict:
            """把 JSON Schema 转换为 Gemini API 格式。"""
            supported_types = {
                "string",
                "number",
                "integer",
                "boolean",
                "array",
                "object",
                "null",
            }
            supported_formats = {
                "string": {"enum", "date-time"},
                "integer": {"int32", "int64"},
                "number": {"float", "double"},
            }

            if "anyOf" in schema:
                return {"anyOf": [convert_schema(s) for s in schema["anyOf"]]}

            result: dict = {}

            # 不修改原 schema，避免副作用
            origin_type = schema.get("type")
            target_type = origin_type

            # 兼容修复：Gemini 期望 type 为字符串，标准 JSON Schema（MCP）
            # 允许列表（如 ["string", "null"]），回退到第一个非 null 类型
            if isinstance(origin_type, list):
                target_type = next((t for t in origin_type if t != "null"), "string")

            if target_type in supported_types:
                result["type"] = target_type
                if "format" in schema and schema["format"] in supported_formats.get(
                    result["type"],
                    set(),
                ):
                    result["format"] = schema["format"]
            else:
                result["type"] = "null"

            support_fields = {
                "title",
                "description",
                "enum",
                "minimum",
                "maximum",
                "maxItems",
                "minItems",
                "nullable",
                "required",
            }
            result.update({k: schema[k] for k in support_fields if k in schema})

            if "properties" in schema:
                properties = {}
                for key, value in schema["properties"].items():
                    prop_value = convert_schema(value)
                    if "default" in prop_value:
                        del prop_value["default"]
                    if "additionalProperties" in prop_value:
                        del prop_value["additionalProperties"]
                    properties[key] = prop_value
                if properties:
                    result["properties"] = properties

            if target_type == "array":
                items_schema = schema.get("items")
                if isinstance(items_schema, dict):
                    result["items"] = convert_schema(items_schema)
                else:
                    # Gemini 要求数组 schema 必须带 items；JSON Schema 允许省略，
                    # 这里兜底为宽松的 string item schema
                    result["items"] = {"type": "string"}

            return result

        tools = []
        for tool in self.tools:
            d: dict = {"name": tool.name}
            if tool.description:
                d["description"] = tool.description
            if tool.parameters:
                d["parameters"] = convert_schema(tool.parameters)
            tools.append(d)

        declarations: dict = {}
        if tools:
            declarations["function_declarations"] = tools
        return declarations

    @deprecated(reason="Use openai_schema() instead", version="4.0.0")
    def get_func_desc_openai_style(self, omit_empty_parameter_field: bool = False):
        """（兼容入口）OpenAI schema。"""
        return self.openai_schema(omit_empty_parameter_field)

    @deprecated(reason="Use anthropic_schema() instead", version="4.0.0")
    def get_func_desc_anthropic_style(self):
        """（兼容入口）Anthropic schema。"""
        return self.anthropic_schema()

    @deprecated(reason="Use google_schema() instead", version="4.0.0")
    def get_func_desc_google_genai_style(self):
        """（兼容入口）Google schema。"""
        return self.google_schema()

    def names(self) -> list[str]:
        """所有工具的名称列表。"""
        return [tool.name for tool in self.tools]

    def merge(self, other: "ToolSet") -> None:
        """把另一个 ToolSet 合并进当前集合（逐项走 add_tool 去重逻辑）。"""
        for tool in other.tools:
            self.add_tool(tool)

    def __len__(self) -> int:
        return len(self.tools)

    def __bool__(self) -> bool:
        return len(self.tools) > 0

    def __iter__(self):
        return iter(self.tools)

    def __repr__(self) -> str:
        return f"ToolSet(tools={self.tools})"

    def __str__(self) -> str:
        return f"ToolSet(tools={self.tools})"


__all__ = [
    "ContextWrapper",
    "FunctionTool",
    "MessageEventResult",
    "ToolExecResult",
    "ToolSchema",
    "ToolSet",
    "deprecated",
]
