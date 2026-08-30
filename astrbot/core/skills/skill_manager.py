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
import os
import re
import shlex
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("astrbot")

_SKILL_NAME_RE = re.compile(r"^[\w.-]+$")
# 用于 prompt 路径净化的正则（对齐本体，防止提示词注入）
_SAFE_PATH_RE = re.compile(r"[^\w./ ,()'\-]", re.UNICODE)
_WINDOWS_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:(?:/|\\)")
_WINDOWS_UNC_PATH_RE = re.compile(r"^(//|\\\\)[^/\\]+[/\\][^/\\]+")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1F\x7F]")


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
    """清理技能展示名（对齐本体 _sanitize_skill_display_name）。

    合法技能名仅允许 [\\w.-]+；不合法返回 ``<invalid_skill_name>``。
    """
    name = str(name or "").strip()
    if _SKILL_NAME_RE.fullmatch(name):
        return name
    return "<invalid_skill_name>"


def _is_windows_prompt_path(path: str) -> bool:
    return bool(_WINDOWS_DRIVE_PATH_RE.match(path) or _WINDOWS_UNC_PATH_RE.match(path))


def _sanitize_prompt_path_for_prompt(path: str) -> str:
    """净化 prompt 中展示的技能路径（对齐本体，防提示词注入）。"""
    path = str(path or "")
    if not path:
        return ""
    if _WINDOWS_DRIVE_PATH_RE.match(path) or _WINDOWS_UNC_PATH_RE.match(path):
        path = path.replace("\\", "/")
    drive_prefix = ""
    if _WINDOWS_DRIVE_PATH_RE.match(path):
        drive_prefix = path[:2]
        path = path[2:]
    path = path.replace("`", "")
    path = _CONTROL_CHARS_RE.sub("", path)
    return f"{drive_prefix}{_SAFE_PATH_RE.sub('', path)}"


def _sanitize_prompt_description(description: str) -> str:
    description = str(description or "").replace("`", "")
    description = _CONTROL_CHARS_RE.sub(" ", description)
    return " ".join(description.split())


def _build_skill_read_command_example(path: str) -> str:
    """构造技能读取命令示例（对齐本体，一个本来可由不可信路径注入的命令）。"""
    if path == "<skills_root>/<skill_name>/SKILL.md":
        return f"cat {path}"
    if _is_windows_prompt_path(path):
        command = "type"
        path_arg = f'"{os.path.normpath(path)}"'
    else:
        command = "cat"
        path_arg = shlex.quote(path)
    return f"{command} {path_arg}"


def build_skills_prompt(skills: list[SkillInfo]) -> str:
    """生成系统提示词中的技能清单片段（对齐本体 build_skills_prompt 输出）。

    只展示技能名与介绍（progressive disclosure：完整 SKILL.md 由 LLM 按需
    读取）。
    """
    skills_lines: list[str] = []
    example_path = ""
    for skill in skills:
        display_name = _sanitize_skill_display_name(skill.name)

        description = skill.description or "No description"
        if skill.source_type in {"sandbox_only", "workspace"}:
            description = _sanitize_prompt_description(description)
            if not description:
                description = "Read SKILL.md for details."

        if skill.source_type == "sandbox_only":
            rendered_path = _sanitize_prompt_path_for_prompt(skill.path)
            if not rendered_path:
                rendered_path = "/workspace/skills/<skill_name>/SKILL.md"
        else:
            rendered_path = _sanitize_prompt_path_for_prompt(skill.path)
            if not rendered_path:
                rendered_path = "<skills_root>/<skill_name>/SKILL.md"

        skills_lines.append(
            f"- **{display_name}**: {description}\n  File: `{rendered_path}`"
        )
        if not example_path:
            example_path = rendered_path

    skills_block = "\n".join(skills_lines)
    if example_path == "<skills_root>/<skill_name>/SKILL.md":
        example_path = "<skills_root>/<skill_name>/SKILL.md"
    else:
        example_path = _sanitize_prompt_path_for_prompt(example_path) or "<skills_root>/<skill_name>/SKILL.md"
    example_command = _build_skill_read_command_example(example_path)

    return (
        "## Skills\n\n"
        "You have specialized skills — reusable instruction bundles stored "
        "in `SKILL.md` files. Each skill has a **name** and a **description** "
        "that tells you what it does and when to use it.\n\n"
        "### Available skills\n\n"
        f"{skills_block}\n\n"
        "### Skill rules\n\n"
        "1. **Discovery** — The list above is the complete skill inventory "
        "for this session. Full instructions are in the referenced "
        "`SKILL.md` file.\n"
        "2. **When to trigger** — Use a skill if the user names it "
        "explicitly, or if the task clearly matches the skill's description. "
        "*Never silently skip a matching skill* — either use it or briefly "
        "explain why you chose not to.\n"
        "3. **Mandatory grounding** — Before executing any skill you MUST "
        "first read its `SKILL.md` by running a shell command compatible "
        "with the current runtime shell and using the **absolute path** "
        f"shown above (e.g. `{example_command}`). "
        "Never rely on memory or assumptions about a skill's content.\n"
        "4. **Progressive disclosure** — Load only what is directly "
        "referenced from `SKILL.md`:\n"
        "   - If `scripts/` exist, prefer running or patching them over "
        "rewriting code from scratch.\n"
        "   - If `assets/` or templates exist, reuse them.\n"
        "   - Do NOT bulk-load every file in the skill directory.\n"
        "5. **Coordination** — When multiple skills apply, pick the minimal "
        "set needed. Announce which skill(s) you are using and why "
        "(one short line). Prefer `astrbot_*` tools when running skill "
        "scripts.\n"
        "6. **Context hygiene** — Avoid deep reference chasing; open only "
        "files that are directly linked from `SKILL.md`.\n"
        "7. **Failure handling** — If a skill cannot be applied, state the "
        "issue clearly and continue with the best alternative.\n"
    )


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
        *,
        active_only: bool = False,
        runtime: str = "local",
        show_sandbox_path: bool = True,
    ) -> list[SkillInfo]:
        """列出全部技能（active_only 时只返回启用项）。

        转发宿主 bridge.list_skills(active_only, runtime)；bridge 未接入
        宿主时恒返回空列表（不抛异常）。SDK 薄壳不回传沙盒路径，因此
        show_sandbox_path 在此不产生路径差异（宿主统一维护）。
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

    def list_workspace_skills(self, workspace_root: str | None) -> list[SkillInfo]:
        """列出会话工作区下的请求级技能（对齐原版 list_workspace_skills）。

        扫描 ``<workspace_root>/skills`` 下的 SKILL.md，返回 SkillInfo 列表；
        workspace_root 为空或目录不存在时返回空列表。
        """
        if not workspace_root:
            return []
        from pathlib import Path

        skills_root = Path(str(workspace_root)) / "skills"
        if not skills_root.is_dir():
            return []
        out: list[SkillInfo] = []
        for entry in sorted(skills_root.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                skill_md = entry / "skill.md"
            if not skill_md.is_file():
                continue
            try:
                text = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            out.append(
                SkillInfo(
                    name=entry.name,
                    description=self._parse_frontmatter_description(text),
                    path=str(skill_md),
                    active=True,
                    source_type="workspace",
                    source_label="workspace",
                )
            )
        return out

    def install_skill_from_zip(
        self,
        zip_path: str,
        *,
        overwrite: bool = True,
        skill_name_hint: str | None = None,
    ) -> str:
        """从 zip 安装技能（对齐原版 install_skill_from_zip，返回安装的技能名）。

        SDK 薄壳：解析 zip 里的顶层 ``SKILL.md``（或含 SKILL.md 的顶层目录）
        解包到本地 skills 目录；宿主沙盒/市场安装能力由宿主原生负责，此方法
        仅满足插件 import 与基础本地安装语义。
        """
        import shutil
        import tempfile
        import zipfile
        from pathlib import Path

        del overwrite  # SDK 本地安装始终覆盖同名目录
        zip_path_obj = Path(str(zip_path))
        if not zip_path_obj.exists():
            raise FileNotFoundError(f"Zip file not found: {zip_path}")
        if not zipfile.is_zipfile(str(zip_path_obj)):
            raise ValueError("Uploaded file is not a valid zip archive.")

        with zipfile.ZipFile(str(zip_path_obj)) as zf:
            names = [n for n in zf.namelist() if n and not n.endswith("/")]
            root_skill_md = next(
                (n for n in names if Path(n).name.lower() == "skill.md"), None
            )
            if root_skill_md is None:
                raise ValueError("Zip archive has no SKILL.md at its root.")
            skill_name = skill_name_hint or Path(root_skill_md).parent.name or "skill"

            with tempfile.TemporaryDirectory() as tmp:
                tmp_root = Path(tmp)
                zf.extractall(tmp_root)
                src = tmp_root / root_skill_md
                dst = Path(self.skills_root) / skill_name
                if dst.exists():
                    shutil.rmtree(dst, ignore_errors=True)
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst / "SKILL.md")
                # 附带同目录其他文件（脚本/资源）
                for member in names:
                    p = Path(member)
                    if p.parent != Path(root_skill_md).parent or p.name.lower() == "skill.md":
                        continue
                    f_src = tmp_root / member
                    if f_src.is_file():
                        f_dst = dst / p.name
                        f_dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f_src, f_dst)
        return skill_name

    @staticmethod
    def _parse_frontmatter_description(text: str) -> str:
        """解析 SKILL.md frontmatter 的 description 字段（对齐原版解析语义）。"""
        import re

        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if not m:
            return ""
        fm = m.group(1)
        dm = re.search(r"(?:^|\n)description\s*:\s*(.+?)(?:\n|$)", fm)
        if not dm:
            return ""
        return dm.group(1).strip().strip('"').strip("'")


__all__ = ["SkillInfo", "SkillManager", "build_skills_prompt"]