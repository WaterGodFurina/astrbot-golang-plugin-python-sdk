"""默认配置常量（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.config.default` 的常用常量。
"""
import os

from astrbot import __version__
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

VERSION = __version__

DB_PATH = os.path.join(get_astrbot_data_path(), "data_v4.db")

# 支持统一 Webhook 回调的平台（对齐本体 config.default.WEBHOOK_SUPPORTED_PLATFORMS）。
WEBHOOK_SUPPORTED_PLATFORMS = [
    "qq_official_webhook",
    "weixin_official_account",
    "wecom",
    "wecom_ai_bot",
    "slack",
    "lark",
    "line",
]

# 配置项类型 → 默认值映射（对齐本体 config.default.DEFAULT_VALUE_MAP 全量键集）。
DEFAULT_VALUE_MAP = {
    "int": 0,
    "float": 0.0,
    "bool": False,
    "string": "",
    "text": "",
    "list": [],
    "file": [],
    "object": {},
    "template_list": [],
    "dict": {},
}

# 默认配置骨架（对齐本体 config.default.DEFAULT_CONFIG；宿主运行时的
# 完整配置由宿主下发，此处保留本体同构的顶层与二级结构及默认值，
# 供 AstrBotConfig 无配置源构造 / check_config_integrity 参考使用。
# sandbox.cua_* 六项取自本体 computer.booters.cua_defaults.CUA_DEFAULT_CONFIG）。
DEFAULT_CONFIG = {
    "config_version": 2,
    "platform_settings": {
        "unique_session": False,
        "rate_limit": {
            "time": 60,
            "count": 30,
            "strategy": "stall",  # stall, discard
        },
        "reply_prefix": "",
        "forward_threshold": 1500,
        "enable_id_white_list": True,
        "id_whitelist": [],
        "id_whitelist_log": True,
        "wl_ignore_admin_on_group": True,
        "wl_ignore_admin_on_friend": True,
        "reply_with_mention": False,
        "reply_with_quote": False,
        "path_mapping": [],
        "segmented_reply": {
            "enable": False,
            "only_llm_result": True,
            "interval_method": "random",
            "interval": "1.5,3.5",
            "log_base": 2.6,
            "words_count_threshold": 150,
            "split_mode": "regex",  # regex 或 words
            "regex": ".*?[。？！~…]+|.+$",
            "split_words": ["。", "？", "！", "~", "…"],  # 当 split_mode 为 words 时使用
            "content_cleanup_rule": "",
        },
        "no_permission_reply": True,
        "empty_mention_waiting": True,
        "empty_mention_waiting_need_reply": True,
        "friend_message_needs_wake_prefix": False,
        "ignore_bot_self_message": False,
        "ignore_at_all": False,
    },
    "provider_sources": [],  # provider sources
    "provider": [],  # models from provider_sources
    "provider_settings": {
        "enable": True,
        "default_provider_id": "",
        "fallback_chat_models": [],
        "request_max_retries": 5,
        "default_image_caption_provider_id": "",
        "image_caption_prompt": "Please describe the image using Chinese.",
        "provider_pool": ["*"],  # "*" 表示使用所有可用的提供者
        "wake_prefix": "",
        "web_search": False,
        "websearch_provider": "tavily",
        "websearch_tavily_key": [],
        "websearch_bocha_key": [],
        "websearch_brave_key": [],
        "websearch_baidu_app_builder_key": "",
        "websearch_firecrawl_key": [],
        "websearch_exa_key": [],
        "web_search_link": False,
        "display_reasoning_text": False,
        "identifier": False,
        "group_name_display": False,
        "datetime_system_prompt": True,
        "default_personality": "default",
        "persona_pool": ["*"],
        "prompt_prefix": "{{prompt}}",
        "context_limit_reached_strategy": "llm_compress",  # or truncate_by_turns
        "max_context_length": -1,  # 默认不限制
        "dequeue_context_length": 1,
        "streaming_response": False,
        "show_tool_use_status": False,
        "show_tool_call_result": False,
        "buffer_intermediate_messages": False,
        "sanitize_context_by_modalities": False,
        "max_quoted_fallback_images": 20,
        "agent_runner_type": "local",
        "unsupported_streaming_strategy": "realtime_segmenting",
        "reachability_check": False,
        "max_agent_step": 30,
        "tool_call_timeout": 120,
        "tool_schema_mode": "full",
        "llm_safety_mode": True,
        "safety_mode_strategy": "system_prompt",
        "image_compress_enabled": True,
        "image_compress_options": {
            "max_size": 1280,
            "quality": 95,
        },
    },
    "subagent_orchestrator": {
        "main_enable": False,
        "remove_main_duplicate_tools": False,
        "agents": [],
    },
    "provider_stt_settings": {
        "enable": False,
        "provider_id": "",
    },
    "provider_tts_settings": {
        "enable": False,
        "provider_id": "",
        "dual_output": False,
        "use_file_service": False,
        "trigger_probability": 1.0,
    },
    "provider_ltm_settings": {
        "group_icl_enable": False,
        "group_message_max_cnt": 1000,
        "image_caption": False,
        "image_caption_provider_id": "",
        "group_message_history_enable": False,
        "group_message_history_max_cnt": 700,
        "active_reply": {
            "enable": False,
            "method": "possibility_reply",
            "possibility_reply": 0.1,
            "whitelist": [],
        },
    },
    "content_safety": {
        "also_use_in_response": False,
        "internal_keywords": {"enable": True, "extra_keywords": []},
        "baidu_aip": {"enable": False, "app_id": "", "api_key": "", "secret_key": ""},
    },
    "admins_id": ["astrbot"],
    "t2i": False,
    "t2i_word_threshold": 150,
    "t2i_strategy": "remote",
    "t2i_endpoint": "",
    "t2i_use_file_service": False,
    "t2i_active_template": "base",
    "http_proxy": "",
    "no_proxy": ["localhost", "127.0.0.1", "::1", "10.*", "192.168.*"],
    "dashboard": {
        "enable": True,
        "username": "astrbot",
        "password": "",
        "pbkdf2_password": "",
        "password_storage_upgraded": False,
        "password_change_required": False,
        "jwt_secret": "",
        "host": "0.0.0.0",
        "port": 6185,
        "disable_access_log": True,
        "trust_proxy_headers": False,
        "auth_rate_limit": {
            "enable": True,
            "average_interval": 1.0,
            "max_burst": 3,
        },
        "totp": {
            "enable": False,
            "secret": "",
            "recovery_code_hash": "",
        },
        "ssl": {
            "enable": False,
            "cert_file": "",
            "key_file": "",
            "ca_certs": "",
        },
    },
    "platform": [],
    "platform_specific": {
        # 平台特异配置：按平台分类，平台下按功能分组
        "lark": {
            "pre_ack_emoji": {"enable": False, "emojis": ["Typing"]},
        },
        "telegram": {
            "pre_ack_emoji": {"enable": False, "emojis": ["✍️"]},
        },
        "discord": {
            "pre_ack_emoji": {"enable": False, "emojis": ["🤔"]},
        },
    },
    "wake_prefix": ["/"],
    "log_level": "INFO",
    "log_file_enable": False,
    "log_file_path": "logs/astrbot.log",
    "log_file_max_mb": 20,
    "temp_dir_max_size": 1024,
    "trace_enable": False,
    "trace_log_enable": False,
    "trace_log_path": "logs/astrbot.trace.log",
    "trace_log_max_mb": 20,
    "pip_install_arg": "",
    "pypi_index_url": "https://mirrors.aliyun.com/pypi/simple/",
    "persona": [],  # deprecated
    "timezone": "Asia/Shanghai",
    "callback_api_base": "",
    "default_kb_collection": "",  # 默认知识库名称, 已经过时
    "plugin_set": ["*"],  # "*" 表示使用所有可用的插件, 空列表表示不使用任何插件
    "kb_names": [],  # 默认知识库名称列表
    "kb_fusion_top_k": 20,  # 知识库检索融合阶段返回结果数量
    "kb_final_top_k": 5,  # 知识库检索最终返回结果数量
    "kb_agentic_mode": False,
    "disable_builtin_commands": False,
    "disable_metrics": False,
}
