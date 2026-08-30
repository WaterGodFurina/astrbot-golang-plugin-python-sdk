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
import shlex
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


@dataclass(frozen=True)
class ParsedPackageInput:
    """解析后的包安装输入（对齐原版 ParsedPackageInput）。"""

    specs: tuple[str, ...]
    requirement_names: frozenset[str]


def looks_like_direct_reference(token: str) -> bool:
    """判断 token 是否为直接引用（本地路径 / git+ / url://，对齐原版）。"""
    candidate = str(token or "").strip()
    if not candidate:
        return False
    if candidate.startswith((".", "/", "~", "file://", "git+", "https://", "http://")):
        return True
    return "://" in candidate


def extract_requirement_name(raw_requirement: str) -> str | None:
    """从 requirements 行提取规范包名（对齐原版 extract_requirement_name）。"""
    line = str(raw_requirement).split("#", 1)[0].strip()
    if not line:
        return None
    if line.startswith(("-r", "--requirement", "-c", "--constraint")):
        return None
    egg_match = re.search(r"#egg=([A-Za-z0-9_.-]+)", str(raw_requirement))
    if egg_match:
        return canonicalize_distribution_name(egg_match.group(1))
    if line.startswith("-"):
        return None
    candidate = re.split(r"[<>=!~;\s\[]", line, maxsplit=1)[0].strip()
    if not candidate:
        return None
    return canonicalize_distribution_name(candidate)


def iter_requirements(requirements_path: str):
    """逐行迭代 requirements 文件（对齐原版 iter_requirements）。"""
    import os as _os

    if not _os.path.exists(requirements_path):
        return
    with open(requirements_path, encoding="utf-8") as f:
        for line in f:
            yield line.rstrip("\n")


def extract_requirement_names(requirements_path: str) -> set[str]:
    """提取 requirements 文件中的全部规范包名（对齐原版）。"""
    names: set[str] = set()
    for line in iter_requirements(requirements_path):
        parsed = _parse_requirement_line(line)
        if parsed is not None:
            names.add(parsed[0])
    return names


def get_requirement_check_paths() -> list[str]:
    """返回需参与缺失检查的 requirements 路径（SDK 薄壳：插件目录里常用项）。"""
    return ["requirements.txt"]


def find_missing_requirements(requirements_path: str) -> set[str] | None:
    """找出 requirements 中缺失/版本不匹配的包名；无法预检查返回 None。"""
    if not os.path.exists(requirements_path):
        return None
    plan = plan_missing_requirements_install(requirements_path)
    if plan is None:
        return None
    return set(plan.missing_names) | set(plan.version_mismatch_names)


def find_missing_requirements_from_lines(lines: Sequence[str]) -> set[str] | None:
    """从 requirements 行序列找出缺失/版本不匹配的包名。"""
    required: list[tuple[str, object | None]] = []
    for line in lines:
        parsed = _parse_requirement_line(line)
        if parsed is not None:
            required.append(parsed)
    if not required:
        return set()
    installed = collect_installed_distribution_versions()
    if installed is None:
        return None
    missing: set[str] = set()
    for name, specifier in required:
        installed_version = installed.get(name)
        if not installed_version:
            missing.add(name)
        elif specifier is not None and not _specifier_contains_version(specifier, installed_version):
            missing.add(name)
    return missing


def classify_missing_requirements_from_lines(lines: Sequence[str]):
    """按缺失/版本不匹配分类缺失依赖（对齐原版，返回 MissingRequirementsAnalysis）。"""
    required: list[tuple[str, object | None]] = []
    for line in lines:
        parsed = _parse_requirement_line(line)
        if parsed is not None:
            required.append(parsed)
    if not required:
        return MissingRequirementsAnalysis(missing_names=frozenset())
    installed = collect_installed_distribution_versions()
    if installed is None:
        return MissingRequirementsAnalysis(missing_names=frozenset())
    missing: set[str] = set()
    mismatch: set[str] = set()
    for name, specifier in required:
        installed_version = installed.get(name)
        if not installed_version:
            missing.add(name)
        elif specifier is not None and not _specifier_contains_version(specifier, installed_version):
            missing.add(name)
            mismatch.add(name)
    return MissingRequirementsAnalysis(
        missing_names=frozenset(missing), version_mismatch_names=frozenset(mismatch)
    )


def build_missing_requirements_install_lines(lines: Sequence[str], missing_names) -> tuple[str, ...]:
    """构造仅含缺失依赖的安装行（对齐原版）。"""
    if missing_names is None:
        return tuple(line for line in lines if (parsed := _parse_requirement_line(line)) is not None)
    missing: set[str] = set(missing_names)
    return tuple(
        line
        for line in lines
        if (parsed := _parse_requirement_line(line)) is not None and parsed[0] in missing
    )


def find_missing_requirements_or_raise(requirements_path: str) -> set[str]:
    """找出缺失依赖；无法检查时抛 RequirementsPrecheckFailed（对齐原版）。"""
    missing = find_missing_requirements(requirements_path)
    if missing is None:
        raise RequirementsPrecheckFailed(
            f"无法预检查 requirements 缺失依赖: {requirements_path}"
        )
    return missing


def parse_package_install_input(raw_input: str) -> ParsedPackageInput:
    """解析用户输入的包安装文本（对齐原版 parse_package_install_input）。

    Returns:
        ParsedPackageInput：拆好的 specs 行 + 从各 token 提取的规范包名。
    """
    specs: list[str] = []
    requirement_names: set[str] = set()
    normalized = str(raw_input or "").strip()
    if not normalized:
        return ParsedPackageInput(specs=(), requirement_names=frozenset())

    for raw_line in normalized.splitlines():
        line = strip_inline_requirement_comment(raw_line)
        if not line:
            continue
        if Requirement is not None:
            try:
                Requirement(line)
            except InvalidRequirement:
                tokens = shlex.split(line)
                if not tokens:
                    continue
                specs.extend(tokens)
                for token in tokens:
                    name = extract_requirement_name(token)
                    if name:
                        requirement_names.add(name)
                continue
        specs.append(line)
        name = _parse_requirement_line(line)
        if name is not None:
            requirement_names.add(name[0])
    return ParsedPackageInput(
        specs=tuple(specs), requirement_names=frozenset(requirement_names)
    )
