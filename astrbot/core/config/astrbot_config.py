"""AstrBot 配置（Go 宿主兼容运行时）。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger("astrbot")

# 宿主桥回退绑定（Context.get_config 创建实例时优先绑定实例属性，
# 插件自行构造 AstrBotConfig 时经 bind_host 回退到模块级引用）。
_HOST_BRIDGE: Any = None
_PLUGIN_NAME: str = ""


def bind_host(bridge, plugin_name: str = "") -> None:
    """绑定宿主桥（模块级回退，供非 Context 路径构造的配置对象 save 使用）。"""
    global _HOST_BRIDGE, _PLUGIN_NAME
    _HOST_BRIDGE = bridge
    _PLUGIN_NAME = plugin_name or ""


def get_astrbot_data_path() -> str:
    """返回 AstrBot 数据目录（由宿主注入 ASTRBOT_DATA_PATH，缺省为 cwd）。"""
    return os.environ.get("ASTRBOT_DATA_PATH", os.getcwd())


class AstrBotConfig(dict):
    """插件配置对象（dict 子类 + 属性访问）。

    对齐 Python 本体 AstrBotConfig 的接口：属性访问、save_config /
    save_config_async（把当前配置写回宿主）。宿主桥引用以实例私有属性
    `_bridge` / `_plugin_name` 注入（Context.get_config 创建时绑定），
    无桥时回退到本地 JSON 文件写入。
    """

    def __getattr__(self, item):
        # 对齐 Python 本体：缺项返回 None 而非抛 AttributeError
        # （插件惯用 `if self.config.some_switch:` 写法依赖此行为）。
        try:
            return self[item]
        except KeyError:
            return None

    def __setattr__(self, key, value) -> None:
        # 下划线前缀的私有属性（_bridge/_plugin_name 等）存入实例 __dict__，
        # 避免被当作配置项写进宿主配置。
        if key.startswith("_"):
            object.__setattr__(self, key, value)
            return
        self[key] = value

    def __delattr__(self, key) -> None:
        if key in self:
            del self[key]

    def get(self, key: str, default=None):
        return super().get(key, default)

    @staticmethod
    def load_config(namespace: str) -> dict | bool:
        """从配置文件加载配置（兼容旧 API）。"""
        path = os.path.join(
            get_astrbot_data_path(), "config", f"{namespace}.json"
        )
        if not os.path.exists(path):
            return False
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
            ret = {}
            for k in data:
                if isinstance(data[k], dict) and "value" in data[k]:
                    ret[k] = data[k]["value"]
                else:
                    ret[k] = data[k]
            return ret

    @staticmethod
    def put_config(namespace: str, name: str, key: str, value, description: str) -> None:
        """写入配置项（兼容旧 API）。"""
        if namespace == "":
            raise ValueError("namespace 不能为空。")
        config_dir = os.path.join(get_astrbot_data_path(), "config")
        os.makedirs(config_dir, exist_ok=True)
        path = os.path.join(config_dir, f"{namespace}.json")
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write("{}")
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
        assert isinstance(d, dict)
        if key not in d:
            d[key] = {
                "config_type": "item",
                "name": name,
                "description": description,
                "path": key,
                "value": value,
                "val_type": type(value).__name__,
            }
            with open(path, "w", encoding="utf-8-sig") as f:
                json.dump(d, f, indent=2, ensure_ascii=False)
                f.flush()

    @staticmethod
    def update_config(namespace: str, key: str, value) -> None:
        """更新配置项（兼容旧 API）。"""
        path = os.path.join(get_astrbot_data_path(), "config", f"{namespace}.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"配置文件 {namespace}.json 不存在。")
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
        assert isinstance(d, dict)
        if key not in d:
            raise KeyError(f"配置项 {key} 不存在。")
        d[key]["value"] = value
        with open(path, "w", encoding="utf-8-sig") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
            f.flush()

    # ── 写回宿主（对齐本体 save_config / save_config_async）──────────────
    def _bridge_and_plugin(self) -> tuple[Any, str]:
        """解析宿主桥引用与插件名（实例注入优先，模块级回退）。

        兼容两种注入形态：宿主桥实例，或返回宿主桥的可调用对象
        （如 Context._bridge 绑定方法）。
        """
        bridge = getattr(self, "_bridge", None) or _HOST_BRIDGE
        if bridge is not None and not hasattr(bridge, "set_config") and not hasattr(
            bridge, "set_config_async"
        ):
            if callable(bridge):
                try:
                    bridge = bridge()
                except Exception:
                    bridge = None
        plugin_name = str(getattr(self, "_plugin_name", None) or _PLUGIN_NAME or "")
        return bridge, plugin_name

    def save_config(
        self, replace_config: dict | None = None, *, indent: int = 2
    ) -> None:
        """把当前配置写回宿主（同步入口）。

        Args:
            replace_config: 保存前合并进配置的项。
            indent: 本地 JSON 兜底落盘时的缩进。

        Notes:
            插件在事件循环中调用本方法时，经宿主桥 `set_config_async`
            以任务方式异步落盘（不阻塞插件循环）；无运行中事件循环时
            走同步 `set_config`。宿主桥不可用时回退写本地
            `data/config/<plugin>.json`，保证配置不丢失。
        """
        if replace_config:
            self.update(replace_config)
        bridge, plugin_name = self._bridge_and_plugin()
        if bridge is None:
            self._save_local_fallback(plugin_name, indent)
            return
        snapshot = dict(self)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and hasattr(bridge, "set_config_async"):
            task = loop.create_task(bridge.set_config_async(plugin_name, snapshot))

            def _log_err(t) -> None:
                if t.cancelled():
                    return
                exc = t.exception()
                if exc:
                    logger.warning(f"save_config 写回宿主失败: {exc}")

            task.add_done_callback(_log_err)
        else:
            try:
                bridge.set_config(plugin_name, snapshot)
            except Exception as e:
                logger.warning(f"save_config 写回宿主失败: {e}")

    async def save_config_async(
        self, replace_config: dict | None = None, *, indent: int = 2
    ) -> bool:
        """把当前配置写回宿主（异步入口，对齐本体 save_config_async）。

        返回是否成功写入宿主；宿主桥不可用时回退本地文件并返回 True。
        """
        if replace_config:
            self.update(replace_config)
        bridge, plugin_name = self._bridge_and_plugin()
        snapshot = dict(self)
        if bridge is None:
            self._save_local_fallback(plugin_name, indent)
            return True
        try:
            if hasattr(bridge, "set_config_async"):
                return bool(await bridge.set_config_async(plugin_name, snapshot))
            return bool(bridge.set_config(plugin_name, snapshot))
        except Exception as e:
            logger.warning(f"save_config_async 写回宿主失败: {e}")
            return False

    def _save_local_fallback(self, plugin_name: str, indent: int) -> None:
        """宿主桥不可用时的兜底：写本地 config/<namespace>.json（load_config 同格式）。"""
        if not plugin_name:
            logger.warning("save_config：宿主桥未绑定且无插件名，配置未落盘")
            return
        config_dir = os.path.join(get_astrbot_data_path(), "config")
        os.makedirs(config_dir, exist_ok=True)
        path = os.path.join(config_dir, f"{plugin_name}.json")
        with open(path, "w", encoding="utf-8-sig") as f:
            json.dump(dict(self), f, indent=indent, ensure_ascii=False)
            f.flush()
        logger.info(f"save_config：宿主桥未绑定，已回退写入 {path}")
