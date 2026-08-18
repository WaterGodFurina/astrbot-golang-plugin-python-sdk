"""LLM 函数工具（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.agent.tool` 的对外入口：插件以

    from astrbot.core.agent.tool import FunctionTool

方式继承定义工具子类。SDK 的实现本体在 `astrbot.api.FunctionTool`
（普通 dataclass：name / description / parameters / handler / active +
`call()`），这里直接 re-export，避免两套定义漂移。

插件常见用法（与本体兼容，无需 pydantic 校验）：
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
"""
from astrbot.api import FunctionTool

__all__ = ["FunctionTool"]
