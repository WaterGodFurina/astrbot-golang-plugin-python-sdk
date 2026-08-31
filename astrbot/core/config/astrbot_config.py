"""AstrBot 配置（Go 宿主兼容运行时）。"""
from __future__ import annotations

import asyncio
import enum
import json
import logging
import os
import re
import tempfile
import threading
from typing import Any

logger = logging.getLogger("astrbot")

# 配置文件读-改-写互斥锁（put_config/update_config 并发写同一文件时保护）
_config_io_lock = threading.Lock()

# 宿主桥回退绑定（Context.get_config 创建实例时优先绑定实例属性，
# 插件自行构造 AstrBotConfig 时经 bind_host 回退到模块级引用）。
_HOST_BRIDGE: Any = None
_PLUGIN_NAME: str = ""


def get_astrbot_data_path() -> str:
    """返回 AstrBot 数据目录（由宿主注入 ASTRBOT_DATA_PATH，缺省为 cwd）。"""
    return os.environ.get("ASTRBOT_DATA_PATH", os.getcwd())


# 主配置文件路径（对齐本体 astrbot.core.config.astrbot_config.ASTRBOT_CONFIG_PATH）
ASTRBOT_CONFIG_PATH = os.path.join(get_astrbot_data_path(), "cmd_config.json")
DASHBOARD_INITIAL_PASSWORD_ENV = "ASTRBOT_DASHBOARD_INITIAL_PASSWORD"
DASHBOARD_RESET_PASSWORD_ENV = "ASTRBOT_RESET_DASHBOARD_PASSWORD"


class RateLimitStrategy(enum.Enum):
    """速率限制策略（对齐本体 astrbot.core.config.astrbot_config.RateLimitStrategy）。"""

    STALL = "stall"
    DISCARD = "discard"


def bind_host(bridge, plugin_name: str = "") -> None:
    """绑定宿主桥（模块级回退，供非 Context 路径构造的配置对象 save 使用）。"""
    global _HOST_BRIDGE, _PLUGIN_NAME
    _HOST_BRIDGE = bridge
    _PLUGIN_NAME = plugin_name or ""


def _atomic_write_json(path: str, data: dict) -> None:
    """mkstemp 唯一临时文件 + os.replace 原子写配置（避免并发写交错损坏 JSON）。"""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


class AstrBotConfig(dict):
    """插件配置对象（dict 子类 + 属性访问）。

    对齐 Python 本体 AstrBotConfig 的接口：属性访问、save_config /
    save_config_async（把当前配置写回宿主）。宿主桥引用以实例私有属性
    `_bridge` / `_plugin_name` 注入（Context.get_config 创建时绑定），
    无桥时回退到本地 JSON 文件写入。

    构造签名对齐本体 ``(config_path, default_config, schema)``：
    位置参数按类型识别（str → config_path，dict → default_config），
    关键字参数 config_path / default_config / schema 与本体同名同义。
    schema 属性对齐本体存储插件配置的 JSON Schema（构造时从宿主
    配置读入），插件可在运行时更新 options/labels（如 update_manager
    的插件列表下拉），宿主配置对话框实时拉取。
    """

    def __init__(
        self,
        *args,
        config_path: str | None = None,
        default_config: dict | None = None,
        schema: dict | None = None,
        **kwargs,
    ) -> None:
        # 本体签名 (config_path, default_config, schema) 的位置参数兼容：
        # str → config_path，dict → default_config，第三个 dict → schema。
        pos_config_path: str | None = None
        pos_default_config: dict | None = None
        pos_schema: dict | None = None
        rest = list(args)
        if rest and isinstance(rest[0], str):
            pos_config_path = rest.pop(0)
        if rest and isinstance(rest[0], dict):
            pos_default_config = rest.pop(0)
        if rest and isinstance(rest[0], dict):
            pos_schema = rest.pop(0)
        # 剩余位置参数宽松忽略（本体无此形态，不抛错以免插件炸）

        eff_config_path = config_path if config_path is not None else pos_config_path
        eff_default_config = (
            default_config if default_config is not None else pos_default_config
        )
        eff_schema = schema if schema is not None else pos_schema

        super().__init__(**kwargs)

        # 关键的实例属性存入 __dict__（object.__setattr__），避免被当作
        # 配置项写回宿主（对齐本体同款做法）。
        object.__setattr__(
            self,
            "config_path",
            eff_config_path if eff_config_path is not None else ASTRBOT_CONFIG_PATH,
        )
        object.__setattr__(self, "schema", eff_schema)
        object.__setattr__(self, "default_config", eff_default_config)
        object.__setattr__(self, "_save_state_lock", threading.Lock())

        if eff_schema is not None:
            # schema 解析出的默认值先入底（对齐本体"schema 生成默认配置"），
            # 随后 default_config / 位置 dict 携带的真实配置值覆盖默认值
            # （对齐本体"文件值覆盖 schema 默认"；Context.get_config 场景
            # 传入的宿主配置 data 即真实值，不得丢失）。
            schema_defaults = self._config_schema_to_default_config(eff_schema)
            self.update(schema_defaults)
        if eff_default_config is not None:
            self.update(eff_default_config)

        # 显式传入 config_path 且文件存在时读入（对齐本体构造时读文件行为；
        # 未显式传路径时不读 cmd_config.json——宿主运行时配置由宿主下发）。
        if config_path is not None or pos_config_path is not None:
            if os.path.exists(self.config_path):
                with open(self.config_path, encoding="utf-8-sig") as f:
                    conf_str = f.read()
                if conf_str.startswith("\ufeff"):
                    conf_str = conf_str[1:]
                conf = json.loads(conf_str)
                if isinstance(conf, dict):
                    self.update(conf)

    @staticmethod
    def _config_schema_to_default_config(schema: dict) -> dict:
        """将 Schema 转换成默认配置（对齐 Python 本体 astrbot_config）。"""
        from astrbot.core.config.default import DEFAULT_VALUE_MAP

        conf: dict = {}

        def _parse(schema: dict, conf: dict) -> None:
            for k, v in schema.items():
                if not isinstance(v, dict):
                    continue
                vtype = v.get("type", "string")
                if vtype not in DEFAULT_VALUE_MAP:
                    raise TypeError(
                        f"不受支持的配置类型 {vtype}。支持的类型有：{list(DEFAULT_VALUE_MAP.keys())}",
                    )
                if "default" in v:
                    default = v["default"]
                else:
                    default = DEFAULT_VALUE_MAP[vtype]
                if vtype == "object":
                    conf[k] = {}
                    _parse(v.get("items") or {}, conf[k])
                else:
                    conf[k] = default

        _parse(schema, conf)
        return conf

    def set_schema(self, schema: dict | None) -> None:
        """更新运行时 schema（插件 __init__ 后宿主注入/刷新时调用）。"""
        object.__setattr__(self, "schema", schema)

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
        # 对齐本体：删除配置项后立即 save_config；缺项抛 AttributeError。
        try:
            del self[key]
        except KeyError:
            raise AttributeError(f"没有找到 Key: '{key}'")
        self.save_config()

    def get(self, key: str, default=None):
        return super().get(key, default)

    def check_exist(self) -> bool:
        """配置文件是否存在（对齐本体 AstrBotConfig.check_exist）。"""
        if not self.config_path:  # 加判空
            return False
        return os.path.exists(self.config_path)

    def check_config_integrity(self, refer_conf: dict, conf: dict, path=""):
        """检查配置完整性（对齐本体同名方法）。

        按 refer_conf 的顺序重建 conf：缺失项补默认值、None 项用默认值、
        子 dict 递归检查、多余项剔除、顺序不一致重排；有任何变更返回 True。
        """
        has_new = False

        # 创建一个新的有序字典以保持参考配置的顺序
        new_conf = {}

        # 先按照参考配置的顺序添加配置项
        for key, value in refer_conf.items():
            if key not in conf:
                # 配置项不存在，插入默认值
                path_ = path + "." + key if path else key
                logger.info("Config key missing; added default.")
                new_conf[key] = value
                has_new = True
            elif conf[key] is None:
                # 配置项为 None，使用默认值
                new_conf[key] = value
                has_new = True
            elif isinstance(value, dict):
                # 递归检查子配置项
                if not isinstance(conf[key], dict):
                    # 类型不匹配，使用默认值
                    new_conf[key] = value
                    has_new = True
                else:
                    # 递归检查并同步顺序
                    child_has_new = self.check_config_integrity(
                        value,
                        conf[key],
                        path + "." + key if path else key,
                    )
                    new_conf[key] = conf[key]
                    has_new |= child_has_new
            else:
                # 直接使用现有配置
                new_conf[key] = conf[key]

        # 检查是否存在参考配置中没有的配置项
        for key in list(conf.keys()):
            if key not in refer_conf:
                path_ = path + "." + key if path else key
                logger.info("Config key removed: %s", path_)
                has_new = True

        # 顺序不一致也算作变更
        if list(conf.keys()) != list(new_conf.keys()):
            if path:
                logger.info("Config key order fixed: %s", path)
            else:
                logger.info("Config key order fixed")
            has_new = True

        # 更新原始配置
        conf.clear()
        conf.update(new_conf)

        return has_new

    @staticmethod
    def _consume_reset_dashboard_password_flag() -> bool:
        """兼容占位（本体用于 dashboard 密码重置，宿主运行时无此逻辑）。"""
        raw_value = os.environ.pop(DASHBOARD_RESET_PASSWORD_ENV, "")
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _safe_namespace(namespace: str) -> str:
        """校验 namespace 只含安全字符，防止路径穿越（../../somewhere/evil
        可读任意 .json 或改写宿主 config/ 下的文件）。"""
        if not namespace or not re.fullmatch(r"[A-Za-z0-9_\-]+", namespace):
            raise ValueError(f"非法的配置 namespace: {namespace!r}")
        return namespace

    @staticmethod
    def load_config(namespace: str) -> dict | bool:
        """从配置文件加载配置（兼容旧 API）。"""
        namespace = AstrBotConfig._safe_namespace(namespace)
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
        namespace = AstrBotConfig._safe_namespace(namespace)
        config_dir = os.path.join(get_astrbot_data_path(), "config")
        os.makedirs(config_dir, exist_ok=True)
        path = os.path.join(config_dir, f"{namespace}.json")
        with _config_io_lock:
            if not os.path.exists(path):
                _atomic_write_json(path, {})
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
                _atomic_write_json(path, d)

    @staticmethod
    def update_config(namespace: str, key: str, value) -> None:
        """更新配置项（兼容旧 API）。"""
        namespace = AstrBotConfig._safe_namespace(namespace)
        path = os.path.join(get_astrbot_data_path(), "config", f"{namespace}.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"配置文件 {namespace}.json 不存在。")
        with _config_io_lock:
            with open(path, encoding="utf-8-sig") as f:
                d = json.load(f)
            assert isinstance(d, dict)
            if key not in d:
                raise KeyError(f"配置项 {key} 不存在。")
            d[key]["value"] = value
            _atomic_write_json(path, d)

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
