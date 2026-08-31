"""Tool-Loop Agent Runner（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.agent.runners.tool_loop_agent_runner.
ToolLoopAgentRunner` 的公开方法面：reset / step / step_until_done /
done / request_stop / was_aborted / follow_up / get_final_llm_resp 的
签名与本体一致。

SDK 降级：Go 宿主在管线内原生执行 Agent 工具循环（工具收集、多轮
tool-call 迭代、结果回填均由宿主完成），本运行器不产出任何步骤——
step / step_until_done 为空异步生成器，get_final_llm_resp 恒为 None。
插件侧一般不直接驱动 runner：注册 Agent 走 `register_agent`（宿主
编排为 transfer_to_<name> 移交工具），主动循环走 `context.tool_loop_agent`。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator


class ToolLoopAgentRunner:
    """工具循环 Agent 运行器（SDK 降级：签名对齐本体，不执行真实循环）。"""

    def __init__(self) -> None:
        self._final_llm_resp = None
        self._done = False
        self._aborted = False
        self._stop_requested = False
        # reset() 注入的运行期对象（本体同名字段，SDK 仅保存不消费）
        self.req = None
        self.provider = None
        self.run_context = None
        self.tool_executor = None
        self.agent_hooks = None
        self.streaming = False

    async def reset(
        self,
        provider=None,
        request=None,
        run_context=None,
        tool_executor=None,
        agent_hooks=None,
        streaming: bool = False,
        enforce_max_turns: int = -1,
        llm_compress_instruction: str | None = None,
        llm_compress_keep_recent_ratio: float = 0.15,
        llm_compress_provider=None,
        truncate_turns: int = 1,
        custom_token_counter=None,
        custom_compressor=None,
        tool_schema_mode: str | None = "full",
        fallback_providers: list | None = None,
        request_max_retries: int | None = None,
        tool_result_overflow_dir: str | None = None,
        read_tool=None,
        **kwargs,
    ) -> None:
        """重置运行器（签名对齐本体 ToolLoopAgentRunner.reset）。

        SDK 降级：仅保存 provider / request / run_context / tool_executor /
        agent_hooks 等运行期对象供属性访问，不做上下文压缩、不装配消息。
        """
        self.req = request
        self.provider = provider
        self.run_context = run_context
        self.tool_executor = tool_executor
        self.agent_hooks = agent_hooks
        self.streaming = streaming
        self._done = False
        self._aborted = False
        self._stop_requested = False
        self._final_llm_resp = None

    async def step(self) -> AsyncGenerator[None, None]:
        """处理单步（SDK 降级：不产出任何步骤的空异步生成器）。"""
        return
        yield  # pragma: no cover - 保证该方法是异步生成器

    async def step_until_done(self, max_step: int = 30) -> AsyncGenerator[None, None]:
        """迭代执行步骤直至完成（SDK 降级：不产出任何步骤）。

        Args:
            max_step: 最大步数（对齐本体参数名 max_step）。
        """
        self._done = True
        return
        yield  # pragma: no cover - 保证该方法是异步生成器

    def done(self) -> bool:
        """检查 Agent 是否已完成工作（SDK 降级：step_until_done 后为 True）。"""
        return self._done

    def request_stop(self) -> None:
        """请求停止运行（SDK 降级：仅记录标记，无运行中循环可打断）。"""
        self._stop_requested = True

    def was_aborted(self) -> bool:
        """是否因用户中止而结束（SDK 降级：恒为 False，无真实循环）。"""
        return self._aborted

    def follow_up(self, *, message_text: str):
        """排队一条 follow-up 消息（SDK 降级：无真实工具循环，恒返回 None）。

        Args:
            message_text: 追加给下一次工具结果的用户补充消息。
        """
        del message_text
        return None

    def get_final_llm_resp(self):
        """返回最终 LLM 响应（SDK 降级：恒为 None）。"""
        return self._final_llm_resp
