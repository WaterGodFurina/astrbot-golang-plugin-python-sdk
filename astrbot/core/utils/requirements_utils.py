"""插件 requirements 缺失预检查工具（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.utils.requirements_utils`：提供
`MissingRequirementsPlan`（dataclass）与 `plan_missing_requirements_install`
等符号。SDK 为简化实现：读取 requirements 文本并对照当前环境已安装
distribution 判定缺失/版本不匹配，任何解析异常都回退返回 None（表示
“无法安全裁剪，安装完整 requirements”），与本体行为一致。
"""
from __future__ import annotations

import importlib.metadata as importlib_metadata
import logging
import os
import re
from dataclasses import dataclass
from typing import Sequence

try:
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.version import InvalidVersion, Version
except Exception:  # pragma: no cover - 环境无 packaging 时降级
    InvalidRequirement = None  # type: ignore
    Requirement = None  # type: ignore
    SpecifierSet = None  # type: ignore
    InvalidVersion = None  # type: ignore
    Version = None  # type: ignore

logger = logging.getLogger("astrbot")


class RequirementsPrecheckFailed(Exception):
    """当 requirements 缺失预检查失败时抛出。"""


@dataclass(frozen=True)
class MissingRequirementsAnalysis:
    missing_names: frozenset[str]
    version_mismatch_names: frozenset[str] = frozenset()


@dataclass(frozen=True)
class MissingRequirementsPlan:
    missing_names: frozenset[str]
    install_lines: tuple[str, ...]
    version_mismatch_names: frozenset[str] = frozenset()
    fallback_reason: str | None = None


def canonicalize_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip("-").lower()


def strip_inline_requirement_comment(raw_input: str) -> str:
    if raw_input.lstrip().startswith("#"):
        return ""
    return re.split(r"[ \t]+#", raw_input, maxsplit=1)[0].strip()


def _specifier_contains_version(specifier, version: str) -> bool:
    try:
        parsed_version = Version(version)
    except Exception:
        return False
    try:
        return specifier.contains(parsed_version, prereleases=True)
    except Exception:
        return False


def _parse_requirement_line(line: str):
    """解析单行 requirements，返回 (规范名, specifier) 或 None。"""
    line = strip_inline_requirement_comment(line)
    if not line or line.startswith(("-", "--")):
        return None
    if "#" in line:
        line = line.split("#", 1)[0].strip()
    if not line:
        return None
    if Requirement is not None:
        try:
            req = Requirement(line)
        except InvalidRequirement:
            return None
        if req.marker and not req.marker.evaluate():
            return None
        return canonicalize_distribution_name(req.name), (req.specifier or None)
    # 无 packaging 时退化为取第一个 token 作为包名
    name = re.split(r"[<>=!~;\s\[]", line, maxsplit=1)[0].strip()
    return canonicalize_distribution_name(name), None


def collect_installed_distribution_versions() -> dict[str, str] | None:
    """收集当前环境已安装 distribution 的 {规范名: 版本}。"""
    installed: dict[str, str] = {}
    try:
        for distribution in importlib_metadata.distributions():
            distribution_name = distribution.metadata["Name"] if "Name" in distribution.metadata else None
            if not distribution_name:
                continue
            installed.setdefault(
                canonicalize_distribution_name(distribution_name),
                distribution.version,
            )
    except Exception as exc:
        logger.warning("读取已安装依赖失败，跳过缺失依赖预检查: %s", exc)
        return None
    return installed


def plan_missing_requirements_install(requirements_path: str):
    """检查 requirements 缺失项，返回 MissingRequirementsPlan。

    无法安全预检查（文件不存在/读取失败/无 packaging）时返回 None，
    语义与本体一致：调用方回退安装完整 requirements。
    """
    if not os.path.exists(requirements_path):
        return None
    try:
        with open(requirements_path, encoding="utf-8") as f:
            lines = [line for line in f]
    except Exception as exc:
        logger.warning("预检查缺失依赖失败，将回退到完整安装: %s (%s)", requirements_path, exc)
        return None

    required: list[tuple[str, object | None]] = []
    for line in lines:
        parsed = _parse_requirement_line(line)
        if parsed is not None:
            required.append(parsed)
    if not required:
        return MissingRequirementsPlan(
            missing_names=frozenset(),
            version_mismatch_names=frozenset(),
            install_lines=(),
        )

    installed = collect_installed_distribution_versions()
    if installed is None:
        return None

    missing: set[str] = set()
    version_mismatch_names: set[str] = set()
    for name, specifier in required:
        installed_version = installed.get(name)
        if not installed_version:
            missing.add(name)
            continue
        if specifier is not None and not _specifier_contains_version(specifier, installed_version):
            missing.add(name)
            version_mismatch_names.add(name)

    install_lines = tuple(
        line
        for line in lines
        if (parsed := _parse_requirement_line(line)) is not None
        and parsed[0] in missing
    )
    return MissingRequirementsPlan(
        missing_names=frozenset(missing),
        version_mismatch_names=frozenset(version_mismatch_names),
        install_lines=install_lines,
    )
