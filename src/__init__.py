# -*- coding: utf-8 -*-
"""FPS 多模态态势感知系统 —— 源码包。"""

from .config_manager import (
    AppConfig,
    AudioConfig,
    ConfigManager,
    CVConfig,
    FusionConfig,
    RoiBBox,
    get_config,
)

__all__ = [
    "AppConfig",
    "AudioConfig",
    "ConfigManager",
    "CVConfig",
    "FusionConfig",
    "RoiBBox",
    "get_config",
]
