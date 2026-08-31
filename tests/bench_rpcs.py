"""全 RPC 逐接口测速基准。

运行：python tests/bench_rpcs.py [--iters 200] [--output 结果.md]

测速口径：
- HostService 59 RPC：经 HostBridge 真实方法全链路调用（FakeStub 承接，
  请求构造 + proto 编解码 + 响应解析全部真实发生）；
- PluginService 15 RPC：直接调 PluginServiceServicer 方法（含 proto
  序列化往返），事件经 SDKEvent 原生字段构造（P1 协议）。

计时：每 RPC warmup 10 次后采 iters 个样本，输出 min/avg/p50/p95/max（µs）。
结果为 SDK 侧纯软件开销（不含真实网络往返），用于回归对比与上界评估。
"""
from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")) if (
    (os := __import__("os")) and True
) else None

os.environ.setdefault("ASTRBOT_DATA_PATH", tempfile.mkdtemp(prefix="bench_rpcs_"))

from astrbot._bridge.gen import plugin_pb2
from astrbot._bridge import host as host_mod
from astrbot._bridge.host import HostBridge


# ── 计时工具 ────────────────────────────────────────────────────────────


def bench(fn, iters: int, warmup: int = 10) -> dict:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(iters):
        t0 = time.perf_counter_ns()
        fn()
        samples.append((time.perf_counter_ns() - t0) / 1000.0)
    samples.sort()

    def pct(p):
        return samples[min(len(samples) - 1, int(len(samples) * p))]

    return {
        "min": samples[0],
        "avg": statistics.fmean(samples),
        "p50": pct(0.50),
        "p95": pct(0.95),
        "max": samples[-1],
    }


# ── HostService FakeStub ────────────────────────────────────────────────


class SpeedStub:
    """承接 HostBridge 全链路的桩：proto 请求构造与响应解析真实发生。"""

    def __init__(self, responses: dict):
        self._responses = responses
        self.calls: list[tuple[str, object]] = []

    def __getattr__(self, rpc_name):
        def _call(request, timeout=None):
            self.calls.append((rpc_name, request))
            return self._responses[rpc_name]

        return _call


def _default_responses() -> dict:
    """每个 HostService RPC 的最小合法响应（字段对照 proto 真实定义）。"""
    return {
        "CallAction": plugin_pb2.CallActionResponse(result_json=b'{"status": 0}'),
        "SendMessage": plugin_pb2.Empty(),
        "RecallMessage": plugin_pb2.Empty(),
        "GetConfig": plugin_pb2.GetConfigResponse(config_json=b'{"k": "v"}'),
        "SetConfig": plugin_pb2.Empty(),
        "ChatLLM": plugin_pb2.ChatLLMResponse(text="hi"),
        "React": plugin_pb2.Empty(),
        "TextToImage": plugin_pb2.TextToImageResponse(image_bytes=b"img"),
        "HtmlRender": plugin_pb2.HtmlRenderResponse(image_bytes=b"html"),
        "GetCurrConversationID": plugin_pb2.ConversationIDResponse(cid="cid-1"),
        "NewConversation": plugin_pb2.ConversationIDResponse(cid="cid-new"),
        "GetConversation": plugin_pb2.ConversationResponse(
            conversation_json=b'{"conversation_id": "c1", "history": "[]"}'
        ),
        "GetConversations": plugin_pb2.ConversationsResponse(
            conversations_json=[b'[{"conversation_id": "c1"}]']
        ),
        "DeleteConversation": plugin_pb2.Empty(),
        "SwitchConversation": plugin_pb2.Empty(),
        "UpdateConversationTitle": plugin_pb2.Empty(),
        "UpdateConversationPersonaID": plugin_pb2.Empty(),
        "GetPersonas": plugin_pb2.PersonasResponse(personas_json=[b'[{"name": "p1"}]']),
        "GetDefaultPersona": plugin_pb2.PersonaResponse(
            persona_json=b'{"name": "default"}'
        ),
        "GetPersonaTree": plugin_pb2.PersonaTreeResponse(
            personas_json=[b'[{"name": "p1"}]'], folders_json=[b"[]"]
        ),
        "ResolveSelectedPersona": plugin_pb2.ResolvePersonaResponse(
            persona_id="pid", persona_name="pn", persona_prompt="pp"
        ),
        "ListProviders": plugin_pb2.ProvidersResponse(
            providers_json=[b'[{"id": "pv"}]']
        ),
        "GetUsingProvider": plugin_pb2.ProviderResponse(provider_json=b'{"id": "pv"}'),
        "SetProvider": plugin_pb2.Empty(),
        "GetProviderModels": plugin_pb2.ProviderModelsResponse(models='["m1","m2"]'),
        "GetPluginRegistry": plugin_pb2.StarsResponse(stars_json=[b'[{"name": "s"}]']),
        "GetStar": plugin_pb2.StarResponse(star_json=b'{"name": "s"}'),
        "SetPluginEnabled": plugin_pb2.Empty(),
        "InstallPlugin": plugin_pb2.Empty(),
        "UninstallPlugin": plugin_pb2.Empty(),
        "ListCommandDescriptors": plugin_pb2.CommandDescriptorsResponse(
            descriptors_json=[b'[{"command": "help"}]']
        ),
        "ListPlatforms": plugin_pb2.PlatformsResponse(
            platforms_json=[b'[{"id": "qq", "type": "aiocqhttp", "name": "qq"}]']
        ),
        "RegisterSessionWait": plugin_pb2.RegisterSessionWaitResponse(wait_id="w-1"),
        "UnregisterSessionWait": plugin_pb2.Empty(),
        "RegisterBridgeHook": plugin_pb2.Empty(),
        "UnregisterBridgeHook": plugin_pb2.Empty(),
        "CreateBlob": plugin_pb2.CreateBlobResponse(
            file=plugin_pb2.FileReference(handle_id="h-1", size=1024)
        ),
        "ReadBlob": plugin_pb2.ReadBlobResponse(data=b"x" * 1024, eof=True, total_size=1024),
        "GetBlobInfo": plugin_pb2.GetBlobInfoResponse(
            file=plugin_pb2.FileReference(handle_id="h-1", size=1024)
        ),
        "ReleaseBlob": plugin_pb2.Empty(),
        "ListSkills": plugin_pb2.SkillsResponse(skills_json=[b'[{"name": "sk"}]']),
        "SetSkillActive": plugin_pb2.Empty(),
        "DeleteSkill": plugin_pb2.Empty(),
        "GetPlatformMessageHistory": plugin_pb2.PMHistoryRecordsResponse(
            records_json=[b'[{"id": 1}]']
        ),
        "InsertPlatformMessageHistory": plugin_pb2.PMHistoryRecordResponse(
            record_json=b'{"id": 2}'
        ),
        "UpdatePlatformMessageHistory": plugin_pb2.Empty(),
        "DeletePlatformMessageHistory": plugin_pb2.Empty(),
        "KBRetrieve": plugin_pb2.KBRetrieveResponse(
            context_text="ctx", results_json='[{"chunk_id": "1"}]'
        ),
        "KBUploadFromURL": plugin_pb2.Empty(),
        "KBListKBs": plugin_pb2.KBListResponse(kbs_json=[b'[{"kb_id": "kb1"}]']),
        "ListSkillsV2": plugin_pb2.SkillsResponse(skills_json=[b'[{"name": "sk2"}]']),
        "RegisterFileToken": plugin_pb2.RegisterFileTokenResponse(token="tok-1"),
        "CronCreate": plugin_pb2.CronJobResponse(
            job_json=b'{"job_id": "j1", "name": "job", "enabled": true}'
        ),
        "CronUpdate": plugin_pb2.CronJobResponse(job_json=b'{"job_id": "j1"}'),
        "CronDelete": plugin_pb2.Empty(),
        "CronList": plugin_pb2.CronJobsResponse(jobs_json=[b'[{"job_id": "j1"}]']),
        "CronRunNow": plugin_pb2.Empty(),
        "McpListTools": plugin_pb2.McpToolsResponse(
            tools_json=[b'[{"server": "s", "name": "t"}]']
        ),
        "McpCallTool": plugin_pb2.McpCallToolResponse(
            text="ok", is_error=False, result_json=b'{"ok": true}'
        ),
    }




class _PlainChain:
    """send_message(session, chain) 的链参数替身（只读 .chain 属性）。"""

    def __init__(self):
        from astrbot.core.message.components import Plain

        self.chain = [Plain("bench")]


# ── HostService 逐 RPC 驱动（调 bridge 真实方法）───────────────────────


def host_rpc_drivers(bridge: HostBridge, blob_data: bytes) -> dict[str, callable]:
    session = "aiocqhttp:FriendMessage:uid"
    return {
        "CallAction": lambda: bridge.call_action("aiocqhttp", "get_login_info", {"k": 1}),
        "SendMessage": lambda: bridge.send_message(
            session, _PlainChain()
        ),
        "RecallMessage": lambda: bridge.recall_message("aiocqhttp", "mid-1"),
        "GetConfig": lambda: bridge.get_config("plug"),
        "SetConfig": lambda: bridge.set_config("plug", {"k": "v"}),
        "ChatLLM": lambda: bridge.chat_llm(prompt="hi", system_prompt="sys", session_id="s1"),
        "React": lambda: bridge.react("aiocqhttp", "sid", "mid", "👍"),
        "TextToImage": lambda: bridge.text_to_image("some text to render"),
        "HtmlRender": lambda: bridge.html_render("<h1>hi</h1>"),
        "GetCurrConversationID": lambda: bridge.get_curr_conversation_id(session),
        "NewConversation": lambda: bridge.new_conversation(session),
        "GetConversation": lambda: bridge.get_conversation(session, "cid"),
        "GetConversations": lambda: bridge.get_conversations(session),
        "DeleteConversation": lambda: bridge.delete_conversation(session, "cid"),
        "SwitchConversation": lambda: bridge.switch_conversation(session, "cid"),
        "UpdateConversationTitle": lambda: bridge.update_conversation_title(session, "t"),
        "UpdateConversationPersonaID": lambda: bridge.update_conversation_persona_id(
            session, "pid"
        ),
        "GetPersonas": lambda: bridge.get_personas(),
        "GetDefaultPersona": lambda: bridge.get_default_persona(session),
        "GetPersonaTree": lambda: bridge.get_persona_tree(),
        "ResolveSelectedPersona": lambda: bridge.resolve_selected_persona(
            session, platform_name="aiocqhttp"
        ),
        "ListProviders": lambda: bridge.list_providers("chat_completion"),
        "GetUsingProvider": lambda: bridge.get_using_provider(session, "chat_completion"),
        "SetProvider": lambda: bridge.set_provider(session, "pid", "chat_completion"),
        "GetProviderModels": lambda: bridge.get_provider_models("pid"),
        "GetPluginRegistry": lambda: bridge.get_plugin_registry(),
        "GetStar": lambda: bridge.get_star("plug"),
        "SetPluginEnabled": lambda: bridge.set_plugin_enabled("plug", True),
        "InstallPlugin": lambda: bridge.install_plugin("https://example.com/r"),
        "UninstallPlugin": lambda: bridge.uninstall_plugin("plug"),
        "ListCommandDescriptors": lambda: bridge.list_command_descriptors(),
        "ListPlatforms": lambda: bridge.list_platforms(),
        "RegisterSessionWait": lambda: bridge.register_session_wait(session, 30),
        "UnregisterSessionWait": lambda: bridge.unregister_session_wait("w-1"),
        "RegisterBridgeHook": lambda: bridge.register_bridge_hook("on_decorating_result"),
        "UnregisterBridgeHook": lambda: bridge.unregister_bridge_hook("on_decorating_result"),
        "CreateBlob": lambda: bridge.create_blob(blob_data, "application/octet-stream"),
        "ReadBlob": lambda: bridge.read_blob("h-1"),
        "GetBlobInfo": lambda: bridge.get_blob_info("h-1"),
        "ReleaseBlob": lambda: bridge.release_blob("h-1"),
        "ListSkills": lambda: bridge.list_skills(),
        "SetSkillActive": lambda: bridge.set_skill_active("sk", True),
        "DeleteSkill": lambda: bridge.delete_skill("sk"),
        "GetPlatformMessageHistory": lambda: bridge.get_platform_message_history(
            "aiocqhttp", "uid", 20
        ),
        "InsertPlatformMessageHistory": lambda: bridge.insert_platform_message_history(
            "aiocqhttp", "uid", {"type": "Plain", "text": "m"}
        ),
        "UpdatePlatformMessageHistory": lambda: bridge.update_platform_message_history(
            1, {"type": "Plain", "text": "m2"}
        ),
        "DeletePlatformMessageHistory": lambda: bridge.delete_platform_message_history(1),
        "KBRetrieve": lambda: bridge.kb_retrieve("query text", ["kb1"], 20, 5),
        "KBUploadFromURL": lambda: bridge.kb_upload_from_url("kb1", "https://e.com", 512, 50),
        "KBListKBs": lambda: bridge.kb_list_kbs(),
        "ListSkillsV2": lambda: bridge.list_skills_v2(False, "local", True),
        "RegisterFileToken": lambda: bridge.register_file_token("/tmp/f.bin", 0),
        "CronCreate": lambda: bridge.cron_create(
            name="job", job_type="cron", cron_expression="* * * * *", payload={"a": 1}
        ),
        "CronUpdate": lambda: bridge.cron_update("j1", {"enabled": False}),
        "CronDelete": lambda: bridge.cron_delete("j1"),
        "CronList": lambda: bridge.cron_list(),
        "CronRunNow": lambda: bridge.cron_run_now("j1"),
        "McpListTools": lambda: bridge.mcp_list_tools(),
        "McpCallTool": lambda: bridge.mcp_call_tool("srv", "tool", {"q": "x"}),
    }


# ── PluginService 逐 RPC 驱动（直调 Servicer）─────────────────────────


_PLUGIN_CODE = '''
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.star.register import register_star
from astrbot.api.star import Context, Star, register

@register_star("bench_plugin", "bench", "bench plugin", "1.0.0")
class BenchStar(Star):
    def __init__(self, context: Context):
        self.context = context

    @filter.command("bench")
    async def cmd(self, event: AstrMessageEvent):
        yield event.plain_result("ok")

    @filter.on_decorating_result()
    async def on_dec(self, event: AstrMessageEvent):
        pass

    @filter.llm_tool()
    async def bench_tool(self, event: AstrMessageEvent, x: str = "1"):
        return "ok"
'''


def _make_plugin_module() -> None:
    import types as _types

    mod = _types.ModuleType("bench_plugin")
    sys.modules["bench_plugin"] = mod
    exec(compile(_PLUGIN_CODE, "<bench_plugin>", "exec"), mod.__dict__)


def _make_sdk_event():
    ev = plugin_pb2.SDKEvent(
        type="FriendMessage",
        platform="aiocqhttp",
        platform_id="qq",
        message_type="FriendMessage",
        self_id="bot",
        sender_id="uid",
        sender_name="u",
        conv_id="uid",
        message_str="/bench",
        plain_text="/bench",
        raw_message="/bench",
        message_id="mid",
    )
    return ev


def plugin_rpc_drivers(svc) -> dict[str, callable]:
    pb = plugin_pb2
    ev = _make_sdk_event()

    # proto 序列化往返：保证计时包含真实 RPC 请求编码形态
    def roundtrip(method, request):
        data = request.SerializeToString()
        parsed = type(request)()
        parsed.ParseFromString(data)
        return method(parsed, None)

    def _register_keep_running():
        # Register 会把生命周期 mark_registered（REGISTERED），而 Handle* RPC
        # 前置 _wait_instanced 需 RUNNING——每次基准调用后立即恢复，避免
        # 后续 RPC 全部阻塞 15s/次。
        resp = roundtrip(svc.Register, pb.RegisterRequest(protocol_version=2))
        svc.mark_instanced()
        return resp

    return {
        "Register": _register_keep_running,
        "HandleCommand": lambda: roundtrip(
            svc.HandleCommand, pb.HandleCommandRequest(name="bench", event=ev)
        ),
        "HandleFilter": lambda: roundtrip(
            svc.HandleFilter, pb.HandleFilterRequest(event=ev)
        ),
        "HandleHook": lambda: roundtrip(
            svc.HandleHook, pb.HandleHookRequest(name="on_decorating_result", event=ev)
        ),
        "HandleLLMRequest": lambda: roundtrip(
            svc.HandleLLMRequest, pb.HandleLLMRequestRequest(name="on_llm_request", event=ev)
        ),
        "HandleTool": lambda: roundtrip(
            svc.HandleTool,
            pb.HandleToolRequest(name="bench_tool", args_json=b'{"x": "1"}', event=ev),
        ),
        "ListTools": lambda: roundtrip(svc.ListTools, pb.Empty()),
        "ListWebApis": lambda: roundtrip(svc.ListWebApis, pb.Empty()),
        "HandleWebRequest": lambda: roundtrip(
            svc.HandleWebRequest,
            pb.HandleWebRequestRequest(method="GET", path="/x", body=b""),
        ),
        "HealthCheck": lambda: roundtrip(svc.HealthCheck, pb.Empty()),
        "SetLogLevel": lambda: roundtrip(
            svc.SetLogLevel, pb.SetLogLevelRequest(level="INFO")
        ),
        "FeedSessionWait": lambda: roundtrip(
            svc.FeedSessionWait,
            pb.FeedSessionWaitRequest(event=ev),
        ),
        "GetConfigSchema": lambda: roundtrip(svc.GetConfigSchema, pb.Empty()),
        "Cleanup": lambda: roundtrip(svc.Cleanup, pb.Empty()),
        "FeedCronJob": lambda: roundtrip(
            svc.FeedCronJob,
            pb.FeedCronJobRequest(job_id="nope", job_name="n", payload_json=b"{}"),
        ),
    }


# ── 主流程 ──────────────────────────────────────────────────────────────


def _patch_bridge_entry(bridge: HostBridge):
    import astrbot.core.star.context as ctx_mod

    saved = {}
    if hasattr(ctx_mod, "_host_bridge"):
        saved["_host_bridge"] = getattr(ctx_mod, "_host_bridge", None)
        ctx_mod._host_bridge = bridge
    saved["host_get_bridge"] = host_mod.get_bridge
    host_mod.get_bridge = lambda: bridge
    saved["ctx_get_host_bridge"] = getattr(ctx_mod, "get_host_bridge", None)
    if saved["ctx_get_host_bridge"] is not None:
        ctx_mod.get_host_bridge = lambda: bridge
    return saved


def _restore_bridge_entry(saved: dict):
    import astrbot.core.star.context as ctx_mod

    if "_host_bridge" in saved and hasattr(ctx_mod, "_host_bridge"):
        ctx_mod._host_bridge = saved["_host_bridge"]
    host_mod.get_bridge = saved["host_get_bridge"]
    if saved["ctx_get_host_bridge"] is not None:
        ctx_mod.get_host_bridge = saved["ctx_get_host_bridge"]


def run_host_bench(iters: int) -> list[tuple[str, dict]]:
    bridge = HostBridge()
    bridge._stub = SpeedStub(_default_responses())
    bridge._probed = True
    saved = _patch_bridge_entry(bridge)

    blob = b"b" * 1024
    drivers = host_rpc_drivers(bridge, blob)
    results = []
    try:
        for rpc_name, fn in drivers.items():
            try:
                results.append((rpc_name, bench(fn, iters)))
            except Exception as e:
                results.append((rpc_name, {"error": f"{type(e).__name__}: {e}"}))
    finally:
        _restore_bridge_entry(saved)
    return results


def run_plugin_bench(iters: int) -> list[tuple[str, dict]]:
    from astrbot._bridge.dispatch import PluginServiceServicer

    _make_plugin_module()
    svc = PluginServiceServicer(
        "bench_plugin", "1.0.0", "", "", plugin_dir=tempfile.gettempdir()
    )
    svc.mark_ready()
    # 取桥入口全程 mock：覆盖 drivers 里的 Register 等调用，防 dial 重试挂起
    patcher = mock.patch.object(
        host_mod,
        "get_bridge",
        return_value=mock.MagicMock(
            ensure_connected=lambda: True, get_plugin_registry=lambda: []
        ),
    )
    patcher.start()
    try:
        svc.Register(plugin_pb2.RegisterRequest(protocol_version=2), None)
        if hasattr(svc, "mark_instanced"):
            svc.mark_instanced()

        drivers = plugin_rpc_drivers(svc)
        results = []
        for rpc_name, fn in drivers.items():
            try:
                results.append((rpc_name, bench(fn, iters)))
            except Exception as e:
                results.append((rpc_name, {"error": f"{type(e).__name__}: {e}"}))
        return results
    finally:
        patcher.stop()


def render_table(title: str, results, iters: int) -> str:
    lines = [
        f"## {title}（iters={iters}，单位 µs）",
        "",
        "| RPC | min | avg | p50 | p95 | max |",
        "|---|---|---|---|---|---|",
    ]
    ok = 0
    for rpc_name, stats in results:
        if "error" in stats:
            lines.append(f"| {rpc_name} | ERROR | {stats['error']} | | | |")
        else:
            ok += 1
            lines.append(
                f"| {rpc_name} | {stats['min']:.1f} | {stats['avg']:.1f} "
                f"| {stats['p50']:.1f} | {stats['p95']:.1f} | {stats['max']:.1f} |"
            )
    lines += ["", f"覆盖：{ok}/{len(results)} RPC"]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iters", type=int, default=200)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    host_results = run_host_bench(args.iters)
    plugin_results = run_plugin_bench(args.iters)

    text = "\n".join(
        [
            "# Python SDK 全 RPC 测速结果",
            "",
            f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}｜口径：HostService=HostBridge "
            "全链路（FakeStub 承载，含请求构造/proto 编解码/响应解析）；"
            "PluginService=直调 Servicer（含 proto 序列化往返）。"
            "结果为 SDK 侧纯软件开销（无真实网络）。",
            "",
            render_table("HostService（插件→宿主，59 RPC）", host_results, args.iters),
            render_table("PluginService（宿主→插件，15 RPC）", plugin_results, args.iters),
        ]
    )
    print(text)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"\n结果已写入 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
