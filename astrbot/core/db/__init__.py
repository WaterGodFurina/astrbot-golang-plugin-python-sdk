"""astrbot.core.db —— 数据库实体薄封装（Go 宿主兼容运行时）。

Python 本体 `astrbot.core.db` 是 SQLModel 数据库实体层；Go 宿主的数据
持久化在宿主侧完成，插件一般不需要操作数据库。这里提供插件常用的
import 路径（如 `from astrbot.core.db import BaseDatabase, Conversation`）
所需的全部 PO 模型与 BaseDatabase 接口。

BaseDatabase 为降级实现：不做抽象约束（插件可继承后选择性覆写），
所有方法提供“不抛异常”的默认行为——查询返回空列表/None/0，写入为
no-op，保证插件在 Go 宿主下不因数据库调用而崩溃。
"""
from astrbot.core.db.po import (
    ApiKey,
    Attachment,
    ChatUIProject,
    CommandConfig,
    CommandConflict,
    Conversation,
    ConversationV2,
    CronJob,
    Persona,
    PersonaFolder,
    Personality,
    Platform,
    PlatformMessageHistory,
    PlatformSession,
    PlatformStat,
    Preference,
    ProviderStat,
    SessionProjectRelation,
    Stats,
    TimestampMixin,
    UmoAlias,
    WebChatThread,
)

# 模块级单例 sentinel（对齐原版 astrbot.core.sentinels.NOT_GIVEN：
# 用于区分“未传参”与“显式传 None”）。
NOT_GIVEN = object()


class _AsyncSessionContext:
    """Go 宿主降级的“数据库会话”异步上下文管理器。

    进入时（首次）执行 initialize，会话本体为 None——插件 `async with
    db.get_db() as session:` 可正常使用，但拿不到真实数据库会话。
    """

    def __init__(self, db: "BaseDatabase") -> None:
        self._db = db

    async def __aenter__(self) -> None:
        if not self._db.inited:
            await self._db.initialize()
            self._db.inited = True
        return None

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class BaseDatabase:
    """数据库接口基类（Go 宿主兼容降级版）。

    原版为 abc.ABC 抽象类，方法均标注 @abstractmethod；SDK 不设抽象约束，
    所有方法提供“不抛异常”的降级实现，插件可继承后选择性覆写。
    """

    DATABASE_URL = ""

    def __init__(self) -> None:
        self.inited = False

    async def initialize(self) -> None:
        """初始化数据库连接（SDK 降级：no-op）。"""

    def get_db(self) -> _AsyncSessionContext:
        """返回一个“数据库会话”上下文（SDK 降级：会话为 None）。

        用法与本体一致：`async with db.get_db() as session:`。
        """
        return _AsyncSessionContext(self)

    # ── 旧版（已废弃）统计接口 ──────────────────────────────────────

    def get_base_stats(self, offset_sec: int = 86400) -> Stats:
        """获取基础统计数据（SDK 降级：返回空统计）。"""
        return Stats()

    def get_total_message_count(self) -> int:
        """获取总消息数（SDK 降级：返回 0）。"""
        return 0

    def get_grouped_base_stats(self, offset_sec: int = 86400) -> Stats:
        """获取基础统计数据（合并）（SDK 降级：返回空统计）。"""
        return Stats()

    # ── 平台统计 ────────────────────────────────────────────────────

    async def insert_platform_stats(
        self,
        platform_id: str,
        platform_type: str,
        count: int = 1,
        timestamp=None,
    ) -> None:
        """插入平台统计记录（SDK 降级：no-op）。"""

    async def count_platform_stats(self) -> int:
        """统计平台统计记录条数（SDK 降级：返回 0）。"""
        return 0

    async def get_platform_stats(self, offset_sec: int = 86400) -> list[PlatformStat]:
        """获取 offset 秒内的平台统计（SDK 降级：返回空列表）。"""
        return []

    async def insert_provider_stat(
        self,
        *,
        umo: str,
        provider_id: str,
        provider_model: str | None = None,
        conversation_id: str | None = None,
        status: str = "completed",
        stats: dict | None = None,
        agent_type: str = "internal",
    ) -> ProviderStat:
        """插入 provider 统计记录（SDK 降级：返回空 ProviderStat）。"""
        return ProviderStat()

    # ── 会话（Conversation）─────────────────────────────────────────

    async def get_conversations(
        self,
        user_id: str | None = None,
        platform_id: str | None = None,
    ) -> list[ConversationV2]:
        """获取指定用户/平台的会话列表（SDK 降级：返回空列表）。"""
        return []

    async def get_conversation_by_id(self, cid: str) -> ConversationV2 | None:
        """按 ID 获取会话（SDK 降级：返回 None）。"""
        return None

    async def get_all_conversations(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> list[ConversationV2]:
        """分页获取全部会话（SDK 降级：返回空列表）。"""
        return []

    async def get_filtered_conversations(
        self,
        page: int = 1,
        page_size: int = 20,
        platform_ids: list[str] | None = None,
        search_query: str = "",
        include_history: bool = True,
        **kwargs,
    ) -> tuple[list[ConversationV2], int]:
        """按平台/关键词过滤会话（SDK 降级：返回空列表与 0）。"""
        return [], 0

    async def create_conversation(
        self,
        user_id: str,
        platform_id: str,
        content: list[dict] | None = None,
        title: str | None = None,
        persona_id: str | None = None,
        cid: str | None = None,
        created_at=None,
        updated_at=None,
    ) -> ConversationV2:
        """创建会话（SDK 降级：返回空 ConversationV2）。"""
        return ConversationV2()

    async def update_conversation(
        self,
        cid: str,
        title: str | None = None,
        persona_id: str | None = None,
        content: list[dict] | None = None,
        token_usage: int | None = None,
    ) -> None:
        """更新会话（SDK 降级：no-op）。"""

    async def delete_conversation(self, cid: str) -> None:
        """删除会话（SDK 降级：no-op）。"""

    async def delete_conversations_by_user_id(self, user_id: str) -> None:
        """删除指定用户的全部会话（SDK 降级：no-op）。"""

    # ── 平台消息历史 ────────────────────────────────────────────────

    async def insert_platform_message_history(
        self,
        platform_id: str,
        user_id: str,
        content: dict,
        sender_id: str | None = None,
        sender_name: str | None = None,
        llm_checkpoint_id: str | None = None,
        max_messages: int | None = None,
    ) -> PlatformMessageHistory:
        """插入平台消息历史（SDK 降级：返回空 PlatformMessageHistory）。"""
        return PlatformMessageHistory()

    async def update_platform_message_history(
        self,
        message_id: int,
        content: dict | None = None,
        llm_checkpoint_id: str | None = None,
    ) -> None:
        """更新平台消息历史（SDK 降级：no-op）。"""

    async def delete_platform_message_history_by_id(self, message_id: int) -> None:
        """按 ID 删除平台消息历史（SDK 降级：no-op）。"""

    async def delete_platform_message_offset(
        self,
        platform_id: str,
        user_id: str,
        offset_sec: int = 86400,
    ) -> None:
        """删除超过指定时间偏移的旧平台消息历史（SDK 降级：no-op）。"""

    async def get_platform_message_history(
        self,
        platform_id: str,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> list[PlatformMessageHistory]:
        """获取指定用户的平台消息历史（SDK 降级：返回空列表）。"""
        return []

    async def get_platform_message_history_by_id(
        self,
        message_id: int,
    ) -> PlatformMessageHistory | None:
        """按 ID 获取平台消息历史（SDK 降级：返回 None）。"""
        return None

    # ── WebChat 侧线程 ──────────────────────────────────────────────

    async def create_webchat_thread(
        self,
        creator: str,
        parent_session_id: str,
        parent_message_id: int,
        base_checkpoint_id: str,
        selected_text: str,
    ) -> WebChatThread:
        """创建 WebChat 侧线程（SDK 降级：返回空 WebChatThread）。"""
        return WebChatThread()

    async def get_webchat_thread_by_id(
        self,
        thread_id: str,
    ) -> WebChatThread | None:
        """按 thread_id 获取侧线程（SDK 降级：返回 None）。"""
        return None

    async def get_webchat_threads_by_parent_session(
        self,
        parent_session_id: str,
        creator: str | None = None,
    ) -> list[WebChatThread]:
        """获取父会话下的侧线程列表（SDK 降级：返回空列表）。"""
        return []

    async def get_webchat_thread_by_parent_message_and_text(
        self,
        parent_session_id: str,
        parent_message_id: int,
        selected_text: str,
        creator: str | None = None,
    ) -> WebChatThread | None:
        """按父消息与选中文本获取侧线程（SDK 降级：返回 None）。"""
        return None

    async def delete_webchat_thread(self, thread_id: str) -> None:
        """删除侧线程（SDK 降级：no-op）。"""

    async def delete_webchat_threads_by_parent_session(
        self,
        parent_session_id: str,
    ) -> list[str]:
        """删除父会话下的全部侧线程（SDK 降级：返回空列表）。"""
        return []

    async def delete_webchat_threads_by_parent_message_ids(
        self,
        parent_session_id: str,
        parent_message_ids: list[int],
    ) -> list[str]:
        """删除指定父消息 ID 关联的侧线程（SDK 降级：返回空列表）。"""
        return []

    # ── 附件 ────────────────────────────────────────────────────────

    async def insert_attachment(self, path: str, type: str, mime_type: str) -> Attachment:
        """插入附件（SDK 降级：返回空 Attachment）。"""
        return Attachment()

    async def get_attachment_by_id(self, attachment_id: str) -> Attachment | None:
        """按 ID 获取附件（SDK 降级：返回 None）。"""
        return None

    async def get_attachments(self, attachment_ids: list[str]) -> list[Attachment]:
        """批量获取附件（SDK 降级：返回空列表）。"""
        return []

    async def delete_attachment(self, attachment_id: str) -> bool:
        """删除附件（SDK 降级：返回 False，表示未删除）。"""
        return False

    async def delete_attachments(self, attachment_ids: list[str]) -> int:
        """批量删除附件（SDK 降级：返回 0）。"""
        return 0

    # ── API 密钥 ────────────────────────────────────────────────────

    async def create_api_key(
        self,
        name: str,
        key_hash: str,
        key_prefix: str,
        scopes: list[str] | None,
        created_by: str,
        expires_at=None,
    ) -> ApiKey:
        """创建 API 密钥（SDK 降级：返回空 ApiKey）。"""
        return ApiKey()

    async def list_api_keys(self) -> list[ApiKey]:
        """列出全部 API 密钥（SDK 降级：返回空列表）。"""
        return []

    async def get_api_key_by_id(self, key_id: str) -> ApiKey | None:
        """按 key_id 获取 API 密钥（SDK 降级：返回 None）。"""
        return None

    async def get_active_api_key_by_hash(self, key_hash: str) -> ApiKey | None:
        """按哈希获取有效（未吊销未过期）API 密钥（SDK 降级：返回 None）。"""
        return None

    async def touch_api_key(self, key_id: str) -> None:
        """更新密钥最后使用时间（SDK 降级：no-op）。"""

    async def revoke_api_key(self, key_id: str) -> bool:
        """吊销 API 密钥（SDK 降级：返回 False）。"""
        return False

    async def delete_api_key(self, key_id: str) -> bool:
        """删除 API 密钥（SDK 降级：返回 False）。"""
        return False

    # ── 人物卡 ──────────────────────────────────────────────────────

    async def insert_persona(
        self,
        persona_id: str,
        system_prompt: str,
        begin_dialogs: list[str] | None = None,
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        custom_error_message: str | None = None,
        folder_id: str | None = None,
        sort_order: int = 0,
    ) -> Persona:
        """插入人物卡（SDK 降级：返回空 Persona）。"""
        return Persona()

    async def get_persona_by_id(self, persona_id: str) -> Persona | None:
        """按 ID 获取人物卡（SDK 降级：返回 None）。"""
        return None

    async def get_personas(self) -> list[Persona]:
        """获取全部人物卡（SDK 降级：返回空列表）。"""
        return []

    async def update_persona(
        self,
        persona_id: str,
        system_prompt: str | None = None,
        begin_dialogs: list[str] | None = None,
        tools=None,
        skills=None,
        custom_error_message=None,
    ) -> Persona | None:
        """更新人物卡（SDK 降级：返回 None）。"""
        return None

    async def delete_persona(self, persona_id: str) -> None:
        """删除人物卡（SDK 降级：no-op）。"""

    # ── 人物卡文件夹 ────────────────────────────────────────────────

    async def insert_persona_folder(
        self,
        name: str,
        parent_id: str | None = None,
        description: str | None = None,
        sort_order: int = 0,
    ) -> PersonaFolder:
        """插入人物卡文件夹（SDK 降级：返回空 PersonaFolder）。"""
        return PersonaFolder()

    async def get_persona_folder_by_id(self, folder_id: str) -> PersonaFolder | None:
        """按 folder_id 获取文件夹（SDK 降级：返回 None）。"""
        return None

    async def get_persona_folders(
        self, parent_id: str | None = None
    ) -> list[PersonaFolder]:
        """获取文件夹列表，可按 parent_id 过滤（SDK 降级：返回空列表）。"""
        return []

    async def get_all_persona_folders(self) -> list[PersonaFolder]:
        """获取全部文件夹（SDK 降级：返回空列表）。"""
        return []

    async def update_persona_folder(
        self,
        folder_id: str,
        name: str | None = None,
        parent_id=None,
        description=None,
        sort_order: int | None = None,
    ) -> PersonaFolder | None:
        """更新文件夹（SDK 降级：返回 None）。"""
        return None

    async def delete_persona_folder(self, folder_id: str) -> None:
        """删除文件夹（SDK 降级：no-op）。"""

    async def move_persona_to_folder(
        self, persona_id: str, folder_id: str | None
    ) -> Persona | None:
        """移动人物卡到文件夹（SDK 降级：返回 None）。"""
        return None

    async def get_personas_by_folder(
        self, folder_id: str | None = None
    ) -> list[Persona]:
        """获取文件夹下的人物卡列表（SDK 降级：返回空列表）。"""
        return []

    async def batch_update_sort_order(self, items: list[dict]) -> None:
        """批量更新人物卡/文件夹排序（SDK 降级：no-op）。"""

    # ── 偏好 ────────────────────────────────────────────────────────

    async def insert_preference_or_update(
        self,
        scope: str,
        scope_id: str,
        key: str,
        value: dict,
    ) -> Preference:
        """插入或更新偏好（SDK 降级：返回 Preference 占位）。"""
        return Preference(scope=scope, scope_id=scope_id, key=key, value=value)

    async def get_preference(self, scope: str, scope_id: str, key: str) -> Preference | None:
        """获取单条偏好（SDK 降级：返回 None）。"""
        return None

    async def get_preferences(
        self,
        scope: str | None = None,
        scope_id: str | None = None,
        key: str | None = None,
    ) -> list[Preference]:
        """获取偏好列表，可按 scope/scope_id/key 过滤（SDK 降级：返回空列表）。"""
        return []

    async def remove_preference(self, scope: str, scope_id: str, key: str) -> None:
        """删除偏好（SDK 降级：no-op）。"""

    async def clear_preferences(self, scope: str, scope_id: str) -> None:
        """清空指定 scope 的偏好（SDK 降级：no-op）。"""

    # ── 命令配置与冲突 ──────────────────────────────────────────────

    async def get_command_configs(self) -> list[CommandConfig]:
        """获取全部命令配置（SDK 降级：返回空列表）。"""
        return []

    async def get_command_config(self, handler_full_name: str) -> CommandConfig | None:
        """按 handler 获取命令配置（SDK 降级：返回 None）。"""
        return None

    async def upsert_command_config(
        self,
        handler_full_name: str,
        plugin_name: str,
        module_path: str,
        original_command: str,
        *,
        resolved_command: str | None = None,
        enabled: bool | None = None,
        keep_original_alias: bool | None = None,
        conflict_key: str | None = None,
        resolution_strategy: str | None = None,
        note: str | None = None,
        extra_data: dict | None = None,
        auto_managed: bool | None = None,
    ) -> CommandConfig:
        """创建或更新命令配置（SDK 降级：返回空 CommandConfig）。"""
        return CommandConfig()

    async def delete_command_config(self, handler_full_name: str) -> None:
        """删除命令配置（SDK 降级：no-op）。"""

    async def delete_command_configs(self, handler_full_names: list[str]) -> None:
        """批量删除命令配置（SDK 降级：no-op）。"""

    async def list_command_conflicts(
        self,
        status: str | None = None,
    ) -> list[CommandConflict]:
        """列出命令冲突记录（SDK 降级：返回空列表）。"""
        return []

    async def upsert_command_conflict(
        self,
        conflict_key: str,
        handler_full_name: str,
        plugin_name: str,
        *,
        status: str | None = None,
        resolution: str | None = None,
        resolved_command: str | None = None,
        note: str | None = None,
        extra_data: dict | None = None,
        auto_generated: bool | None = None,
    ) -> CommandConflict:
        """创建或更新冲突记录（SDK 降级：返回空 CommandConflict）。"""
        return CommandConflict()

    async def delete_command_conflicts(self, ids: list[int]) -> None:
        """删除冲突记录（SDK 降级：no-op）。"""

    async def get_session_conversations(
        self,
        page: int = 1,
        page_size: int = 20,
        search_query: str | None = None,
        platform: str | None = None,
    ) -> tuple[list[dict], int]:
        """分页获取会话对话（SDK 降级：返回空列表与 0）。"""
        return [], 0

    # ── Cron 任务 ───────────────────────────────────────────────────

    async def create_cron_job(
        self,
        name: str,
        job_type: str,
        cron_expression: str | None,
        *,
        timezone: str | None = None,
        payload: dict | None = None,
        description: str | None = None,
        enabled: bool = True,
        persistent: bool = True,
        run_once: bool = False,
        status: str | None = None,
        job_id: str | None = None,
    ) -> CronJob:
        """创建 Cron 任务（SDK 降级：返回空 CronJob）。"""
        return CronJob()

    async def update_cron_job(
        self,
        job_id: str,
        *,
        name: str | None = None,
        cron_expression: str | None = None,
        timezone: str | None = None,
        payload: dict | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        persistent: bool | None = None,
        run_once: bool | None = None,
        status: str | None = None,
        next_run_time=None,
        last_run_at=None,
        last_error: str | None = None,
    ) -> CronJob | None:
        """更新 Cron 任务（SDK 降级：返回 None）。"""
        return None

    async def delete_cron_job(self, job_id: str) -> None:
        """删除 Cron 任务（SDK 降级：no-op）。"""

    async def get_cron_job(self, job_id: str) -> CronJob | None:
        """按 job_id 获取 Cron 任务（SDK 降级：返回 None）。"""
        return None

    async def list_cron_jobs(self, job_type: str | None = None) -> list[CronJob]:
        """列出 Cron 任务，可按 job_type 过滤（SDK 降级：返回空列表）。"""
        return []

    # ── 平台会话 ────────────────────────────────────────────────────

    async def create_platform_session(
        self,
        creator: str,
        platform_id: str = "webchat",
        session_id: str | None = None,
        display_name: str | None = None,
        is_group: int = 0,
    ) -> PlatformSession:
        """创建平台会话（SDK 降级：返回空 PlatformSession）。"""
        return PlatformSession()

    async def get_platform_session_by_id(
        self, session_id: str
    ) -> PlatformSession | None:
        """按 session_id 获取平台会话（SDK 降级：返回 None）。"""
        return None

    async def get_platform_sessions_by_ids(
        self, session_ids: list[str]
    ) -> list[PlatformSession]:
        """按 ID 列表批量获取平台会话（SDK 降级：返回空列表）。"""
        return []

    async def get_platform_sessions_by_creator(
        self,
        creator: str,
        platform_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> list[dict]:
        """按创建者获取平台会话（SDK 降级：返回空列表）。"""
        return []

    async def get_platform_sessions_by_creator_paginated(
        self,
        creator: str,
        platform_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
        exclude_project_sessions: bool = False,
    ) -> tuple[list[dict], int]:
        """分页获取创建者的平台会话（SDK 降级：返回空列表与 0）。"""
        return [], 0

    async def update_platform_session(
        self,
        session_id: str,
        display_name: str | None = None,
    ) -> None:
        """更新平台会话（SDK 降级：no-op）。"""

    async def delete_platform_session(self, session_id: str) -> None:
        """删除平台会话（SDK 降级：no-op）。"""

    # ── UMO 别名 ────────────────────────────────────────────────────

    async def upsert_umo_alias(
        self,
        umo: str,
        creator_sender_id: str,
        auto_name: str | None,
        user_alias: str | None,
    ) -> UmoAlias:
        """创建或更新 UMO 别名（SDK 降级：返回空 UmoAlias）。"""
        return UmoAlias()

    async def get_umo_alias(self, umo: str) -> UmoAlias | None:
        """获取单个 UMO 别名（SDK 降级：返回 None）。"""
        return None

    async def get_umo_aliases(self, umos: list[str] | None = None) -> list[UmoAlias]:
        """获取 UMO 别名列表，可按 UMO 列表过滤（SDK 降级：返回空列表）。"""
        return []

    # ── ChatUI 项目 ─────────────────────────────────────────────────

    async def create_chatui_project(
        self,
        creator: str,
        title: str,
        emoji: str | None = "📁",
        description: str | None = None,
        workspace_type: str = "session",
        workspace_path: str | None = None,
    ) -> ChatUIProject:
        """创建 ChatUI 项目（SDK 降级：返回空 ChatUIProject）。"""
        return ChatUIProject()

    async def get_chatui_project_by_id(self, project_id: str) -> ChatUIProject | None:
        """按 project_id 获取项目（SDK 降级：返回 None）。"""
        return None

    async def get_chatui_projects_by_creator(
        self,
        creator: str,
        page: int = 1,
        page_size: int = 100,
    ) -> list[ChatUIProject]:
        """按创建者获取项目列表（SDK 降级：返回空列表）。"""
        return []

    async def update_chatui_project(
        self,
        project_id: str,
        title: str | None = None,
        emoji: str | None = None,
        description: str | None = None,
        workspace_type: str | None = None,
        workspace_path: str | None = None,
    ) -> None:
        """更新项目（SDK 降级：no-op）。"""

    async def delete_chatui_project(self, project_id: str) -> None:
        """删除项目（SDK 降级：no-op）。"""

    async def add_session_to_project(
        self,
        session_id: str,
        project_id: str,
    ) -> SessionProjectRelation:
        """将会话加入项目（SDK 降级：返回空 SessionProjectRelation）。"""
        return SessionProjectRelation()

    async def remove_session_from_project(self, session_id: str) -> None:
        """将会话移出项目（SDK 降级：no-op）。"""

    async def get_project_sessions(
        self,
        project_id: str,
        page: int = 1,
        page_size: int = 100,
    ) -> list[PlatformSession]:
        """获取项目下的全部会话（SDK 降级：返回空列表）。"""
        return []

    async def get_project_by_session(
        self, session_id: str, creator: str
    ) -> ChatUIProject | None:
        """获取会话所属项目（SDK 降级：返回 None）。"""
        return None


__all__ = [
    "ApiKey",
    "Attachment",
    "BaseDatabase",
    "ChatUIProject",
    "CommandConfig",
    "CommandConflict",
    "Conversation",
    "ConversationV2",
    "CronJob",
    "NOT_GIVEN",
    "Persona",
    "PersonaFolder",
    "Personality",
    "Platform",
    "PlatformMessageHistory",
    "PlatformSession",
    "PlatformStat",
    "Preference",
    "ProviderStat",
    "SessionProjectRelation",
    "Stats",
    "TimestampMixin",
    "UmoAlias",
    "WebChatThread",
]