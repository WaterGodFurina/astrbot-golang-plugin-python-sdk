"""
slack_sdk 兼容层（宿主桥骨架）。

对齐插件常用的编程面：WebClient（chat_postMessage/chat_update 等）、
WebhookClient。网络层由宿主 Go 的 slack 适配器承担：发送经
HostBridge.call_action("slack", api, params) 转发宿主。
"""
from __future__ import annotations

from astrbot._bridge.host import get_bridge

__all__ = ["WebClient", "WebhookClient"]


class WebClient:
    """Slack Web API 客户端（宿主桥）。token 等参数仅作兼容保留。"""

    def __init__(self, token: str = "", base_url: str = "", **kwargs):
        self.token = token
        self.base_url = base_url

    async def chat_postMessage(self, channel: str, text: str = "", **params) -> dict:
        return await get_bridge().call_action("slack", "chat.postMessage", {
            "channel": channel, "text": text, **params,
        })

    async def chat_update(self, channel: str, ts: str, text: str = "", **params) -> dict:
        return await get_bridge().call_action("slack", "chat.update", {
            "channel": channel, "ts": ts, "text": text, **params,
        })

    async def chat_delete(self, channel: str, ts: str, **params) -> dict:
        return await get_bridge().call_action("slack", "chat.delete", {
            "channel": channel, "ts": ts, **params,
        })

    async def conversations_open(self, users: str | list, **params) -> dict:
        return await get_bridge().call_action("slack", "conversations.open", {
            "users": users, **params,
        })

    async def conversations_info(self, channel: str, **params) -> dict:
        return await get_bridge().call_action("slack", "conversations.info", {
            "channel": channel, **params,
        })

    async def users_info(self, user: str, **params) -> dict:
        return await get_bridge().call_action("slack", "users.info", {
            "user": user, **params,
        })

    async def reactions_add(self, channel: str, name: str, timestamp: str, **params) -> dict:
        return await get_bridge().call_action("slack", "reactions.add", {
            "channel": channel, "name": name, "timestamp": timestamp, **params,
        })

    def __getattr__(self, method: str):
        """未显式实现的方法 → 动态转发 call_action（slack API 点分名）。"""
        if method.startswith("_"):
            raise AttributeError(method)

        async def _call(**params):
            return await get_bridge().call_action("slack", method, params)

        return _call


class WebhookClient:
    """Incoming Webhook 客户端（发送 markdown/text 消息）。"""

    def __init__(self, url: str = "", **kwargs):
        self.url = url

    async def send(self, text: str = "", markdown: bool = True, **params) -> dict:
        return await get_bridge().call_action("slack", "webhook.send", {
            "text": text, "markdown": markdown, **params,
        })
