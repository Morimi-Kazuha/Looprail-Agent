"""Looprail Configuration 的公开入口。

Package 暴露 Two Layers：

Base Agent Runtime Layer 使用 ``Config`` + ``load_config`` + Path Helpers，覆盖从基础 Agent Framework
继承的 Agents、Channels、Providers、Tools Fields。Looprail Feature Layer 使用 ``LooprailConfig`` +
``load_looprail_config`` + Per-feature Blocks，例如 ``ContextConfig``、``CallEfficiencyConfig``、
``SkillForgeConfig``，定义于 :mod:`looprail.config.looprail`。

Loader 负责把 Disk/Env/Overrides 解析成类型化对象，Path Helpers 统一持久化根。配置加载成功只证明
Schema/迁移通过，不证明 Provider Credentials、Channel Connectivity 或 Sandbox Backend 实际可用。
"""

from looprail.config.loader import get_config_path, load_config
from looprail.config.looprail import (
    BudgetPolicyConfig,
    CallEfficiencyConfig,
    ContextConfig,
    LooprailConfig,
    SkillForgeConfig,
    SmartRoutingConfig,
    TokenWiseConfig,
    ToolResultLifecycleConfig,
    load_looprail_config,
)
from looprail.config.paths import (
    get_cli_history_path,
    get_cron_dir,
    get_data_dir,
    get_logs_dir,
    get_media_dir,
    get_runtime_subdir,
    get_workspace_path,
)
from looprail.config.schema import Config

__all__ = [
    # 基础层
    "Config",
    "load_config",
    "get_config_path",
    "get_data_dir",
    "get_runtime_subdir",
    "get_media_dir",
    "get_cron_dir",
    "get_logs_dir",
    "get_workspace_path",
    "get_cli_history_path",
    # Looprail 功能层
    "LooprailConfig",
    "load_looprail_config",
    "ContextConfig",
    "CallEfficiencyConfig",
    "TokenWiseConfig",
    "SkillForgeConfig",
    "BudgetPolicyConfig",
    "SmartRoutingConfig",
    "ToolResultLifecycleConfig",
]
