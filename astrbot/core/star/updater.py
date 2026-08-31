"""插件元数据常量与更新器（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot/core/star/updater.py`：
- 模块级常量 PLUGIN_METADATA_FILENAMES / PLUGIN_METADATA_REQUIRED_FIELDS /
  PLUGIN_METADATA_MAX_BYTES / PLUGIN_REPOSITORY_TIMEOUT_SECONDS /
  PLUGIN_GIT_CLONE_TIMEOUT_SECONDS；
- `_PluginUpdater` 的公开方法面（validate_plugin_metadata /
  find_plugin_metadata_entry / inspect_plugin_directory /
  inspect_plugin_archive / validate_plugin_archive /
  inspect_plugin_repository / install / update）。

纯本地的元数据定位与校验逻辑按本体移植为真实现；涉及网络下载 / git 克隆 /
落盘安装的方法（install / update / inspect_plugin_repository /
_clone_repository）在 Go 宿主中由原生能力处理（internal/plugin
InstallFromSource / ReinstallSource），SDK 侧补齐签名并降级抛出
RuntimeError，保证按名调用不产生 AttributeError / TypeError。
"""

import os
import zipfile
from pathlib import Path

PLUGIN_METADATA_FILENAMES = ("metadata.yaml", "metadata.yml")
PLUGIN_METADATA_REQUIRED_FIELDS = ("name", "desc", "version", "author")
PLUGIN_METADATA_MAX_BYTES = 1024 * 1024
PLUGIN_REPOSITORY_TIMEOUT_SECONDS = 15
PLUGIN_GIT_CLONE_TIMEOUT_SECONDS = 180


def _load_yaml_metadata(text: str) -> dict:
    """解析插件元数据 YAML（优先 PyYAML，未安装时降级为简易解析）。

    本体经 yaml.safe_load 解析（PyYAML 为本体必装依赖）；SDK 运行环境
    可能未声明 PyYAML，此时用逐行 ``key: value`` 简易解析兜底（插件
    metadata.yaml 为扁平键值，够用）；值支持单/双引号与行内注释剥离。
    """
    try:
        import yaml
    except ModuleNotFoundError:
        metadata: dict = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            elif " #" in value:
                value = value.split(" #", 1)[0].strip()
            if key:
                metadata[key] = value
        return metadata
    else:
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError("metadata 格式错误。") from exc
        return loaded if isinstance(loaded, dict) else {}


__all__ = ["PLUGIN_METADATA_FILENAMES"]


class _PluginUpdater:
    """插件安装/更新器。

    元数据校验方法为本体移植的真实现；安装/更新动作交由 Go 宿主
    原生处理（internal/plugin），SDK 侧方法仅保证签名对齐、调用报错
    语义清晰。
    """

    def __init__(self, verify: str | bool | None = None) -> None:
        self.verify = verify
        self.plugin_store_path = self.get_plugin_store_path()

    def get_plugin_store_path(self) -> str:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_path

        return get_astrbot_plugin_path()

    @staticmethod
    def _resolve_archive_root_dir(entries: list[str]) -> str:
        """解析压缩包内唯一根目录名（对齐本体 zip_updater 实现）。

        Args:
            entries: 压缩包成员名列表。

        Returns:
            根目录名（os.sep 分隔）；无根目录时返回空字符串。
        """
        normalized_entries = [os.path.normpath(entry) for entry in entries]
        portable_entries = [entry.replace("\\", "/") for entry in normalized_entries]
        root_candidates: list[str] = []

        for raw_entry, normalized_entry, portable_entry in zip(
            entries, normalized_entries, portable_entries
        ):
            if normalized_entry == ".":
                continue

            has_children = any(
                other_entry != portable_entry
                and other_entry.startswith(f"{portable_entry}/")
                for other_entry in portable_entries
            )
            if raw_entry.endswith(("/", "\\")) or has_children:
                root_candidates.append(normalized_entry)
                continue

            parent_portable, _, _ = portable_entry.rpartition("/")
            if not parent_portable:
                return ""
            root_candidates.append(parent_portable.replace("/", os.sep))

        if not root_candidates:
            return ""
        return os.path.commonpath(root_candidates)

    @classmethod
    def find_plugin_metadata_entry(cls, entries: list[str]) -> str | None:
        """在压缩包成员中定位 AstrBot 插件元数据文件（对齐本体）。

        Args:
            entries: 压缩包成员名列表。

        Returns:
            元数据文件的原始成员名；未找到返回 None。
        """
        update_dir = cls._resolve_archive_root_dir(entries)
        portable_update_dir = os.path.normpath(update_dir).replace("\\", "/")
        if portable_update_dir == ".":
            portable_update_dir = ""

        entries_by_portable_path = {}
        for entry in entries:
            portable_entry = os.path.normpath(entry).replace("\\", "/")
            if portable_entry in ("", "."):
                continue
            entries_by_portable_path[portable_entry] = entry

        metadata_candidates = (
            [
                f"{portable_update_dir}/{filename}"
                for filename in PLUGIN_METADATA_FILENAMES
            ]
            if portable_update_dir
            else list(PLUGIN_METADATA_FILENAMES)
        )
        for candidate in metadata_candidates:
            if candidate in entries_by_portable_path:
                return entries_by_portable_path[candidate]
        return None

    @staticmethod
    def validate_plugin_metadata(metadata: object, metadata_label: str = "metadata.yaml") -> None:
        """校验插件元数据内容（对齐本体语义与参数名 metadata_label）。

        Args:
            metadata: 已解析的元数据 YAML 内容（dict）。
            metadata_label: 元数据文件名或压缩包成员名，用于错误信息。

        Raises:
            ValueError: 元数据格式错误或缺少必需字段。
        """
        if not isinstance(metadata, dict):
            raise ValueError(f"{metadata_label} 格式错误。")

        normalized_metadata = dict(metadata)
        if "desc" not in normalized_metadata and "description" in normalized_metadata:
            normalized_metadata["desc"] = normalized_metadata["description"]

        missing_fields = [
            field
            for field in PLUGIN_METADATA_REQUIRED_FIELDS
            if field not in normalized_metadata
        ]
        if missing_fields:
            raise ValueError(
                f"{metadata_label} 中缺少必需字段: {', '.join(missing_fields)}。"
            )

        invalid_fields = [
            field
            for field in PLUGIN_METADATA_REQUIRED_FIELDS
            if not isinstance(normalized_metadata[field], str)
            or not normalized_metadata[field].strip()
        ]
        if invalid_fields:
            raise ValueError(
                f"{metadata_label} 中字段 {', '.join(invalid_fields)} 必须是非空字符串。"
            )

    @classmethod
    def inspect_plugin_directory(cls, plugin_path: str | Path) -> dict[str, object]:
        """检查仓库目录中的插件元数据（对齐本体）。

        Args:
            plugin_path: 含插件元数据的仓库工作目录。

        Returns:
            {"metadata_entry": 文件名, "metadata": 解析后的元数据}。

        Raises:
            ValueError: 目录不是合法的 AstrBot 插件。
        """
        root = Path(plugin_path)
        for filename in PLUGIN_METADATA_FILENAMES:
            metadata_path = root / filename
            if not metadata_path.is_file():
                continue
            if metadata_path.stat().st_size > PLUGIN_METADATA_MAX_BYTES:
                raise ValueError(f"{filename} 超过 1MB。")
            try:
                metadata_text = metadata_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"{filename} 必须使用 UTF-8 编码。") from exc
            metadata = _load_yaml_metadata(metadata_text)
            cls.validate_plugin_metadata(metadata, filename)
            return {"metadata_entry": filename, "metadata": metadata}
        raise ValueError("未在仓库根目录找到 metadata.yaml 或 metadata.yml。")

    @classmethod
    def inspect_plugin_archive(cls, zip_path: str) -> dict[str, object]:
        """检查插件压缩包中的元数据（对齐本体）。

        Args:
            zip_path: 插件压缩包路径。

        Returns:
            {"metadata_entry": 成员名, "metadata": 解析后的元数据}。

        Raises:
            ValueError: 压缩包不是合法的 AstrBot 插件。
        """
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                metadata_entry = cls.find_plugin_metadata_entry(z.namelist())
                if metadata_entry is None:
                    raise ValueError(
                        "压缩包不是合法的 AstrBot 插件：未找到 metadata.yaml 或 metadata.yml。"
                    )

                try:
                    metadata_text = z.read(metadata_entry).decode("utf-8")
                    metadata = _load_yaml_metadata(metadata_text)
                except UnicodeDecodeError as exc:
                    raise ValueError(f"{metadata_entry} 必须使用 UTF-8 编码。") from exc

                cls.validate_plugin_metadata(metadata, metadata_entry)
                return {
                    "metadata_entry": metadata_entry,
                    "metadata": metadata,
                }
        except zipfile.BadZipFile as exc:
            raise ValueError("插件压缩包格式错误。") from exc

    @classmethod
    def validate_plugin_archive(cls, zip_path: str) -> str:
        """校验压缩包是否为合法的 AstrBot 插件（对齐本体）。

        Args:
            zip_path: 插件压缩包路径。

        Returns:
            插件元数据文件的压缩包成员名。

        Raises:
            ValueError: 压缩包不是合法的 AstrBot 插件。
        """
        inspection = cls.inspect_plugin_archive(zip_path)
        return str(inspection["metadata_entry"])

    async def _clone_repository(self, repo_url: str, target_path: str | Path) -> None:
        """浅克隆远程 Git 仓库。

        SDK 降级：克隆/安装/更新由 Go 宿主原生处理
        （internal/plugin InstallFromSource），此处仅保证签名可用。

        Raises:
            RuntimeError: 始终抛出，提示改用宿主安装能力。
        """
        raise RuntimeError(
            "SDK 不支持 git 克隆插件仓库：插件安装/更新由 Go 宿主原生处理，"
            "请使用宿主的插件安装能力。"
        )

    async def inspect_plugin_repository(
        self,
        repo_url: str,
        proxy: str = "",
    ) -> dict[str, object]:
        """读取并校验仓库来源插件的元数据。

        SDK 降级：需要网络访问（httpx / git），宿主原生处理插件安装
        前的校验，此处仅保证签名可用。

        Raises:
            RuntimeError: 始终抛出，提示改用宿主安装能力。
        """
        raise RuntimeError(
            "SDK 不支持在线检查插件仓库：插件安装/元数据校验由 Go 宿主原生处理。"
        )

    async def install(self, repo_url: str, proxy="", download_url: str = "") -> str:
        """从仓库安装插件。

        SDK 降级：插件安装由 Go 宿主原生处理
        （internal/plugin/runtime.go InstallFromSource）。

        Raises:
            RuntimeError: 始终抛出，提示改用宿主安装能力。
        """
        raise RuntimeError(
            "SDK 不支持安装插件：插件安装由 Go 宿主原生处理，请使用宿主的插件安装能力。"
        )

    async def update(
        self,
        plugin,
        proxy="",
        download_url: str = "",
        repo_url: str = "",
    ) -> str:
        """更新已安装插件。

        SDK 降级：插件更新由 Go 宿主原生处理
        （internal/plugin/runtime_admin.go ReinstallSource）。

        Raises:
            RuntimeError: 始终抛出，提示改用宿主更新能力。
        """
        raise RuntimeError(
            "SDK 不支持更新插件：插件更新由 Go 宿主原生处理，请使用宿主的插件更新能力。"
        )
