"""W9 多模态与个人资产库 —— 对外门面。

模块分工（任务书 §1）：

* :mod:`.store` —— ``assets`` 表 + 本地磁盘文件（owner 隔离、路径穿越防护、大小上限）
* :mod:`.audiogen` —— 纯 Python 芯片音乐合成器（零依赖、零模型、未配 key 也有真实产出）
* :mod:`.imagegen` —— OpenAI 兼容图片/TTS 通道（未配置时诚实 503）+ 定价护栏

对外只暴露 :class:`AssetService`（API 层用）与通道状态函数（前端「通道状态」卡）。
"""

from __future__ import annotations

from .audiogen import MOOD_IDS, MOODS, SynthResult, mood_catalog, render_wav
from .imagegen import (
    ImageGenerationService,
    ProviderConfig,
    ProviderNotConfigured,
    SpeechService,
    channel_status,
    forget_credentials,
    masked_credentials,
    resolve_config,
    set_credentials,
    sniff_image_mime,
)
from .store import (
    ASSET_KINDS,
    DEFAULT_MAX_BYTES,
    MAX_BYTES_BY_KIND,
    Asset,
    AssetService,
    PayloadTooLarge,
    assets_root,
    resolve_within,
)

__all__ = [
    "ASSET_KINDS",
    "MAX_BYTES_BY_KIND",
    "DEFAULT_MAX_BYTES",
    "Asset",
    "AssetService",
    "PayloadTooLarge",
    "assets_root",
    "resolve_within",
    "MOODS",
    "MOOD_IDS",
    "SynthResult",
    "render_wav",
    "mood_catalog",
    "ImageGenerationService",
    "SpeechService",
    "ProviderConfig",
    "ProviderNotConfigured",
    "channel_status",
    "resolve_config",
    "set_credentials",
    "forget_credentials",
    "masked_credentials",
    "sniff_image_mime",
]
