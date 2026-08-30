"""数据目录解析（对齐 Python 本体 astrbot.core.utils.astrbot_path）。

宿主子进程的 ASTRBOT_DATA_PATH 指向宿主数据目录（data/）。
"""
import os
import tempfile


def get_astrbot_data_path() -> str:
    """返回 AstrBot 数据目录（宿主注入 ASTRBOT_DATA_PATH，缺省为 cwd）。"""
    return os.environ.get("ASTRBOT_DATA_PATH", os.getcwd())


def get_astrbot_root() -> str:
    """返回 AstrBot 根目录。

    Go 宿主无独立源码树，简单返回数据目录（ASTRBOT_DATA_PATH）或 cwd。
    """
    return get_astrbot_data_path()


def get_astrbot_path() -> str:
    """返回 AstrBot 项目路径（占位实现）。

    Go 宿主无源码树概念，简单返回数据目录或 cwd。
    """
    return get_astrbot_data_path()


def get_astrbot_temp_path() -> str:
    """返回 AstrBot 临时数据目录（数据目录下 temp，不存在则创建）。"""
    path = os.path.realpath(os.path.join(get_astrbot_data_path(), "temp"))
    os.makedirs(path, exist_ok=True)
    return path


def get_astrbot_plugin_path() -> str:
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "plugins"))


def get_astrbot_config_path() -> str:
    return os.path.join(get_astrbot_data_path(), "config")


def get_astrbot_plugin_data_path() -> str:
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "plugin_data"))


def get_astrbot_webui_path() -> str:
    return os.path.join(get_astrbot_data_path(), "webui")


def get_astrbot_builtin_plugin_path() -> str:
    """返回 AstrBot 内置插件目录（Go 宿主无源码树，占位为数据目录）。"""
    return os.path.realpath(os.path.join(get_astrbot_path(), "astrbot", "builtin_stars"))


def get_astrbot_plugin_data_path() -> str:
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "plugin_data"))


def get_astrbot_t2i_templates_path() -> str:
    """返回 AstrBot T2I 模板目录。"""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "t2i_templates"))


def get_astrbot_webchat_path() -> str:
    """返回 AstrBot WebChat 数据目录。"""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "webchat"))


def get_astrbot_skills_path() -> str:
    """返回 AstrBot 技能目录。"""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "skills"))


def get_astrbot_workspaces_path() -> str:
    """返回 AstrBot 工作区目录。"""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "workspaces"))


def get_astrbot_site_packages_path() -> str:
    """返回 AstrBot 第三方 site-packages 目录。"""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "site-packages"))


def get_astrbot_knowledge_base_path() -> str:
    """返回 AstrBot 知识库根目录。"""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "knowledge_base"))


def get_astrbot_backups_path() -> str:
    """返回 AstrBot 备份目录。"""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "backups"))


def get_astrbot_system_tmp_path() -> str:
    """返回共享的系统临时目录（本地工具使用），对齐 Python 本体。

    实现为 ``tempfile.gettempdir()/.astrbot``，不存在则创建。
    """
    path = os.path.realpath(os.path.join(tempfile.gettempdir(), ".astrbot"))
    os.makedirs(path, exist_ok=True)
    return path
