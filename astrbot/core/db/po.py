"""持久化实体定义（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.db.po`（v4.27.3）中的全部 PO 模型字段。
本模块为纯数据结构（dataclass / 普通类 / TypedDict），不含数据库访问
逻辑——宿主数据由 Go 侧管理，不依赖 SQLModel/pydantic。字段取原版主要
字段，全部带默认值，保证属性访问不抛异常、可无参实例化。

原版中由 SQLModel 提供的 `Field`/`Index`/`UniqueConstraint`/`JSON`/`Text`
及 sqlalchemy 的 `desc` 均为表结构描述，SDK 无需使用，故不提供。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TypedDict

from astrbot.core.utils.deprecation import deprecated


def _utcnow() -> datetime:
    """返回当前 UTC 时间（时间戳字段的默认值工厂）。"""
    return datetime.now(timezone.utc)


@dataclass
class TimestampMixin:
    """时间戳混入（对齐原版 SQLModel TimestampMixin）。

    created_at / updated_at 为 UTC 时间，实例化时自动取当前时间。
    """

    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


@dataclass
class PlatformStat(TimestampMixin):
    """平台使用统计记录（对齐原版 platform_stats 表）。"""

    id: int | None = field(default=None)
    timestamp: datetime = field(default_factory=_utcnow)
    platform_id: str = ""
    """平台实例 ID"""
    platform_type: str = ""
    """平台类型，如 aiocqhttp / slack 等"""
    count: int = 0


@dataclass
class ProviderStat(TimestampMixin):
    """Provider 单次请求统计记录（对齐原版 provider_stats 表）。

    provider_id/provider_model 为实际使用的提供商与模型，token_* 为用量。
    """

    id: int | None = field(default=None)
    agent_type: str = "internal"
    status: str = "completed"
    umo: str = ""
    conversation_id: str | None = None
    provider_id: str = ""
    provider_model: str | None = None
    token_input_other: int = 0
    token_input_cached: int = 0
    token_output: int = 0
    start_time: float = 0.0
    end_time: float = 0.0
    time_to_first_token: float = 0.0


@dataclass
class ConversationV2(TimestampMixin):
    """会话 v2（对齐原版 conversations 表）。"""

    inner_conversation_id: int | None = field(default=None)
    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    platform_id: str = ""
    user_id: str = ""
    content: list | None = None
    """OpenAI 格式的消息列表（list[dict]）"""
    title: str | None = None
    persona_id: str | None = None
    token_usage: int = 0
    """对话总 token 数量；为 0 时表示未知，将使用估算计数"""


@dataclass
class PersonaFolder(TimestampMixin):
    """人物卡文件夹（对齐原版 persona_folders 表），支持递归层级。"""

    id: int | None = field(default=None)
    folder_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    parent_id: str | None = None
    """父文件夹 ID，None 表示根目录"""
    description: str | None = None
    sort_order: int = 0


@dataclass
class Persona(TimestampMixin):
    """人格数据（纯数据结构，字段对齐 Python 本体的 Persona 表）。

    Go 宿主侧的人格由宿主持久化管理，插件仅经此类型做类型标注 /
    数据搬运，不涉及 SQLModel 表结构。
    """

    persona_id: str = ""
    """人格唯一标识"""
    name: str = ""
    """人格名称"""
    type: str = ""
    """人格类型"""
    system_prompt: str = ""
    """人格的系统提示词"""
    begin_dialogs: list | None = None
    """一组用于开场对话的字符串列表"""
    tools: list | None = None
    """None 表示使用所有工具，空列表表示不使用任何工具，否则为工具名列表"""
    skills: list | None = None
    """None 表示使用所有 Skills，空列表表示不使用任何 Skills，否则为 Skill 名列表"""
    custom_error_message: str | None = None
    """可选的自定义报错回复信息，配置后优先发送给最终用户"""
    folder_id: str | None = None
    """所属文件夹 ID，NULL 表示在根目录"""
    sort_order: int = 0
    """排序顺序"""


@dataclass
class CronJob(TimestampMixin):
    """Cron 任务定义（对齐原版 cron_jobs 表）。"""

    id: int | None = field(default=None)
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str | None = None
    job_type: str = ""
    """任务类型：basic / active_agent"""
    cron_expression: str | None = None
    timezone: str | None = None
    payload: dict = field(default_factory=dict)
    enabled: bool = True
    persistent: bool = True
    run_once: bool = False
    status: str = "scheduled"
    last_run_at: datetime | None = None
    next_run_time: datetime | None = None
    last_error: str | None = None


@dataclass
class Preference(TimestampMixin):
    """偏好设置（对齐原版 preferences 表）。

    scope 如 'global' / 'umo' / 'plugin'；value 为 dict，实际值在
    value["val"] 中（对齐原版 range_get 语义）。
    """

    id: int | None = field(default=None)
    scope: str = ""
    scope_id: str = ""
    key: str = ""
    value: dict = field(default_factory=dict)


@dataclass
class PlatformMessageHistory(TimestampMixin):
    """平台消息历史（对齐原版 platform_message_history 表）。

    独立于 LLM 会话检查点存储，与 utils/message_history_manager.py 中的
    同名类互不干扰（模块不同），此处保持 db 版字段。
    """

    id: int | None = field(default=None)
    platform_id: str = ""
    user_id: str = ""
    """平台上的群/用户 ID"""
    sender_id: str | None = None
    """平台上的发送者 ID"""
    sender_name: str | None = None
    """平台上的发送者名称"""
    content: dict = field(default_factory=dict)
    """消息链 dict"""
    llm_checkpoint_id: str | None = None


@dataclass
class WebChatThread(TimestampMixin):
    """WebChat 侧线程（对齐原版 webchat_threads 表），从选中的回复创建。"""

    id: int | None = field(default=None)
    thread_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    creator: str = ""
    parent_session_id: str = ""
    parent_message_id: int = 0
    base_checkpoint_id: str = ""
    selected_text: str = ""


@dataclass
class PlatformSession(TimestampMixin):
    """平台会话（对齐原版 platform_sessions 表）。

    会话代表某个用户在某个平台上的聊天窗口，可关联多个对话。
    """

    inner_id: int | None = field(default=None)
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    platform_id: str = "webchat"
    """平台标识，如 webchat / qq / discord"""
    creator: str = ""
    """会话创建者用户名"""
    display_name: str | None = None
    """会话显示名称"""
    is_group: int = 0
    """0 私聊，1 群聊"""


@dataclass
class UmoAlias(TimestampMixin):
    """统一消息来源别名（对齐原版 umo_aliases 表）。"""

    id: int | None = field(default=None)
    umo: str = ""
    creator_sender_id: str = ""
    auto_name: str | None = None
    user_alias: str | None = None


@dataclass
class Attachment(TimestampMixin):
    """消息附件（对齐原版 attachments 表），图片/文件等媒体。"""

    inner_attachment_id: int | None = field(default=None)
    attachment_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    path: str = ""
    """磁盘文件路径"""
    type: str = ""
    """文件类型，如 image / file"""
    mime_type: str = ""
    """MIME 类型"""


@dataclass
class ApiKey(TimestampMixin):
    """API 密钥（对齐原版 api_keys 表），供外部开发者访问开放 API。"""

    inner_id: int | None = field(default=None)
    key_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    key_hash: str = ""
    key_prefix: str = ""
    scopes: list | None = None
    created_by: str = ""
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


@dataclass
class DashboardTrustedDevice(TimestampMixin):
    """受信设备令牌（对齐原版 dashboard_trusted_devices 表）。

    用于在限定时间内跳过 TOTP 验证。
    """

    id: int | None = field(default=None)
    token_hash: str = ""
    totp_secret_hash: str = ""
    expires_at: datetime = field(default_factory=_utcnow)


@dataclass
class ChatUIProject(TimestampMixin):
    """ChatUI 项目（对齐原版 chatui_projects 表），用于组织会话。"""

    inner_id: int | None = field(default=None)
    project_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    creator: str = ""
    """项目创建者用户名"""
    emoji: str | None = "📁"
    title: str = ""
    description: str | None = None
    workspace_type: str = "session"
    """工作区模式：session / project / custom"""
    workspace_path: str | None = None
    """自定义工作区路径"""


@dataclass
class SessionProjectRelation:
    """会话-项目关系（对齐原版 session_project_relations 表，无时间戳字段）。"""

    id: int | None = field(default=None)
    session_id: str = ""
    """PlatformSession.session_id"""
    project_id: str = ""
    """ChatUIProject.project_id"""


@dataclass
class CommandConfig(TimestampMixin):
    """命令配置（对齐原版 command_configs 表），仪表盘管理用。"""

    handler_full_name: str = ""
    plugin_name: str = ""
    module_path: str = ""
    original_command: str = ""
    resolved_command: str | None = None
    enabled: bool = True
    keep_original_alias: bool = False
    conflict_key: str | None = None
    resolution_strategy: str | None = None
    note: str | None = None
    extra_data: dict | None = None
    auto_managed: bool = False


@dataclass
class CommandConflict(TimestampMixin):
    """命令冲突记录（对齐原版 command_conflicts 表），追踪重复命令名。"""

    id: int | None = field(default=None)
    conflict_key: str = ""
    handler_full_name: str = ""
    plugin_name: str = ""
    status: str = "pending"
    resolution: str | None = None
    resolved_command: str | None = None
    note: str | None = None
    extra_data: dict | None = None
    auto_generated: bool = False


@dataclass
class Conversation:
    """LLM 对话类（对齐 Python 本体 v4.27.3 po.py 的 Conversation 字段）。

    原版为必填字段（platform_id/user_id/cid）的 dataclass；SDK 版额外
    兼容旧插件使用的 conversation_id/name/handler_module_name/
    current_chain_id 等字段，全部字段带默认值，构造时不抛异常。

    对于 WebChat，history 存储了包括指令、回复、图片等在内的所有消息；
    对于其他平台，不存储非 LLM 的回复（已在各平台保存）。v4.0.0 起
    WebChat 历史迁移至 PlatformMessageHistory 表。
    """

    conversation_id: str = ""
    """对话 ID（uuid 字符串，兼容字段）"""
    platform_id: str = ""
    user_id: str = ""
    cid: str = ""
    """对话 ID，uuid 格式字符串"""
    name: str = ""
    """会话名称（兼容字段）"""
    persona_id: str | None = ""
    handler_module_name: str = ""
    """处理模块名（兼容字段）"""
    current_chain_id: str = ""
    """当前会话链 ID（兼容字段）"""
    history: str = ""
    """字符串格式的对话列表"""
    title: str | None = ""
    created_at: int = 0
    updated_at: int = 0
    token_usage: int = 0
    """对话的总 token 数量，0 表示未知"""


class Personality(TypedDict):
    """LLM 人格类（字段对齐 Python 本体 po.py 的 Personality）。

    在 v4.0.0 版本及之后，推荐使用 Persona 类。mood_imitation_dialogs
    字段已被废弃，这里保留以兼容旧插件的类型标注。
    """

    prompt: str
    name: str
    begin_dialogs: list[str]
    mood_imitation_dialogs: list[str]
    """情感模拟对话预设。在 v4.0.0 版本及之后，已被废弃。"""
    tools: list[str] | None
    """工具列表。None 表示使用所有工具，空列表表示不使用任何工具"""
    skills: list[str] | None
    """Skills 列表。None 表示使用所有 Skills，空列表表示不使用任何 Skills"""
    custom_error_message: str | None
    """可选的人格自定义报错回复信息。配置后将优先发送给最终用户。"""

    # cache
    _begin_dialogs_processed: list[dict]
    _mood_imitation_dialogs_processed: str


# ====
# 已废弃，未来版本将移除（对齐原版 po.py 尾部的 @deprecated 类）。
# ====


@deprecated(reason="Use PlatformStat instead.", version="4.0.0")
@dataclass
class Platform:
    """平台使用统计数据（已废弃）。"""

    name: str = ""
    count: int = 0
    timestamp: int = 0


@deprecated(reason="Use get_platform_stats() instead.", version="4.0.0")
@dataclass
class Stats:
    """总统计（已废弃）。"""

    platform: list = field(default_factory=list)