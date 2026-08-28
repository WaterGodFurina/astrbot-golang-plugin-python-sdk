"""sdk.Event / 消息组件 JSON 与 Python 对象互转（对齐 Go SDK Component 字段）。"""
from __future__ import annotations

from astrbot.core.message.components import (
    At,
    AtAll,
    BaseMessageComponent,
    Face,
    File,
    Forward,
    Image,
    Json,
    Node,
    Nodes,
    Plain,
    Record,
    Reply,
    Unknown,
    Video,
)
from astrbot.core.message.message_event_result import MessageEventResult
from astrbot.core.platform.astr_message_event import AstrMessageEvent

# 跨进程序列化边界的递归上限：宿主推来的 event_json/chain_json 可能含
# 自嵌套转发消息（数千层 Node），无限递归会抛 RecursionError（不在
# JSONDecodeError 捕获范围内）导致 gRPC handler 以 UNKNOWN 失败。
_MAX_NODE_DEPTH = 50


def component_from_json(data: dict, _depth: int = 0) -> BaseMessageComponent:
    """把宿主 sdk.Component JSON 还原为 Python 组件对象。"""
    if not data:
        return Unknown(text="")
    ctype = str(data.get("type", "Unknown"))
    text = data.get("text", "")
    if ctype == "Plain":
        return Plain(text=text)
    if ctype == "At":
        target = data.get("target_id", "")
        if target in ("all", "0"):
            return AtAll()
        return At(qq=target or "0", name=data.get("name", ""))
    if ctype == "AtAll":
        return AtAll()
    if ctype == "Image":
        b64 = data.get("base64")
        file_ = data.get("file") or None
        if not file_ and b64 and not data.get("url") and not data.get("path"):
            file_ = f"base64://{b64}"
        return Image(
            file=file_,
            url=data.get("url") or None,
            path=data.get("path") or None,
        )
    if ctype == "Record":
        return Record(
            file=data.get("file") or None,
            url=data.get("url") or None,
            path=data.get("path") or None,
        )
    if ctype == "File":
        return File(
            name=data.get("name") or "",
            file=data.get("file") or data.get("path") or "",
            url=data.get("url") or "",
        )
    if ctype == "Video":
        return Video(
            file=data.get("file") or data.get("url") or "",
            url=data.get("url") or None,
            path=data.get("path") or None,
        )
    if ctype == "Face":
        try:
            face_id = int(data.get("id", 0) or 0)
        except (TypeError, ValueError):
            # 非数字 id（畸形数据）兜底为 0，避免解析崩溃
            face_id = 0
        return Face(id=face_id)
    if ctype == "Emoji":
        comp = Unknown(text="")
        comp.type = "Emoji"
        comp.id = data.get("id", "")
        comp.url = data.get("url", "")
        return comp
    if ctype == "Json":
        return Json(data=data.get("data") or {})
    if ctype == "Reply":
        return Reply(id=data.get("id", ""), message_str=data.get("text", ""))
    if ctype == "Node":
        # 还原转发子链（component_to_json 的对称结构：data.content）
        if _depth >= _MAX_NODE_DEPTH:
            return Unknown(text="")
        content = [
            component_from_json(c, _depth + 1)
            for c in (data.get("data") or {}).get("content") or []
        ]
        return Node(content=content, uin=data.get("uin", "0"), name=data.get("name", ""))
    comp = Unknown(text=text)
    comp.raw = data
    return comp


def component_to_json(comp: BaseMessageComponent, _depth: int = 0) -> dict:
    """把 Python 组件对象转为宿主 sdk.Component JSON。"""
    ctype = str(getattr(comp, "type", "Unknown"))
    if isinstance(comp, Plain):
        return {"type": "Plain", "text": comp.text}
    if isinstance(comp, AtAll):
        return {"type": "AtAll"}
    if isinstance(comp, At):
        return {"type": "At", "target_id": str(comp.qq), "name": comp.name or ""}
    if isinstance(comp, Image):
        out = {"type": "Image"}
        file_ = getattr(comp, "file", None)
        if file_ and (file_.startswith("http://") or file_.startswith("https://")):
            out["url"] = file_
            return out
        if getattr(comp, "url", None):
            out["url"] = comp.url
        if getattr(comp, "path", None):
            out["path"] = comp.path
        if file_:
            if file_.startswith("base64://"):
                out["base64"] = file_[len("base64://"):]
            else:
                out["file"] = file_
        return out
    if isinstance(comp, Record):
        out = {"type": "Record"}
        file_ = getattr(comp, "file", None)
        if file_ and (file_.startswith("http://") or file_.startswith("https://")):
            out["url"] = file_
            return out
        if getattr(comp, "url", None):
            out["url"] = comp.url
        if getattr(comp, "path", None):
            out["path"] = comp.path
        if file_:
            if file_.startswith("base64://"):
                out["base64"] = file_[len("base64://"):]
            else:
                out["file"] = file_
        return out
    if isinstance(comp, File):
        # 统一用真实属性：url（远程链接）与 file_（本地路径/base64 等承载
        # 字段）。不访问 File.file 属性——它是会触发下载/临时文件的 property，
        # 序列化时不应引入下载副作用（也避免热路径上的额外开销）。
        out = {"type": "File", "name": comp.name or "file"}
        if getattr(comp, "url", None):
            out["url"] = comp.url
        file_ = getattr(comp, "file_", None)
        if file_:
            if file_.startswith("base64://"):
                out["base64"] = file_[len("base64://"):]
            else:
                out["file"] = file_
        return out
    if isinstance(comp, Video):
        out = {"type": "Video"}
        if getattr(comp, "url", None):
            out["url"] = comp.url
        if getattr(comp, "path", None):
            out["path"] = comp.path
        if getattr(comp, "file", None) and not getattr(comp, "url", None):
            if comp.file.startswith("base64://"):
                out["base64"] = comp.file[len("base64://"):]
            else:
                out["path"] = comp.file
        return out
    if isinstance(comp, Face):
        return {"type": "Face", "id": str(getattr(comp, "id", 0))}
    if getattr(comp, "type", None) == "Emoji":
        return {"type": "Emoji", "id": str(getattr(comp, "id", "")), "url": getattr(comp, "url", "")}
    if isinstance(comp, Json):
        return {"type": "Json", "data": comp.data or {}}
    if isinstance(comp, Reply):
        return {"type": "Reply", "id": str(getattr(comp, "id", "")), "text": getattr(comp, "message_str", "") or ""}
    if isinstance(comp, Forward):
        return {"type": "Forward", "id": str(getattr(comp, "id", ""))}
    if isinstance(comp, Node):
        if _depth >= _MAX_NODE_DEPTH:
            return {"type": "Unknown", "text": ""}
        return {
            "type": "Node",
            "uin": str(getattr(comp, "uin", "0")),
            "name": getattr(comp, "name", "") or "",
            "data": {
                "content": [component_to_json(c, _depth + 1) for c in (comp.content or [])]
            },
        }
    if isinstance(comp, Nodes):
        return {
            "type": "Nodes",
            "data": {
                "nodes": [component_to_json(n, _depth + 1) for n in (comp.nodes or [])]
            },
        }
    if isinstance(comp, Unknown):
        return {"type": "Unknown", "text": getattr(comp, "text", "")}
    return {"type": "Unknown", "text": getattr(comp, "text", "") or str(comp)}


def result_to_json(result: MessageEventResult | str | None) -> list[dict]:
    """把插件 handler 返回的结果转为 Component JSON 列表。

    返回 (chain_json, stop)。str → Plain；None → 空；MessageEventResult → chain。
    """
    if result is None:
        return [], False
    if isinstance(result, str):
        return [{"type": "Plain", "text": result}], False
    if isinstance(result, MessageEventResult):
        # 直接走 is_stopped()：EventResultType 为 enum.Enum（auto() 值为整数），
        # 用 str(result_type.value) == "stop" 判断恒 False，导致 stop_event()
        # 的结果被误判为未停止（回归测试 test_result_to_json 覆盖）。
        stop = result.is_stopped()
        return [component_to_json(c) for c in (result.chain or [])], stop
    if isinstance(result, dict):
        return [result], False
    return [], False


# ── P1：proto Component ⇄ Python 组件（native，0 JSON）──

def component_from_proto(c, _depth: int = 0) -> BaseMessageComponent:
    """把 proto Component（SDKEvent.components / response.chain）还原为
    Python 组件对象。media 走 base64_data（bytes）→ Base64 string。"""
    from astrbot.core.message.components import ComponentType

    if c is None:
        return Unknown(text="")
    ctype = str(c.type or "Unknown")
    text = c.text or ""
    if ctype == "Plain":
        return Plain(text=text)
    if ctype == "At":
        target = c.target_id or ""
        if target in ("all", "0"):
            return AtAll()
        return At(qq=target or "0", name=c.name or "")
    if ctype == "AtAll":
        return AtAll()
    if ctype == "Image":
        b64 = None
        if c.base64_data:
            b64 = base64.b64encode(bytes(c.base64_data)).decode()
        file_ = c.file or None
        if not file_ and b64 and not c.url and not c.path:
            file_ = f"base64://{b64}"
        return Image(file=file_, url=c.url or None, path=c.path or None)
    if ctype == "Record":
        return Record(file=c.file or None, url=c.url or None, path=c.path or None)
    if ctype == "File":
        return File(name=c.name or "", file=c.file or c.path or "", url=c.url or "")
    if ctype == "Video":
        return Video(file=c.file or c.url or "", url=c.url or None, path=c.path or None)
    if ctype == "Face":
        try:
            face_id = int(c.id or 0)
        except (TypeError, ValueError):
            face_id = 0
        return Face(id=face_id)
    if ctype == "Emoji":
        comp = Unknown(text="")
        comp.type = "Emoji"
        comp.id = c.id or ""
        comp.url = c.url or ""
        return comp
    if ctype == "Json":
        import json as _json
        data = {}
        if c.data_json:
            try:
                data = _json.loads(bytes(c.data_json).decode("utf-8", "replace"))
            except (ValueError, TypeError):
                data = {}
        return Json(data=data or {})
    if ctype == "Reply":
        return Reply(id=c.id or "", message_str=c.text or "")
    if ctype == "Node":
        if _depth >= _MAX_NODE_DEPTH:
            return Unknown(text="")
        import json as _json
        content = []
        if c.data_json:
            try:
                dd = _json.loads(bytes(c.data_json).decode("utf-8", "replace"))
                content = [
                    component_from_proto(comp, _depth + 1)
                    for comp in (dd or {}).get("content") or []
                ]
            except (ValueError, TypeError):
                content = []
        return Node(content=content, uin=c.name or "0", name=c.name or "")
    return Unknown(text=text)


def component_to_proto(comp: BaseMessageComponent) -> object:
    """把 Python 组件转成 proto Component（发送链）。"""
    import base64 as _b64

    from astrbot._bridge.gen import plugin_pb2

    _t = getattr(comp, "type", "Unknown") or "Unknown"
    # ComponentType 是 (str, enum.Enum)：str() 会得 'ComponentType.Plain'，
    # 需取 .value 才得到 wire 上约定的 'Plain'。
    if hasattr(_t, "value"):
        ctype = str(_t.value)
    else:
        ctype = str(_t)
    pc = plugin_pb2.Component(type=ctype, text=getattr(comp, "text", "") or "")
    pc.target_id = getattr(comp, "target_id", "") or ""
    pc.name = getattr(comp, "name", "") or ""
    pc.url = getattr(comp, "url", "") or ""
    pc.path = getattr(comp, "path", "") or ""
    pc.file = getattr(comp, "file", "") or ""
    pc.id = str(getattr(comp, "id", "") or "")
    if getattr(comp, "type", "") == "Json":
        import json as _json
        data = getattr(comp, "data", None)
        if data:
            pc.data_json = _json.dumps(data, ensure_ascii=False).encode()
    if ctype in ("Image", "Record"):
        # 优先 url/path，其次 base64/file。
        raw = getattr(comp, "file", "") or getattr(comp, "path", "") or ""
        if raw.startswith("base64://"):
            try:
                pc.base64_data = _b64.b64decode(raw[len("base64://"):])
            except Exception:
                pc.base64_data = b""
        elif raw and "://" not in raw:
            # data URI 或本地路径
            if raw.startswith("data:"):
                try:
                    pc.base64_data = _b64.b64decode(raw.split(";base64,", 1)[1])
                except Exception:
                    pc.base64_data = b""
            else:
                pc.path = raw
    return pc


def proto_to_component_list(proto_list) -> list:
    """proto repeated Component → Python 组件列表（事件链/响应链通用）。"""
    return [component_from_proto(c) for c in (proto_list or [])]


def component_list_to_proto(comps) -> list:
    """Python 组件列表 → proto Component 列表（发送链/响应链通用）。"""
    return [component_to_proto(c) for c in (comps or [])]


def proto_to_event_dict(event_proto) -> dict:
    """P1：把 proto SDKEvent 转成桥接层（botpy/telegram 兼容钩子）所需的
    dict。仅兼容层内部使用，非 RPC wire 序列化。"""
    import base64 as _b64
    import json as _json

    chain = []
    for c in (event_proto.components or []):
        d = {
            "type": c.type or "Unknown",
            "text": c.text or "",
            "target_id": c.target_id or "",
            "name": c.name or "",
            "url": c.url or "",
            "path": c.path or "",
            "file": c.file or "",
            "file_id": c.file_id or "",
            "id": c.id or "",
        }
        if c.base64_data:
            d["base64"] = _b64.b64encode(bytes(c.base64_data)).decode()
        if c.data_json:
            try:
                d["data"] = _json.loads(bytes(c.data_json).decode("utf-8", "replace"))
            except (ValueError, TypeError):
                d["data"] = {}
        chain.append(d)
    metadata = {}
    if event_proto.metadata_json:
        try:
            metadata = _json.loads(bytes(event_proto.metadata_json).decode("utf-8", "replace"))
        except (ValueError, TypeError):
            metadata = {}
    return {
        "type": event_proto.type or "",
        "platform": event_proto.platform or "",
        "platform_id": event_proto.platform_id or "",
        "message_type": event_proto.message_type or "",
        "self_id": event_proto.self_id or "",
        "sender_id": event_proto.sender_id or "",
        "sender_name": event_proto.sender_name or "",
        "conv_id": event_proto.conv_id or "",
        "group_name": event_proto.group_name or "",
        "is_group": event_proto.is_group,
        "is_at_bot": event_proto.is_at_bot,
        "is_admin": event_proto.is_admin,
        "message_str": event_proto.message_str or "",
        "plain_text": event_proto.plain_text or "",
        "raw_message": event_proto.raw_message or "",
        "message_id": event_proto.message_id or "",
        "timestamp": event_proto.timestamp or 0,
        "metadata": metadata,
        "chain": chain,
    }
