"""AstrBot 配置管理器（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.astrbot_config_mgr.AstrBotConfigManager`
的公开方法面：get_conf / default_conf / get_conf_info / get_conf_list /
create_conf / delete_conf / update_conf_info / initialize / g 与
ConfInfo / DEFAULT_CONFIG_CONF_INFO 导出。

SDK 无 UmopConfigRouter（umo→配置档案绑定）基础设施，非 default 档案
的解析一律 fallback 到默认配置（与本体 get_conf 的兜底语义一致）；
构造签名兼容本体 ``(default_config, ucr, sp)`` 三个位置/关键字参数。

SDK 保留独有便捷方法 get_config / get_config_async / get_config_path。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, TypedDict

from astrbot.core.config.astrbot_config import (
    ASTRBOT_CONFIG_PATH,
    AstrBotConfig,
)
from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.utils.astrbot_path import (
    get_astrbot_config_path,
    get_astrbot_data_path,
)

logger = logging.getLogger("astrbot")


class ConfInfo(TypedDict):
    """Configuration information for a specific session or platform."""

    id: str  # UUID of the configuration or "default"
    name: str
    path: str  # File name to the configuration file


DEFAULT_CONFIG_CONF_INFO = ConfInfo(
    id="default",
    name="default",
    path=ASTRBOT_CONFIG_PATH,
)


class AstrBotConfigManager:
    """A class to manage the system configuration of AstrBot, aka ACM.

    Go 宿主运行时降级说明：无 abconf 配置档案的 webui 绑定路由（ucr）
    与持久化加载，档案映射维护在内存（可选经 SharedPreferences 持久化），
    非 default 档案解析一律返回默认配置。
    """

    def __init__(
        self,
        default_config: AstrBotConfig | None = None,
        ucr: Any = None,
        sp: Any = None,
    ) -> None:
        self.sp = sp
        self.ucr = ucr
        self.confs: dict[str, AstrBotConfig] = {}
        """uuid / "default" -> AstrBotConfig"""
        self.confs["default"] = (
            default_config if default_config is not None else AstrBotConfig()
        )
        self.abconf_data: dict | None = {}
        """配置档案元数据映射（conf_id -> {path, name}），SDK 内存维护。"""
        self._abconf_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Load configuration profile metadata and profile files.

        SDK 无 sp/ucr 基础设施：尽力从 SharedPreferences 恢复映射并
        加载对应本地配置文件，失败不抛异常（保持可初始化）。
        """
        self.abconf_data = await self._load_abconf_mapping()
        self._load_all_configs()

    async def _load_abconf_mapping(self) -> dict:
        """Load configuration profile metadata from persistent storage."""
        if self.sp is None:
            # SDK 无持久化层：直接返回内存映射（create/delete 生命周期
            # 在同实例内自洽）。
            return self.abconf_data if self.abconf_data is not None else {}
        try:
            abconf_data = await self.sp.global_get("abconf_mapping", {})
            return abconf_data if abconf_data is not None else {}
        except Exception as e:
            logger.warning(f"加载配置档案映射失败（忽略，使用内存映射）: {e}")
            return self.abconf_data if self.abconf_data is not None else {}

    async def _persist_abconf_mapping(self, abconf_data: dict) -> None:
        """Persist configuration profile metadata and refresh memory."""
        self.abconf_data = abconf_data
        if self.sp is None:
            return
        try:
            await self.sp.global_put("abconf_mapping", abconf_data)
        except Exception as e:
            logger.warning(f"持久化配置档案映射失败（忽略，仅保留内存映射）: {e}")

    def _get_abconf_data(self) -> dict:
        """Return configuration profile metadata loaded during initialization."""
        if self.abconf_data is None:
            raise RuntimeError(
                "AstrBotConfigManager must be initialized before use.",
            )
        return self.abconf_data

    def _load_all_configs(self) -> None:
        """Load all configurations from the shared preferences."""
        abconf_data = self._get_abconf_data()
        self.abconf_data = abconf_data
        for uuid_, meta in abconf_data.items():
            if not isinstance(meta, dict):
                continue
            filename = meta.get("path")
            if not filename:
                continue
            conf_path = os.path.join(get_astrbot_config_path(), filename)
            if os.path.exists(conf_path):
                conf = AstrBotConfig(config_path=conf_path)
                self.confs[uuid_] = conf
            else:
                logger.warning(
                    f"Config file {conf_path} for UUID {uuid_} does not exist, skipping.",
                )
                continue

    def _load_conf_mapping(self, umo: str | MessageSession) -> ConfInfo:
        """获取指定 umo 的配置文件 uuid, 如果不存在则返回默认配置(返回 "default")。

        SDK 无 umo→档案绑定路由（ucr），恒返回默认档案元数据。
        """
        # uuid -> { "path": str, "name": str }
        abconf_data = self._get_abconf_data()

        if isinstance(umo, MessageSession):
            umo = str(umo)
        else:
            try:
                umo = str(MessageSession.from_str(umo))  # validate
            except Exception:
                return DEFAULT_CONFIG_CONF_INFO

        if self.ucr is not None:
            try:
                conf_id = self.ucr.get_conf_id_for_umop(umo)
            except Exception:
                conf_id = None
            if conf_id:
                meta = abconf_data.get(conf_id)
                if meta and isinstance(meta, dict):
                    meta.pop("umop", None)
                    return ConfInfo(**meta, id=conf_id)

        return DEFAULT_CONFIG_CONF_INFO

    async def _save_conf_mapping(
        self,
        abconf_path: str,
        abconf_id: str,
        abconf_name: str | None = None,
    ) -> None:
        """Persist a new configuration profile mapping."""
        abconf_data = await self._load_abconf_mapping()
        random_word = abconf_name or uuid.uuid4().hex[:8]
        abconf_data[abconf_id] = {
            "path": abconf_path,
            "name": random_word,
        }
        await self._persist_abconf_mapping(abconf_data)

    def get_conf(self, umo: str | MessageSession | None = None) -> AstrBotConfig:
        """获取指定 umo 的配置文件。如果不存在，则 fallback 到默认配置文件。"""
        if not umo:
            return self.confs["default"]
        if isinstance(umo, MessageSession):
            umo = f"{umo.platform_id}:{umo.message_type}:{umo.session_id}"

        uuid_ = self._load_conf_mapping(umo)["id"]

        conf = self.confs.get(uuid_)
        if not conf:
            conf = self.confs["default"]  # default MUST exists

        return conf

    @property
    def default_conf(self) -> AstrBotConfig:
        """获取默认配置文件"""
        return self.confs["default"]

    def get_conf_info(self, umo: str | MessageSession) -> ConfInfo:
        """获取指定 umo 的配置文件元数据"""
        if isinstance(umo, MessageSession):
            umo = f"{umo.platform_id}:{umo.message_type}:{umo.session_id}"

        return self._load_conf_mapping(umo)

    def get_conf_list(self) -> list[ConfInfo]:
        """获取所有配置文件的元数据列表"""
        conf_list = []
        abconf_mapping = self._get_abconf_data()
        for uuid_, meta in abconf_mapping.items():
            if not isinstance(meta, dict):
                continue
            meta.pop("umop", None)
            conf_list.append(ConfInfo(**meta, id=uuid_))
        conf_list.append(DEFAULT_CONFIG_CONF_INFO)
        return conf_list

    async def create_conf(
        self,
        config: dict = DEFAULT_CONFIG,
        name: str | None = None,
    ) -> str:
        """Create and persist a configuration profile.

        Args:
            config: Initial profile configuration.
            name: Optional display name.

        Returns:
            The generated configuration profile ID.
        """
        async with self._abconf_lock:
            conf_uuid = str(uuid.uuid4())
            conf_file_name = f"abconf_{conf_uuid}.json"
            conf_path = os.path.join(get_astrbot_config_path(), conf_file_name)
            os.makedirs(os.path.dirname(conf_path), exist_ok=True)
            with open(conf_path, "w", encoding="utf-8-sig") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            conf = AstrBotConfig(config_path=conf_path, default_config=config)
            self.confs[conf_uuid] = conf
            await self._save_conf_mapping(
                conf_file_name,
                conf_uuid,
                abconf_name=name,
            )
            return conf_uuid

    async def delete_conf(self, conf_id: str) -> bool:
        """Delete a configuration profile.

        Args:
            conf_id: Configuration profile ID.

        Returns:
            Whether the profile was deleted.

        Raises:
            ValueError: If the default profile is requested.
        """
        if conf_id == "default":
            raise ValueError("不能删除默认配置文件")

        async with self._abconf_lock:
            # 从映射中移除
            abconf_data = await self._load_abconf_mapping()
            if conf_id not in abconf_data:
                logger.warning(f"配置文件 {conf_id} 不存在于映射中")
                return False

            # 获取配置文件路径
            conf_path = os.path.join(
                get_astrbot_config_path(),
                abconf_data[conf_id]["path"],
            )

            # 删除配置文件
            try:
                if os.path.exists(conf_path):
                    os.remove(conf_path)
                    logger.info(f"已删除配置文件: {conf_path}")
            except Exception as e:
                logger.error(f"删除配置文件 {conf_path} 失败: {e}")
                return False

            # 从内存中移除
            if conf_id in self.confs:
                del self.confs[conf_id]

            # 从映射中移除
            del abconf_data[conf_id]
            await self._persist_abconf_mapping(abconf_data)

            logger.info(f"成功删除配置文件 {conf_id}")
            return True

    async def update_conf_info(
        self,
        conf_id: str,
        name: str | None = None,
    ) -> bool:
        """Update configuration profile metadata.

        Args:
            conf_id: Configuration profile ID.
            name: Optional new display name.

        Returns:
            Whether the profile metadata was updated.

        Raises:
            ValueError: If the default profile is requested.
        """
        if conf_id == "default":
            raise ValueError("不能更新默认配置文件的信息")

        async with self._abconf_lock:
            abconf_data = await self._load_abconf_mapping()
            if conf_id not in abconf_data:
                logger.warning(f"配置文件 {conf_id} 不存在于映射中")
                return False

            # 更新名称
            if name is not None:
                abconf_data[conf_id]["name"] = name

            # 保存更新
            await self._persist_abconf_mapping(abconf_data)
            logger.info(f"成功更新配置文件 {conf_id} 的信息")
            return True

    # ── SDK 独有便捷方法（保持既有行为）────────────────────────────────
    def get_config_path(self) -> str:
        """返回主配置文件路径（data/config.json）。"""
        return os.path.join(get_astrbot_data_path(), "config.json")

    def get_config(self, umo: str | None = None) -> AstrBotConfig:
        """获取 AstrBot 配置（等价于 get_conf，返回缓存的默认配置实例）。"""
        return self.get_conf(umo)

    async def get_config_async(self, umo: str | None = None) -> AstrBotConfig:
        """异步获取 AstrBot 配置（等价于 get_conf 的异步形态）。"""
        return self.get_conf(umo)

    def g(
        self,
        umo: str | None = None,
        key: str | None = None,
        default=None,
    ):
        """获取配置项。umo 为 None 时使用默认配置"""
        if umo is None:
            return self.confs["default"].get(key, default)
        conf = self.get_conf(umo)
        return conf.get(key, default)
