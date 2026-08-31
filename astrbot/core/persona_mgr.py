"""人格管理器（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.persona_mgr.PersonaManager` 的接口，
人格数据（data/personas.json）由 Go 宿主维护，本模块经 HostService
RPC 反向调用读取/解析。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("astrbot")


class Persona:
    """包装宿主返回的人格 dict，提供属性访问。

    宿主字段约定：persona_id / name / system_prompt / folder_id / is_default。
    `prompt` 属性对齐 Python 本体 v3 配置的 "prompt" 键（system_prompt 的别名）。
    """

    def __init__(self, data: dict | None = None) -> None:
        self._data: dict = data or {}
        self.persona_id: str = str(
            self._data.get("persona_id") or self._data.get("name") or ""
        )
        self.name: str = str(self._data.get("name") or self.persona_id)
        self.system_prompt: str = str(
            self._data.get("system_prompt")
            or self._data.get("prompt")
            or ""
        )
        self.prompt: str = self.system_prompt
        self.folder_id: Any = self._data.get("folder_id")
        self.is_default: bool = bool(self._data.get("is_default"))
        self.begin_dialogs: list = self._data.get("begin_dialogs") or []

    def __getitem__(self, key: str) -> Any:
        return self._data.get(key)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def to_dict(self) -> dict:
        out = dict(self._data)
        out.setdefault("name", self.name)
        out.setdefault("prompt", self.system_prompt)
        return out

    def __repr__(self) -> str:
        return f"Persona(persona_id={self.persona_id!r}, name={self.name!r})"


class PersonaManager:
    def __init__(self, bridge: Any | None = None) -> None:
        # bridge 可以是 HostBridge 实例，也可以是返回 HostBridge 的
        # 可调用对象（Context 传入 self._bridge 保持单桥来源一致）
        self._bridge_getter: Any = bridge
        self.default_persona: str = "default"

    def _bridge(self):
        if self._bridge_getter is None:
            raise RuntimeError("宿主桥未就绪（PersonaManager 未绑定宿主）")
        if callable(self._bridge_getter):
            return self._bridge_getter()
        return self._bridge_getter

    @property
    def personas(self) -> list[Persona]:
        """全部人格列表（属性访问：p.persona_id / p.folder_id / p.prompt）。"""
        try:
            raw = self._bridge().get_personas()
        except Exception as e:
            logger.warning(f"persona_manager.personas 拉取失败: {e}")
            return []
        return [Persona(item) for item in raw]

    @property
    def persona_configs(self) -> list[dict]:
        """原始人格 dict 列表（对齐本体 persona_v3_config：含 name/prompt 键）。"""
        try:
            raw = self._bridge().get_personas()
        except Exception as e:
            logger.warning(f"persona_manager.persona_configs 拉取失败: {e}")
            return []
        return [Persona(item).to_dict() for item in raw]

    async def get_default_persona_v3(
        self,
        umo: str | None = None,
    ) -> dict:
        """获取默认人格（对齐本体返回 Personality（dict 风格，含 "name"））。

        宿主返回 {persona_id, name, system_prompt, ...}；宿主不可用或未配置
        任何人格时回退为 {"name": "default"}。
        """
        try:
            data = await self._bridge().get_default_persona_async(umo or "")
        except Exception as e:
            logger.warning(f"get_default_persona_v3 失败: {e}")
            data = None
        if isinstance(data, dict) and data:
            return data
        return {"name": self.default_persona}

    async def get_folder_tree(self) -> list[dict]:
        """获取文件夹树形结构（宿主返回 folders 列表，含 children 子列表）。"""
        try:
            folders, _personas = await self._bridge().get_persona_tree_async()
        except Exception as e:
            logger.warning(f"get_folder_tree 失败: {e}")
            return []
        return folders or []

    async def resolve_selected_persona(
        self,
        *,
        umo: str,
        conversation_persona_id: str | None = None,
        platform_name: str = "",
        provider_settings: dict | None = None,
    ) -> tuple[str | None, str, str | None, str]:
        """解析当前会话最终生效的人格。

        对齐本体的返回形状（4 元组）：
            (persona_id, persona_name, force_applied_persona_id, persona_prompt)
        插件侧常见解包：
            (persona_id, _, force_applied_persona_id, _) = await resolve_selected_persona(...)

        宿主解析逻辑：会话绑定人格 → Provider 默认人格 → 全局默认人格 → 无
        （persona_id 为 "[%None]"）。
        """
        try:
            data = await self._bridge().resolve_selected_persona_async(
                umo,
                conversation_persona_id or "",
                platform_name or "",
                provider_settings,
            )
        except Exception as e:
            logger.warning(f"resolve_selected_persona 失败: {e}")
            return ("[%None]", "", None, "")
        if not isinstance(data, dict) or not data:
            return ("[%None]", "", None, "")
        persona_id = data.get("persona_id") or "[%None]"
        persona_name = data.get("persona_name") or ""
        force_applied = data.get("force_applied_persona_id") or None
        persona_prompt = data.get("persona_prompt") or ""
        return (persona_id, persona_name, force_applied, persona_prompt)

    @property
    def personas_v3(self) -> list[dict]:
        """v3 人格 dict 列表（对齐本体 persona_v3 语义：含 prompt/name 键）。"""
        return self.persona_configs

    def get_persona_v3_by_id(self, persona_id: str | None) -> dict | None:
        """按 ID 解析 v3 人格对象（对齐本体 get_persona_v3_by_id）。

        - None / 空 id → None；
        - "default" → 返回默认人格（{"name": "default"}）；
        - 否则按 name 在 personas_v3 中查找。
        """
        if not persona_id:
            return None
        if persona_id == "default":
            return {"name": "default", "prompt": "", "system_prompt": ""}
        for persona in self.persona_configs:
            if str(persona.get("name") or "") == persona_id:
                return persona
        return None

    # ── 以下方法对齐本体 PersonaManager 公开方法面 ─────────────────────────

    @staticmethod
    def _flatten_folders(folders: list[dict]) -> list[dict]:
        """递归展平宿主返回的嵌套文件夹树（folders 含 children 子列表）。"""
        out: list[dict] = []

        def _walk(items: list[dict]) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                out.append(item)
                children = item.get("children")
                if isinstance(children, list):
                    _walk(children)

        _walk(folders or [])
        return out

    async def get_persona(self, persona_id: str) -> Persona:
        """获取指定 persona 的信息（对齐本体：不存在时抛 ValueError）。"""
        for persona in self.personas:
            if persona.persona_id == persona_id or persona.name == persona_id:
                return persona
        raise ValueError(f"Persona with ID {persona_id} does not exist.")

    async def get_all_personas(self) -> list[Persona]:
        """获取所有 personas（经宿主 get_personas RPC）。"""
        return self.personas

    async def get_personas_by_folder(self, folder_id: str | None = None) -> list[Persona]:
        """获取指定文件夹中的 personas（folder_id 为 None 表示根目录）。"""
        _folders, personas = await self._bridge().get_persona_tree_async()
        out: list[Persona] = []
        for item in personas or []:
            if not isinstance(item, dict):
                continue
            item_folder = item.get("folder_id")
            if folder_id is None:
                if item_folder:
                    continue
            elif str(item_folder or "") != str(folder_id):
                continue
            out.append(Persona(item))
        return out

    async def get_folder(self, folder_id: str) -> dict | None:
        """获取指定文件夹（对齐本体，返回 folder dict；未找到返回 None）。"""
        folders, _personas = await self._bridge().get_persona_tree_async()
        for folder in self._flatten_folders(folders or []):
            if str(folder.get("folder_id") or "") == str(folder_id):
                return folder
        return None

    async def get_folders(self, parent_id: str | None = None) -> list[dict]:
        """获取文件夹列表（parent_id 为 None 表示根目录下的文件夹）。"""
        folders, _personas = await self._bridge().get_persona_tree_async()
        flat = self._flatten_folders(folders or [])
        out: list[dict] = []
        for folder in flat:
            f_parent = folder.get("parent_id")
            if parent_id is None:
                if f_parent:
                    continue
            elif str(f_parent or "") != str(parent_id):
                continue
            out.append(folder)
        return out

    async def get_all_folders(self) -> list[dict]:
        """获取所有文件夹（展平宿主树形结构）。"""
        folders, _personas = await self._bridge().get_persona_tree_async()
        return self._flatten_folders(folders or [])

    async def create_persona(
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
        """创建新的 persona（签名对齐本体）。

        SDK 降级：宿主未向 Python 插件暴露人格写 RPC，人格管理请经
        宿主 WebUI 完成。调用时抛出 RuntimeError 而非 AttributeError。
        """
        raise RuntimeError(
            "SDK 不支持创建人格：人格数据由 Go 宿主维护，请使用宿主管理界面。"
        )

    async def delete_persona(self, persona_id: str) -> None:
        """删除指定 persona（签名对齐本体，写操作由宿主处理）。

        Raises:
            RuntimeError: 宿主未暴露人格写 RPC。
        """
        raise RuntimeError(
            "SDK 不支持删除人格：人格数据由 Go 宿主维护，请使用宿主管理界面。"
        )

    async def update_persona(
        self,
        persona_id: str,
        system_prompt: str | None = None,
        begin_dialogs: list[str] | None = None,
        tools=None,
        skills=None,
        custom_error_message=None,
    ) -> None:
        """更新指定 persona 的信息（签名对齐本体，写操作由宿主处理）。

        Raises:
            RuntimeError: 宿主未暴露人格写 RPC。
        """
        raise RuntimeError(
            "SDK 不支持更新人格：人格数据由 Go 宿主维护，请使用宿主管理界面。"
        )

    async def move_persona_to_folder(
        self, persona_id: str, folder_id: str | None
    ) -> None:
        """移动 persona 到指定文件夹（签名对齐本体，写操作由宿主处理）。

        Raises:
            RuntimeError: 宿主未暴露人格写 RPC。
        """
        raise RuntimeError(
            "SDK 不支持移动人格：人格数据由 Go 宿主维护，请使用宿主管理界面。"
        )

    async def create_folder(
        self,
        name: str,
        parent_id: str | None = None,
        description: str | None = None,
        sort_order: int = 0,
    ) -> dict:
        """创建新的文件夹（签名对齐本体，写操作由宿主处理）。

        Raises:
            RuntimeError: 宿主未暴露人格写 RPC。
        """
        raise RuntimeError(
            "SDK 不支持创建人格文件夹：人格数据由 Go 宿主维护，请使用宿主管理界面。"
        )

    async def update_folder(
        self,
        folder_id: str,
        name: str | None = None,
        parent_id=None,
        description=None,
        sort_order: int | None = None,
    ) -> None:
        """更新文件夹信息（签名对齐本体，写操作由宿主处理）。

        Raises:
            RuntimeError: 宿主未暴露人格写 RPC。
        """
        raise RuntimeError(
            "SDK 不支持更新人格文件夹：人格数据由 Go 宿主维护，请使用宿主管理界面。"
        )

    async def delete_folder(self, folder_id: str) -> None:
        """删除文件夹（签名对齐本体，写操作由宿主处理）。

        Raises:
            RuntimeError: 宿主未暴露人格写 RPC。
        """
        raise RuntimeError(
            "SDK 不支持删除人格文件夹：人格数据由 Go 宿主维护，请使用宿主管理界面。"
        )

    async def batch_update_sort_order(self, items: list[dict]) -> None:
        """批量更新排序顺序（签名对齐本体，写操作由宿主处理）。

        Raises:
            RuntimeError: 宿主未暴露人格写 RPC。
        """
        raise RuntimeError(
            "SDK 不支持批量更新排序：人格数据由 Go 宿主维护，请使用宿主管理界面。"
        )

    def get_v3_persona_data(self) -> tuple[list[dict], list[dict], dict]:
        """获取 AstrBot <4.0.0 版本的 persona 数据（对齐本体返回形状）。

        Returns:
            - list[dict]: persona 配置字典列表（prompt/name/begin_dialogs/...）。
            - list[dict]: v3 人格列表。
            - dict: 默认选中的人格。

        SDK 降级：Personality 为 TypedDict（dict 形态），情景预设对话不做
        _begin_dialogs_processed 转换（宿主数据无该维度时保持原样）。
        """
        v3_persona_config: list[dict] = []
        for persona in self.personas:
            v3_persona_config.append(
                {
                    "prompt": persona.system_prompt,
                    "name": persona.persona_id,
                    "begin_dialogs": persona.begin_dialogs or [],
                    "mood_imitation_dialogs": [],
                    "tools": persona.get("tools"),
                    "skills": persona.get("skills"),
                    "custom_error_message": persona.get("custom_error_message"),
                }
            )

        personas_v3: list[dict] = []
        selected_default: dict | None = None
        for persona_cfg in v3_persona_config:
            personas_v3.append(persona_cfg)
            if persona_cfg["name"] == self.default_persona:
                selected_default = persona_cfg

        if not selected_default and personas_v3:
            selected_default = personas_v3[0]
        if not selected_default:
            selected_default = {"name": "default", "prompt": "", "begin_dialogs": []}
            personas_v3.append(selected_default)

        return v3_persona_config, personas_v3, selected_default
