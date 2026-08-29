"""技能管理（Go 宿主兼容运行时，对齐本体 skills/skill_manager）。

- `SkillInfo`：技能元数据（字段对齐本体）；
- `build_skills_prompt`：纯函数，把技能列表组装成系统提示词片段；
- `SkillManager`：读取/启停/删除技能的薄壳——技能数据由宿主 Go 侧
  `internal/skills` 原生管理，经 HostService 新增 skills RPC（ListSkills /
  SetSkillActive / DeleteSkill）转发；bridge 方法未就绪时优雅降级返回空/
  False，不抛异常。

命名注意：SkillInfo 等符号不与宿主 Go 侧名称发生 Python 命名冲突。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("astrbot")


@dataclass
class SkillInfo:
    """技能元数据（对齐本体 skills.skill_manager.SkillInfo）。"""

    name: str
    description: str
    path: str
    active: bool
    source_type: str = "local_only"
    source_label: str = "local"
    local_exists: bool = True
    sandbox_exists: bool = False
    plugin_name: str = ""
    readonly: bool = False
    preset: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "SkillInfo":
        """从宿主返回的 dict 还原（缺省字段取默认值）。"""
        if not isinstance(data, dict):
            raise ValueError(f"无法将 {data!r} 解析为 SkillInfo")
        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description", "") or ""),
            path=str(data.get("path", "") or ""),
            active=bool(data.get("active", False)),
            source_type=str(data.get("source_type", "local_only") or "local_only"),
            source_label=str(data.get("source_label", "local") or "local"),
            local_exists=bool(data.get("local_exists", True)),
            sandbox_exists=bool(data.get("sandbox_exists", False)),
            plugin_name=str(data.get("plugin_name", "") or ""),
            readonly=bool(data.get("readonly", False)),
            preset=bool(data.get("preset", False)),
        )

    def to_dict(self) -> dict:
        """序列化为 dict（与宿主 JSON 字段对齐）。"""
        return {
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "active": self.active,
            "source_type": self.source_type,
            "source_label": self.source_label,
            "local_exists": self.local_exists,
            "sandbox_exists": self.sandbox_exists,
            "plugin_name": self.plugin_name,
            "readonly": self.readonly,
            "preset": self.preset,
        }


def _sanitize_skill_display_name(name: str) -> str:
    """清理技能展示名（对齐本体 _sanitize_skill_display_name）。"""
    name = str(name or "").strip()
    if not name:
        return "unknown skill"
    return name


def build_skills_prompt(skills: list[SkillInfo]) -> str:
    """生成系统提示词中的技能清单片段（对齐本体 build_skills_prompt）。

    只展示技能名与介绍；完整 SKILL.md 由宿主 skills 子系统在 LLM 需要时
    读取（progressive disclosure 语义）。
    """
    if not skills:
        return ""
    lines: list[str] = []
    example_path = ""
    for skill in skills:
        display = _sanitize_skill_display_name(skill.name)
        description = str(skill.description or "").strip()
        if description:
            lines.append(f"## {display}\n{description}")
        else:
            lines.append(f"## {display}")
        if not example_path:
            example_path = skill.path or ""
        lines.append("")
    joined = "\n".join(lines).strip()
    header = ""
    if joined:
        header = "The user has enabled the following skills:\n\n"
    if example_path and isinstance(example_path, str):
        header += (
            "\nExample skill path (read it to see the full SKILL.md format): "
            f"{example_path}\n"
        )
    return f"{header}\n{joined}"


def _host_bridge():
    """获取宿主桥（薄壳转发入口）。"""
    try:
        from astrbot.core.star.context import get_host_bridge

        return get_host_bridge()
    except Exception:
        return None


class SkillManager:
    """技能管理器（SDK 薄壳：技能数据由宿主原生管理，经 skills RPC 转发）。

    构造参数兼容本体（skills_root / plugins_root 均被忽略——宿主统一维护
    技能目录）。
    """

    def __init__(
        self,
        skills_root: str | None = None,
        plugins_root: str | None = None,
    ) -> None:
        self.skills_root = skills_root or ""
        self.plugins_root = plugins_root or ""

    # ── 宿主 skills RPC 转发（慢路径，bridge 未就绪时降级）──────────────
    def _invoke(self, method: str, *args: Any, **kwargs: Any):
        """调用宿主 bridge 的 skills 方法；宿主未提供该 RPC 时优雅降级。"""
        bridge = _host_bridge()
        if bridge is None:
            return None
        fn = getattr(bridge, method, None)
        if fn is None:
            logger.debug("宿主 bridge 未提供 %s，技能操作降级为空", method)
            return None
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.warning("宿主技能操作 %s 失败（降级）", method)
            return None

    # ── 公开接口（对齐本体）──────────────────────────────────────────
    def list_skills(
        self,
        active_only: bool = False,
        runtime: str | None = None,
        hidden: bool = False,
    ) -> list[SkillInfo]:
        """列出全部技能（active_only 时只返回启用项）。

        转发宿主 bridge.list_skills(active_only, runtime)；bridge 未接入
        宿主时恒返回空列表（不抛异常）。
        """
        bridge = _host_bridge()
        if bridge is None:
            return []
        fn = getattr(bridge, "list_skills", None)
        if fn is None:
            logger.debug("宿主 bridge 未提供 list_skills，技能列表为空")
            return []
        try:
            raw = fn(active_only or False, runtime or "")
        except Exception:
            logger.warning("宿主 list_skills 失败（降级为空列表）")
            return []
        skills: list[SkillInfo] = []
        for item in raw if isinstance(raw, (list, tuple)) else []:
            if isinstance(item, dict):
                try:
                    skills.append(SkillInfo.from_dict(item))
                except (ValueError, TypeError):
                    logger.debug("跳过无法解析的技能条目: %r", item)
            elif isinstance(item, SkillInfo):
                skills.append(item)
        return skills

    def list_skills_info(self) -> list[dict]:
        """列出技能为 dict 列表（对齐宿主 ListSkillsInfo 返回形态）。"""
        return [s.to_dict() for s in self.list_skills()]

    def set_skill_active(self, name: str, active: bool) -> None:
        """启用/禁用指定技能（薄壳转发宿主 bridge.set_skill_active）。"""
        self._invoke("set_skill_active", name, bool(active))

    def delete_skill(self, name: str) -> None:
        """删除指定技能（薄壳转发宿主 bridge.delete_skill）。"""
        self._invoke("delete_skill", name)

    def is_sandbox_only_skill(self, name: str) -> bool:
        """是否为沙盒专属技能（SDK 薄壳：返回 False）。"""
        return False

    def is_plugin_skill(self, name: str) -> bool:
        """是否为插件内置技能（SDK 薄壳：返回 False）。"""
        return False

    def set_sandbox_skills_cache(self, skills: list[dict]) -> None:
        """设置沙盒技能缓存（SDK 薄壳：no-op）。"""

    def get_sandbox_skills_cache_status(self) -> dict[str, object]:
        """获取沙盒技能缓存状态（SDK 薄壳：返回空）。"""
        return {}


__all__ = ["SkillInfo", "SkillManager", "build_skills_prompt"]