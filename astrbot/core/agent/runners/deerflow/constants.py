"""Deerflow Agent Runner 常量（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.agent.runners.deerflow.constants`。
"""
# Deerflow 代理的 provider_type 标识
DEERFLOW_PROVIDER_TYPE = "deerflow"
# 存储 Deerflow 会话 thread_id 的 SharedPreferences 键
DEERFLOW_THREAD_ID_KEY = "deerflow_thread_id"
# Deerflow 临时会话前缀（Go 宿主无 Deerflow 集成，仅保持常量面兼容）
DEERFLOW_SESSION_PREFIX = "deerflow-ephemeral"
# 会话偏好中保存的 agent runner provider id 键
DEERFLOW_AGENT_RUNNER_PROVIDER_ID_KEY = "deerflow_agent_runner_provider_id"
