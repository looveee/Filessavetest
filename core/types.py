"""共享数据类型定义。

捕获层（capture）产出的原始数据结构。下游模块（如同步、编码、推理）
统一消费这些带高精度时间戳的对象。所有时间戳均使用
``time.perf_counter_ns()`` 生成，单位为纳秒，仅用于相对时序对齐，
不代表墙上时钟时间。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(slots=True)
class RawVideoFrame:
    """一帧原始截图数据。

    Attributes:
        image: 截图像素，numpy 数组。默认保持 mss 输出的 BGRA 格式，
            形状为 ``(height, width, 4)``，dtype 为 ``uint8``。
        timestamp_ns: 抓取完成时刻的高精度时间戳（``perf_counter_ns``）。
        frame_id: 自增帧序号，从 1 开始，用于检测丢帧。
        roi: 本帧对应的截取区域，包含 ``left/top/width/height``。
    """

    image: np.ndarray
    timestamp_ns: int
    frame_id: int
    roi: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class RawAudioChunk:
    """一段原始音频数据块。

    Attributes:
        samples: 音频采样，``float32`` numpy 数组，已归一化到 [-1, 1]，
            形状为 ``(frames, channels)``。
        timestamp_ns: 该数据块读取完成时刻的高精度时间戳。
        chunk_id: 自增块序号，从 1 开始。
        sample_rate: 采样率（Hz）。
        channels: 声道数。
    """

    samples: np.ndarray
    timestamp_ns: int
    chunk_id: int
    sample_rate: int
    channels: int

    @property
    def frames(self) -> int:
        """该数据块包含的采样帧数。"""
        return int(self.samples.shape[0]) if self.samples.ndim else 0


# 供类型提示使用的别名，避免下游重复 import numpy。
NDArray = Any
