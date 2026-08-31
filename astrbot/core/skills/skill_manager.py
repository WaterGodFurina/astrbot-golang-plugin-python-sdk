"""技能管理（Go 宿主兼容运行时，对齐本体 skills/skill_manager）。

- `SkillInfo`：技能元数据（字段对齐本体）；
- `build_skills_prompt`：纯函数，把技能列表组装成系统提示词片段；
- `SkillManager`：读取/启停/删除技能的薄壳——技能数据由宿主 Go 侧
  `internal/skills` 原生管理，经 HostService 的 skills RPC（ListSkills /
  SetSkillActive / DeleteSkill）转发；bridge 方法未就绪时优雅降级返回空/
  False，不抛异常。
- 本体中 skills RPC 未覆盖的能力按本体移植/对齐：zip 安装、工作区技能
  发现、沙盒技能缓存（插件子进程与宿主共享同一 data 目录，缓存文件
  data/sandbox_skills_cache.json 与宿主 Go SkillManager 互通）、模块级
  常量与辅助函数，保证 ``from astrbot.core.skills.skill_manager import X``
  可用。

命名注意：SkillInfo 等符号不与宿主 Go 侧名称发生 Python 命名冲突。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

logger = logging.getLogger("astrbot")

# ── 模块级常量（对齐本体 skill_manager 顶部常量）─────────────────────
SKILLS_CONFIG_FILENAME = "skills.json"
SANDBOX_SKILLS_CACHE_FILENAME = "sandbox_skills_cache.json"
DEFAULT_SKILLS_CONFIG: dict[str, dict] = {"skills": {}}
SANDBOX_SKILLS_ROOT = "skills"
SANDBOX_WORKSPACE_ROOT = "/workspace"
WORKSPACE_SKILLS_ROOT = "skills"
WORKSPACE_SKILL_FRONTMATTER_MAX_CHARS = 64 * 1024
_SANDBOX_SKILLS_CACHE_VERSION = 1

_SKILL_NAME_RE = re.compile(r"^[\w.-]+$")


def _normalize_skill_name(name: str | None) -> str:
    """技能名规范化：去首尾空白并把空白折为下划线（对齐本体）。"""
    raw = str(name or "")
    return re.sub(r"\s+", "_", raw.strip())


def _default_sandbox_skill_path(name: str) -> str:
    """沙盒内技能的缺省 SKILL.md 路径（对齐本体）。"""
    return f"{SANDBOX_WORKSPACE_ROOT}/{SANDBOX_SKILLS_ROOT}/{name}/SKILL.md"


def _normalize_cached_sandbox_skill_path(name: str, path: str) -> str:
    """规范化沙盒缓存里的技能路径；非法（含 ``..``/非 SKILL.md/父目录名
    与技能名不符）时回退缺省路径（对齐本体）。"""
    normalized = str(path or "").strip().replace("\\", "/")
    if not normalized:
        return _default_sandbox_skill_path(name)

    pure_path = PurePosixPath(normalized)
    if ".." in pure_path.parts:
        return _default_sandbox_skill_path(name)

    if pure_path.name != "SKILL.md":
        return _default_sandbox_skill_path(name)

    if pure_path.parent.name != name:
        return _default_sandbox_skill_path(name)

    return str(pure_path)


def _is_ignored_zip_entry(name: str) -> bool:
    """zip 归档内的忽略条目（``__MACOSX`` 等，对齐本体）。"""
    parts = PurePosixPath(name).parts
    if not parts:
        return True
    return parts[0] == "__MACOSX"


def _normalize_skill_markdown_path(
    skill_dir: Path,
    *,
    rename_legacy: bool = True,
) -> Path | None:
    """返回技能目录的规范 ``SKILL.md`` 路径（对齐本体）。

    若目录里只有旧式 ``skill.md``，在 ``rename_legacy=True`` 时就地重命名
    为 ``SKILL.md``；两者都不存在时返回 ``None``。
    """
    canonical = skill_dir / "SKILL.md"
    entries: set[str] = set()
    if skill_dir.exists():
        entries = {entry.name for entry in skill_dir.iterdir()}
    if "SKILL.md" in entries:
        return canonical
    legacy = skill_dir / "skill.md"
    if "skill.md" not in entries:
        return None
    try:
        if not rename_legacy:
            return legacy
        tmp = skill_dir / f".{uuid.uuid4().hex}.tmp_skill_md"
        legacy.rename(tmp)
        tmp.rename(canonical)
    except OSError:
        return legacy
    return canonical


def _parse_frontmatter_description(text: str) -> str:
    """从 SKILL.md 的 YAML frontmatter 提取 ``description``（对齐本体）。

    标准 SKILL.md 格式（OpenAI Codex CLI / Anthropic Claude Skills）::

        ---
        name: my-skill
        description: What this skill does and when to use it.
        ---

    本体用 pyyaml 解析；SDK 依赖列表无 pyyaml，故优先尝试 pyyaml（语义
    完全一致），运行环境缺 pyyaml 时回退到单行正则解析。
    """
    if not text.startswith("---"):
        return ""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return ""

    frontmatter = "\n".join(lines[1:end_idx])
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        yaml = None  # type: ignore[assignment]

    if yaml is not None:
        try:
            payload = yaml.safe_load(frontmatter) or {}
        except Exception:
            return ""
        if not isinstance(payload, dict):
            return ""
        description = payload.get("description", "")
        if not isinstance(description, str):
            return ""
        return description.strip()

    m = re.search(r"(?:^|\n)description\s*:\s*(.+?)(?:\n|$)", frontmatter)
    if not m:
        return ""
    return m.group(1).strip().strip('"').strip("'")


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


# Regex for sanitizing paths used in prompt examples — only allow
# safe path characters to prevent prompt injection via crafted skill paths.
_SAFE_PATH_RE = re.compile(r"[^\w./ ,()'\-]", re.UNICODE)
_WINDOWS_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:(?:/|\\)")
_WINDOWS_UNC_PATH_RE = re.compile(r"^(//|\\\\)[^/\\]+[/\\][^/\\]+")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1F\x7F]")


def _is_windows_prompt_path(path: str) -> bool:
    """是否为 Windows 风格路径（仅 Windows 平台生效，对齐本体）。"""
    if os.name != "nt":
        return False
    return bool(_WINDOWS_DRIVE_PATH_RE.match(path) or _WINDOWS_UNC_PATH_RE.match(path))


def _sanitize_prompt_path_for_prompt(path: str) -> str:
    """净化 prompt 中展示的技能路径（防提示词注入，对齐本体）。"""
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
    sanitized = _SAFE_PATH_RE.sub("", path)
    return f"{drive_prefix}{sanitized}"


def _sanitize_prompt_description(description: str) -> str:
    """净化 prompt 中展示的技能描述（对齐本体）。"""
    description = str(description or "").replace("`", "")
    description = _CONTROL_CHARS_RE.sub(" ", description)
    return " ".join(description.split())


def _sanitize_skill_display_name(name: str) -> str:
    """清理技能展示名：不合法（非 ``[\\w.-]+``）返回
    ``<invalid_skill_name>``（对齐本体，不做额外 strip）。"""
    if _SKILL_NAME_RE.fullmatch(name):
        return name
    return "<invalid_skill_name>"


def _build_skill_read_command_example(path: str) -> str:
    """构造技能读取命令示例（对齐本体；Windows 平台用 ``type``）。"""
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
    读取）。空列表时同样返回完整模板（skills_block 为空）。
    """
    skills_lines: list[str] = []
    example_path = ""
    for skill in skills:
        display_name = _sanitize_skill_display_name(skill.name)

        # 对齐本体顺序：sandbox_only/workspace 先净化原始 description，
        # 净化后为空才落到 "Read SKILL.md for details."；其余类型在
        # description 为空时使用 "No description" 兜底。
        if skill.source_type in {"sandbox_only", "workspace"}:
            description = _sanitize_prompt_description(skill.description or "")
            if not description:
                description = "Read SKILL.md for details."
        else:
            description = skill.description or "No description"

        if skill.source_type == "sandbox_only":
            # Prefer the actual path from sandbox cache if available
            rendered_path = _sanitize_prompt_path_for_prompt(skill.path)
            if not rendered_path:
                rendered_path = _default_sandbox_skill_path(skill.name)
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
    # Sanitize example_path — it may originate from sandbox cache (untrusted)
    if example_path == "<skills_root>/<skill_name>/SKILL.md":
        example_path = "<skills_root>/<skill_name>/SKILL.md"
    else:
        example_path = _sanitize_prompt_path_for_prompt(example_path)
        example_path = example_path or "<skills_root>/<skill_name>/SKILL.md"
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

    构造参数对齐本体：``skills_root`` / ``plugins_root`` 缺省经
    ``astrbot.core.utils.astrbot_path`` 解析（宿主子进程注入
    ASTRBOT_DATA_PATH，指向宿主数据目录，与本体目录约定一致）。
    """

    def __init__(
        self,
        skills_root: str | None = None,
        plugins_root: str | None = None,
    ) -> None:
        from astrbot.core.utils.astrbot_path import (
            get_astrbot_data_path,
            get_astrbot_plugin_path,
            get_astrbot_skills_path,
        )

        self.skills_root = skills_root or get_astrbot_skills_path()
        self.plugins_root = plugins_root or get_astrbot_plugin_path()
        data_path = Path(get_astrbot_data_path())
        self.config_path = str(data_path / SKILLS_CONFIG_FILENAME)
        self.sandbox_skills_cache_path = str(
            data_path / SANDBOX_SKILLS_CACHE_FILENAME
        )
        try:
            os.makedirs(self.skills_root, exist_ok=True)
        except OSError as e:
            # 本体在此处直接 makedirs；SDK 插件子进程可能无写权限，容错降级
            logger.debug("技能目录 %s 创建失败（由宿主统一管理）: %s", self.skills_root, e)

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

        优先转发宿主 bridge.list_skills_v2(active_only, runtime,
        show_sandbox_path)（宿主已支持 sandbox 视图与 sandbox 路径形态）；
        宿主桥未提供 list_skills_v2（旧版宿主）时回退现有
        bridge.list_skills(active_only, runtime)。两路均不可用时恒返回
        空列表（不抛异常）。
        """
        raw: list | tuple | None = None
        bridge = _host_bridge()
        if bridge is not None:
            v2 = getattr(bridge, "list_skills_v2", None)
            if v2 is not None:
                try:
                    raw = v2(active_only or False, runtime or "", bool(show_sandbox_path))
                except Exception:
                    logger.warning("宿主 list_skills_v2 失败（回退 list_skills）")
                    raw = None
            if raw is None:
                fn = getattr(bridge, "list_skills", None)
                if fn is not None:
                    try:
                        raw = fn(active_only or False, runtime or "")
                    except Exception:
                        logger.warning("宿主 list_skills 失败（降级为空列表）")
                        raw = None
                else:
                    logger.debug("宿主 bridge 未提供 list_skills，技能列表为空")
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
        """启用/禁用指定技能（对齐本体：沙盒预置技能拒绝，其余转发宿主）。

        沙盒预置技能判定读取宿主共享的 sandbox_skills_cache.json（插件
        子进程与宿主 SkillManager 指向同一 data 目录），与宿主 Go 侧
        SetSkillActive 的校验语义一致。
        """
        if self.is_sandbox_only_skill(name):
            raise PermissionError(
                "Sandbox preset skill cannot be enabled/disabled from local skill management."
            )
        self._invoke("set_skill_active", name, bool(active))

    def delete_skill(self, name: str) -> None:
        """删除指定技能（对齐本体：沙盒预置/插件技能拒绝，其余转发宿主）。

        宿主 DeleteSkill 内部会一并清理沙盒缓存与 skills.json 条目。
        """
        if self.is_sandbox_only_skill(name):
            raise PermissionError(
                "Sandbox preset skill cannot be deleted from local skill management."
            )
        if self.is_plugin_skill(name):
            raise PermissionError(
                "Plugin-provided skill cannot be deleted from local skill management."
            )
        self._invoke("delete_skill", name)

    def is_sandbox_only_skill(self, name: str) -> bool:
        """是否为沙盒专属技能（对齐本体；等价宿主 Go IsSandboxOnlySkill）。

        本地 skills 目录下无该技能的 SKILL.md，且沙盒共享缓存
        （data/sandbox_skills_cache.json，由宿主沙盒运行时写入）中存在
        同名条目时为 True。非法技能名（无法作为目录段）直接返回 False。
        """
        if not name or name in {".", ".."} or not _SKILL_NAME_RE.match(name):
            return False
        skill_dir = Path(self.skills_root) / name
        skill_md_exists = _normalize_skill_markdown_path(skill_dir) is not None
        if skill_md_exists:
            return False
        cache = self._load_sandbox_skills_cache()
        skills = cache.get("skills", [])
        if not isinstance(skills, list):
            return False
        for item in skills:
            if not isinstance(item, dict):
                continue
            if str(item.get("name", "")).strip() == name:
                return True
        return False

    def is_plugin_skill(self, name: str) -> bool:
        """是否为插件内置技能（对齐本体：宿主列表 source_type=plugin）。"""
        return self._get_plugin_skill_dir(name) is not None

    def set_sandbox_skills_cache(self, skills: list[dict]) -> None:
        """持久化沙盒侧发现的技能元数据（按本体移植）。

        写入宿主共享缓存 data/sandbox_skills_cache.json（宿主 Go 侧
        ListSkills(runtime="sandbox") 与 dashboard 读同一文件）。条目按
        名字去重、路径经 _normalize_cached_sandbox_skill_path 净化。
        """
        deduped: dict[str, dict[str, str]] = {}
        for item in skills or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name or not _SKILL_NAME_RE.match(name):
                continue
            description = str(item.get("description", "") or "")
            path = _normalize_cached_sandbox_skill_path(
                name, str(item.get("path", "") or "")
            )
            deduped[name] = {
                "name": name,
                "description": description,
                "path": path,
            }
        cache = {
            "version": _SANDBOX_SKILLS_CACHE_VERSION,
            "skills": [deduped[name] for name in sorted(deduped)],
        }
        self._save_sandbox_skills_cache(cache)

    def get_sandbox_skills_cache_status(self) -> dict[str, object]:
        """获取沙盒技能缓存状态（键与值均对齐本体 get_sandbox_skills_cache_status）。"""
        cache = self._load_sandbox_skills_cache()
        skills = cache.get("skills", [])
        count = len(skills) if isinstance(skills, list) else 0
        return {
            "exists": os.path.exists(self.sandbox_skills_cache_path),
            "ready": count > 0,
            "count": count,
            "updated_at": cache.get("updated_at"),
        }

    # ── 本地持久化（与宿主 Go SkillManager 共享同一 data 目录文件）────
    def _load_config(self) -> dict:
        """读取 skills.json（对齐本体；文件缺失时返回默认配置副本）。"""
        if not os.path.exists(self.config_path):
            return json.loads(json.dumps(DEFAULT_SKILLS_CONFIG))
        with open(self.config_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "skills" not in data:
            return json.loads(json.dumps(DEFAULT_SKILLS_CONFIG))
        return data

    def _save_config(self, config: dict) -> None:
        """写 skills.json（对齐本体；注意与宿主写锁并存，勿高频调用）。"""
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)

    def _load_sandbox_skills_cache(self) -> dict:
        """读取沙盒技能共享缓存（对齐本体；损坏/缺失回退空缓存）。"""
        if not os.path.exists(self.sandbox_skills_cache_path):
            return {"version": _SANDBOX_SKILLS_CACHE_VERSION, "skills": []}
        try:
            with open(self.sandbox_skills_cache_path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"version": _SANDBOX_SKILLS_CACHE_VERSION, "skills": []}
            skills = data.get("skills", [])
            if not isinstance(skills, list):
                skills = []
            return {
                "version": int(data.get("version", _SANDBOX_SKILLS_CACHE_VERSION)),
                "skills": skills,
                "updated_at": data.get("updated_at"),
            }
        except Exception:
            return {"version": _SANDBOX_SKILLS_CACHE_VERSION, "skills": []}

    def _save_sandbox_skills_cache(self, cache: dict) -> None:
        """写沙盒技能共享缓存（对齐本体：附 version 与 UTC updated_at）。"""
        cache["version"] = _SANDBOX_SKILLS_CACHE_VERSION
        cache["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(self.sandbox_skills_cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

    def _remove_skill_from_sandbox_cache(self, name: str) -> None:
        """从沙盒共享缓存中移除指定技能（对齐本体；无变化不写盘）。"""
        cache = self._load_sandbox_skills_cache()
        skills = cache.get("skills", [])
        if not isinstance(skills, list):
            return

        filtered = [
            item
            for item in skills
            if not (
                isinstance(item, dict) and str(item.get("name", "")).strip() == name
            )
        ]

        if len(filtered) != len(skills):
            cache["skills"] = filtered
            self._save_sandbox_skills_cache(cache)

    def list_workspace_skills(
        self, workspace_root: str | Path | None
    ) -> list[SkillInfo]:
        """列出会话工作区下的请求级技能（对齐本体 list_workspace_skills）。

        仅识别 ``<workspace_root>/skills`` 下含规范 ``SKILL.md`` 的目录；
        技能名需满足 ``[\\w.-]+``；路径做 resolve 包含性校验（防符号链接
        逃逸）；frontmatter 读取上限 WORKSPACE_SKILL_FRONTMATTER_MAX_CHARS。
        workspace_root 为空或目录不存在时返回空列表。
        """
        if not workspace_root:
            return []

        raw_workspace_root = Path(workspace_root)
        skills_root = raw_workspace_root / WORKSPACE_SKILLS_ROOT
        if not skills_root.is_dir():
            return []

        try:
            resolved_workspace_root = raw_workspace_root.resolve(strict=True)
            resolved_skills_root = skills_root.resolve(strict=True)
            if not resolved_skills_root.is_relative_to(resolved_workspace_root):
                return []
            skill_dirs = sorted(
                resolved_skills_root.iterdir(), key=lambda item: item.name
            )
        except OSError:
            return []

        skills: list[SkillInfo] = []
        for skill_dir in skill_dirs:
            if not skill_dir.is_dir():
                continue
            skill_name = skill_dir.name
            if not _SKILL_NAME_RE.match(skill_name):
                continue
            try:
                entry_names = {entry.name for entry in skill_dir.iterdir()}
            except OSError:
                continue
            if "SKILL.md" not in entry_names:
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                continue

            try:
                resolved_skill_md = skill_md.resolve(strict=True)
            except OSError:
                continue
            if not resolved_skill_md.is_relative_to(resolved_skills_root):
                continue

            description = ""
            try:
                with resolved_skill_md.open(encoding="utf-8") as f:
                    content = f.read(WORKSPACE_SKILL_FRONTMATTER_MAX_CHARS)
                description = _parse_frontmatter_description(content)
            except (OSError, UnicodeError):
                description = ""

            skills.append(
                SkillInfo(
                    name=skill_name,
                    description=description,
                    path=resolved_skill_md.as_posix(),
                    active=True,
                    source_type="workspace",
                    source_label="workspace",
                    local_exists=True,
                    readonly=True,
                )
            )

        return skills

    def install_skill_from_zip(
        self,
        zip_path: str,
        *,
        overwrite: bool = True,
        skill_name_hint: str | None = None,
    ) -> str:
        """从 zip 安装技能（按本体 install_skill_from_zip 移植）。

        支持两种布局：
        - 根模式：zip 根直接含 ``SKILL.md``/``skill.md``，技能名取
          skill_name_hint（规范化后）或 zip 文件名 stem；
        - 目录模式：顶层目录各含 ``SKILL.md``，逐目录安装
          （skill_name_hint 仅在唯一顶层目录时生效）。

        ``overwrite=False`` 且目标已存在时抛 FileExistsError（不部分安装）；
        成功返回以 ", " 连接的已安装技能名；归档无有效技能抛 ValueError。
        安装后经宿主 RPC 将技能置为启用（set_skill_active）。
        """
        from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

        zip_path_obj = Path(zip_path)
        if not zip_path_obj.exists():
            raise FileNotFoundError(f"Zip file not found: {zip_path}")
        if not zipfile.is_zipfile(str(zip_path_obj)):
            raise ValueError("Uploaded file is not a valid zip archive.")

        installed_skills: list[str] = []

        with zipfile.ZipFile(str(zip_path_obj)) as zf:
            names = [
                name
                for name in (entry.replace("\\", "/") for entry in zf.namelist())
                if name and not _is_ignored_zip_entry(name)
            ]
            file_names = [name for name in names if name and not name.endswith("/")]
            if not file_names:
                raise ValueError("Zip archive is empty.")

            has_root_skill_md = any(
                len(parts := PurePosixPath(name).parts) == 1
                and parts[0] in {"SKILL.md", "skill.md"}
                for name in file_names
            )
            root_mode = has_root_skill_md

            archive_skill_name = None
            if skill_name_hint is not None:
                archive_skill_name = _normalize_skill_name(skill_name_hint)
                if archive_skill_name and not _SKILL_NAME_RE.fullmatch(
                    archive_skill_name
                ):
                    raise ValueError("Invalid skill name.")

            for name in names:
                if not name:
                    continue
                if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                    raise ValueError("Zip archive contains absolute paths.")
                parts = PurePosixPath(name).parts
                if ".." in parts:
                    raise ValueError("Zip archive contains invalid relative paths.")

            if not root_mode and not overwrite:
                top_dirs = {PurePosixPath(n).parts[0] for n in file_names if n.strip()}
                conflict_dirs: list[str] = []
                for src_dir_name in top_dirs:
                    if (
                        f"{src_dir_name}/SKILL.md" not in file_names
                        and f"{src_dir_name}/skill.md" not in file_names
                    ):
                        continue

                    candidate_name = _normalize_skill_name(src_dir_name)
                    if not candidate_name or not _SKILL_NAME_RE.fullmatch(
                        candidate_name
                    ):
                        continue

                    if archive_skill_name and len(top_dirs) == 1:
                        target_name = archive_skill_name
                    else:
                        target_name = candidate_name

                    dest_dir = Path(self.skills_root) / target_name
                    if dest_dir.exists():
                        conflict_dirs.append(str(dest_dir))

                if conflict_dirs:
                    raise FileExistsError(
                        "One or more skills from the archive already exist and "
                        "overwrite=False. No skills were installed. Conflicting "
                        f"paths: {', '.join(conflict_dirs)}"
                    )

            with tempfile.TemporaryDirectory(dir=get_astrbot_temp_path()) as tmp_dir:
                for member in zf.infolist():
                    member_name = member.filename.replace("\\", "/")
                    if not member_name or _is_ignored_zip_entry(member_name):
                        continue
                    zf.extract(member, tmp_dir)

                if root_mode:
                    archive_hint = _normalize_skill_name(
                        archive_skill_name or zip_path_obj.stem
                    )
                    if not archive_hint or not _SKILL_NAME_RE.fullmatch(archive_hint):
                        raise ValueError("Invalid skill name.")
                    skill_name = archive_hint

                    src_dir = Path(tmp_dir)
                    normalized_path = _normalize_skill_markdown_path(src_dir)
                    if normalized_path is None:
                        raise ValueError(
                            "SKILL.md not found in the root of the zip archive."
                        )

                    dest_dir = Path(self.skills_root) / skill_name
                    if dest_dir.exists() and overwrite:
                        shutil.rmtree(dest_dir)
                    elif dest_dir.exists() and not overwrite:
                        raise FileExistsError(f"Skill {skill_name} already exists.")

                    shutil.move(str(src_dir), str(dest_dir))
                    self.set_skill_active(skill_name, True)
                    installed_skills.append(skill_name)

                else:
                    top_dirs = {
                        PurePosixPath(n).parts[0] for n in file_names if n.strip()
                    }

                    for archive_root_name in top_dirs:
                        archive_root_name_normalized = _normalize_skill_name(
                            archive_root_name
                        )

                        if (
                            f"{archive_root_name}/SKILL.md" not in file_names
                            and f"{archive_root_name}/skill.md" not in file_names
                        ):
                            continue

                        if archive_root_name in {".", "..", ""} or not (
                            _SKILL_NAME_RE.fullmatch(archive_root_name_normalized)
                        ):
                            continue

                        if archive_skill_name and len(top_dirs) == 1:
                            skill_name = archive_skill_name
                        else:
                            skill_name = archive_root_name_normalized

                        src_dir = Path(tmp_dir) / archive_root_name
                        normalized_path = _normalize_skill_markdown_path(src_dir)
                        if normalized_path is None:
                            continue

                        dest_dir = Path(self.skills_root) / skill_name
                        if dest_dir.exists():
                            if not overwrite:
                                raise FileExistsError(
                                    f"Skill {skill_name} already exists."
                                )
                            shutil.rmtree(dest_dir)

                        shutil.move(str(src_dir), str(dest_dir))
                        self.set_skill_active(skill_name, True)
                        installed_skills.append(skill_name)

        if not installed_skills:
            raise ValueError(
                "No valid SKILL.md found in any folder of the zip archive."
            )

        return ", ".join(installed_skills)

    def _iter_plugin_skill_dirs(self) -> list[tuple[str, str, Path, bool]]:
        """枚举插件提供的技能目录（签名与返回形态对齐本体）。

        SDK 薄壳不扫描本地 plugins 目录（宿主 Go 侧统一发现），改为经
        宿主 ListSkills 取 source_type=plugin 的技能，还原
        ``(skill_name, plugin_name, skill_dir, preset)`` 四元组。
        """
        result: list[tuple[str, str, Path, bool]] = []
        for s in self.list_skills():
            if s.source_type != "plugin" or not s.path:
                continue
            result.append(
                (s.name, s.plugin_name or s.source_label, Path(s.path).parent, s.preset)
            )
        return result

    def _get_plugin_skill_dir(self, name: str) -> Path | None:
        """按技能名取插件技能目录（对齐本体语义：无则 None）。"""
        for (
            skill_name,
            _plugin_name,
            skill_dir,
            _preset,
        ) in self._iter_plugin_skill_dirs():
            if skill_name == name:
                return skill_dir
        return None

    @staticmethod
    def _parse_frontmatter_description(text: str) -> str:
        """解析 SKILL.md frontmatter 的 description 字段（委托模块级对齐实现）。"""
        return _parse_frontmatter_description(text)


__all__ = ["SkillInfo", "SkillManager", "build_skills_prompt"]
