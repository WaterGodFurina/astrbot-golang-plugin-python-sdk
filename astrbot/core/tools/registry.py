"""内置工具注册表（Go 宿主兼容运行时，对齐本体 tools/registry）。

对齐本体 `astrbot.core.tools.registry` 的对外入口：

- ``builtin_tool`` 装饰器：注册内置工具类；``config=`` 关键字把配置映射
  规则注册进 ``_BUILTIN_TOOL_CONFIG_RULES``（语义同本体）；
- ``BuiltinToolConfigCondition`` / ``BuiltinToolConfigRule``：内置工具的
  配置启用条件（equals/in/truthy/custom 操作符，点路径取值）；
- ``ensure_builtin_tools_loaded()``：惰性 import 全部内置工具模块
  （computer_tools / cron_tools / knowledge_base_tools / message_tools /
  web_search_tools），保证 ``get_builtin_tool_class`` 能查到类；
- ``get_builtin_tool_config_statuses(name, entries)`` /
  ``get_builtin_tool_config_tags(name, entries)``：按配置项评估工具在各
  配置项下是否可用（本体签名：两参，返回 list[dict]）。

宿主 Go agent 循环原生装配/执行这些内置工具（internal/pipeline/
cron_tools.go、kb_tools.go、message_tools.go、websearch.go、
computer_tools.go），SDK 注册表只维护"类型面"与配置规则，不做执行。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Any

logger = logging.getLogger("astrbot")

_BUILTIN_TOOL_MODULES = (
    "astrbot.core.tools.computer_tools",
    "astrbot.core.tools.cron_tools",
    "astrbot.core.tools.knowledge_base_tools",
    "astrbot.core.tools.message_tools",
    "astrbot.core.tools.web_search_tools",
)

_builtin_tool_classes_by_name: dict[str, type] = {}
"""名称 → 内置工具类（SDK 本地注册表，仅记录类，不执行）。"""

_builtin_tool_names_by_class: dict[type, str] = {}

_builtin_tools_loaded = False

_MISSING = object()


@dataclass(frozen=True)
class BuiltinToolConfigCondition:
    """单条内置工具配置条件（对齐本体同名 dataclass）。

    Attributes:
        key: 配置项点路径（如 ``provider_settings.web_search``）。
        operator: ``equals`` / ``in`` / ``truthy`` / ``custom``。
        expected: 期望值（``in`` 为元组；``custom`` 恒真时非 None）。
        message: 命中/未命中时附加的说明信息。
    """

    key: str
    operator: str
    expected: Any = None
    message: str | None = None

    def evaluate(self, config: dict[str, Any]) -> dict[str, Any]:
        """评估条件，返回 key/operator/expected/actual/matched/message 字典。"""
        actual = _get_config_value(config, self.key)

        if self.operator == "equals":
            matched = actual == self.expected
        elif self.operator == "in":
            expected_values = tuple(self.expected or ())
            matched = actual in expected_values
        elif self.operator == "truthy":
            matched = bool(actual)
        elif self.operator == "custom":
            matched = bool(self.expected)
        else:
            raise ValueError(
                f"Unsupported builtin tool config operator: {self.operator}"
            )

        return {
            "key": self.key,
            "operator": self.operator,
            "expected": _json_safe(self.expected),
            "actual": _json_safe(None if actual is _MISSING else actual),
            "matched": matched,
            "message": self.message,
        }


@dataclass(frozen=True)
class BuiltinToolConfigRule:
    """内置工具配置规则（条件集合或自定义评估器，对齐本体同名 dataclass）。"""

    conditions: tuple[BuiltinToolConfigCondition, ...] = ()
    evaluator: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None

    def evaluate(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        """评估规则：优先走 evaluator，否则逐条评估 conditions。"""
        if self.evaluator is not None:
            return self.evaluator(config)
        return [condition.evaluate(config) for condition in self.conditions]


def _get_config_value(config: dict[str, Any], key_path: str) -> Any:
    """按点路径取配置值，缺失返回 _MISSING 哨兵。"""
    current: Any = config
    for segment in key_path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


def _json_safe(value: Any) -> Any:
    """把 tuple 递归转 list，保证评估结果可 JSON 序列化。"""
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(val) for key, val in value.items()}
    return value


def _equals(key: str, expected: Any) -> BuiltinToolConfigCondition:
    return BuiltinToolConfigCondition(key=key, operator="equals", expected=expected)


def _in(key: str, expected: tuple[Any, ...]) -> BuiltinToolConfigCondition:
    return BuiltinToolConfigCondition(key=key, operator="in", expected=expected)


def _custom_condition(key: str, *, matched: bool, message: str) -> dict[str, Any]:
    return {
        "key": key,
        "operator": "custom",
        "expected": None,
        "actual": None,
        "matched": matched,
        "message": message,
    }


def _build_rule_from_config_map(
    config_map: dict[str, Any],
) -> BuiltinToolConfigRule:
    """把 ``{key: expected}`` 映射构建为规则（tuple → in，其余 → equals）。"""
    conditions: list[BuiltinToolConfigCondition] = []
    for key, expected in config_map.items():
        if isinstance(expected, tuple):
            conditions.append(_in(key, expected))
        else:
            conditions.append(_equals(key, expected))
    return BuiltinToolConfigRule(conditions=tuple(conditions))


def _evaluate_send_message_tool(config: dict[str, Any]) -> list[dict[str, Any]]:
    """send_message_to_user 的自定义评估器（对齐本体 registry 同名函数）。

    判定已启用平台中是否存在支持主动发消息的平台（wecom /
    weixin_official_account 不支持；wecom_ai_bot 需配置
    msg_push_webhook_url；其余平台默认支持）。
    """
    platform_configs = config.get("platform", [])
    if not isinstance(platform_configs, list):
        return [
            _custom_condition(
                "platform",
                matched=False,
                message="No enabled platform in this config supports proactive messaging.",
            )
        ]

    for platform_cfg in platform_configs:
        if not isinstance(platform_cfg, dict):
            continue
        if platform_cfg.get("enable", False) is False:
            continue

        platform_type = str(platform_cfg.get("type", "")).strip()
        platform_id = str(platform_cfg.get("id", "")).strip() or platform_type
        if not platform_type:
            continue

        if platform_type in {"wecom", "weixin_official_account"}:
            continue

        if platform_type == "wecom_ai_bot":
            webhook = str(platform_cfg.get("msg_push_webhook_url", "")).strip()
            if not webhook:
                continue
            return [
                _custom_condition(
                    "platform[].type",
                    matched=True,
                    message=(
                        f"Enabled platform `{platform_id}` uses `wecom_ai_bot`, which supports proactive messaging "
                        "when `platform[].msg_push_webhook_url` is configured."
                    ),
                ),
                BuiltinToolConfigCondition(
                    key="platform[].msg_push_webhook_url",
                    operator="truthy",
                ).evaluate({"platform[]": {"msg_push_webhook_url": webhook}}),
            ]

        return [
            _custom_condition(
                "platform[].type",
                matched=True,
                message=(
                    f"Enabled platform `{platform_id}` (`{platform_type}`) supports proactive messaging."
                ),
            )
        ]

    return [
        _custom_condition(
            "platform",
            matched=False,
            message="No enabled platform in this config supports proactive messaging.",
        )
    ]


_BUILTIN_TOOL_CONFIG_RULES: dict[str, BuiltinToolConfigRule] = {}


def _register_builtin_tool_config_rule(
    tool_names: tuple[str, ...],
    rule: BuiltinToolConfigRule,
) -> None:
    for tool_name in tool_names:
        _BUILTIN_TOOL_CONFIG_RULES[tool_name] = rule


_register_builtin_tool_config_rule(
    ("send_message_to_user",),
    BuiltinToolConfigRule(evaluator=_evaluate_send_message_tool),
)


def _resolve_builtin_tool_name(tool_cls: type) -> str:
    """解析内置工具名：优先类属性 name，退化取 dataclass name 字段默认值。"""
    name = getattr(tool_cls, "name", None)
    if isinstance(name, str) and name:
        return name
    fields = getattr(tool_cls, "__dataclass_fields__", {})
    field_def = fields.get("name")
    if field_def is not None and isinstance(getattr(field_def, "default", None), str):
        return field_def.default
    raise ValueError(
        f"Builtin tool class {tool_cls.__module__}.{tool_cls.__name__} does not define a valid name."
    )


def builtin_tool(tool_cls=None, *, config: dict | None = None):
    """内置工具注册装饰器（对齐本体：config 声明配置启用规则）。

    用法::

        @builtin_tool                      # 直接注册
        @builtin_tool(config={"kb_agentic_mode": True})  # 附带配置规则

    同名冲突（不同类）抛 ValueError；``config`` 非空时按
    ``_build_rule_from_config_map`` 注册进 ``_BUILTIN_TOOL_CONFIG_RULES``。
    """

    def _register(cls):
        tool_name = _resolve_builtin_tool_name(cls)
        existing = _builtin_tool_classes_by_name.get(tool_name)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"Builtin tool name conflict detected: {tool_name} is already registered by "
                f"{existing.__module__}.{existing.__name__}.",
            )
        _builtin_tool_classes_by_name[tool_name] = cls
        _builtin_tool_names_by_class[cls] = tool_name
        if config is not None:
            _BUILTIN_TOOL_CONFIG_RULES[tool_name] = _build_rule_from_config_map(config)
        return cls

    if tool_cls is None:
        return _register
    return _register(tool_cls)


def ensure_builtin_tools_loaded() -> None:
    """确保内置工具模块已加载（惰性 import，对齐本体语义）。"""
    global _builtin_tools_loaded
    if _builtin_tools_loaded:
        return

    for module_name in _BUILTIN_TOOL_MODULES:
        import_module(module_name)

    _builtin_tools_loaded = True


def get_builtin_tool_class(name: str) -> type | None:
    """按名称取内置工具类（未注册返回 None）。"""
    ensure_builtin_tools_loaded()
    return _builtin_tool_classes_by_name.get(name)


def get_builtin_tool_name(tool_cls: type) -> str | None:
    """按类取内置工具名（未注册返回 None）。"""
    ensure_builtin_tools_loaded()
    return _builtin_tool_names_by_class.get(tool_cls)


def iter_builtin_tool_classes() -> tuple[type, ...]:
    """迭代全部已注册内置工具类。"""
    ensure_builtin_tools_loaded()
    return tuple(_builtin_tool_classes_by_name.values())


def get_builtin_tool_config_rule(name: str) -> BuiltinToolConfigRule | None:
    """取内置工具配置规则（未注册返回 None）。"""
    ensure_builtin_tools_loaded()
    return _BUILTIN_TOOL_CONFIG_RULES.get(name)


def get_builtin_tool_config_statuses(
    tool_name: str,
    config_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """评估工具在各配置项下的可用状态（签名对齐本体）。

    Args:
        tool_name: 内置工具名。
        config_entries: 配置项列表，每项含 conf_id/conf_name/config。

    Returns:
        list[dict]：每项含 conf_id/conf_name/enabled/matched_conditions/
        failed_conditions；无规则时返回空列表。
    """
    rule = get_builtin_tool_config_rule(tool_name)
    if rule is None:
        return []

    statuses: list[dict[str, Any]] = []
    for entry in config_entries:
        config = entry.get("config")
        if not isinstance(config, dict):
            continue

        conditions = rule.evaluate(config)
        enabled = bool(conditions) and all(
            bool(condition.get("matched")) for condition in conditions
        )
        statuses.append(
            {
                "conf_id": entry.get("conf_id"),
                "conf_name": entry.get("conf_name"),
                "enabled": enabled,
                "matched_conditions": [
                    condition for condition in conditions if condition.get("matched")
                ],
                "failed_conditions": [
                    condition
                    for condition in conditions
                    if not condition.get("matched")
                ],
            }
        )
    return statuses


def get_builtin_tool_config_tags(
    tool_name: str,
    config_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """取工具可用的配置项标签（即 statuses 中 enabled 的子集，对齐本体）。"""
    return [
        status
        for status in get_builtin_tool_config_statuses(tool_name, config_entries)
        if status["enabled"]
    ]


__all__ = [
    "BuiltinToolConfigCondition",
    "BuiltinToolConfigRule",
    "builtin_tool",
    "ensure_builtin_tools_loaded",
    "get_builtin_tool_class",
    "get_builtin_tool_config_rule",
    "get_builtin_tool_config_statuses",
    "get_builtin_tool_config_tags",
    "get_builtin_tool_name",
    "iter_builtin_tool_classes",
]
