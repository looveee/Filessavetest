# -*- coding: utf-8 -*-
"""
event_replayer.py
================================================================================
FPS 多模态态势感知系统 —— 时间轴完美回放器 (Event Replayer)

读取 EventLogger 录制的 .jsonl，按原始时间间距把事件重新 publish 回 EventBus，
让雷达看板/下游模块完美重现案发现场。

精度要点
--------
- **绝对时间调度**：以"录制基准 ts0 + (ts_i - ts0)/speed"对齐到墙钟目标时刻，
  逐帧 sleep 到目标点而非累加每步 delta，从根本上消除累积漂移。
- **可中断 sleep**：用 `Event.wait(dt)` 取代 `time.sleep`，stop() 可即时打断。
- **变速回放**：speed_multiplier 支持 0.5x 慢放调试 / 2.0x 快进。
================================================================================
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from event_bus import EventBus, Topic

try:
    from cv_engine import CVUpdate
    from audio_engine import AudioTick
    from fusion_engine import FusionResult
except ImportError:  # pragma: no cover
    CVUpdate = AudioTick = FusionResult = None  # type: ignore

_LOGGER: logging.Logger = logging.getLogger("event_replayer")


@dataclasses.dataclass
class _Entry:
    """一条已解析的回放记录。"""
    topic: Topic
    ts: float
    data: Dict[str, Any]


class EventReplayer:
    """读取 .jsonl 并按原始时序把事件重放回总线。"""

    def __init__(
        self,
        filepath: str | Path,
        event_bus: Optional[EventBus] = None,
        speed_multiplier: float = 1.0,
        on_complete: Optional[Callable[[], None]] = None,
        loop: bool = False,
    ) -> None:
        """
        :param filepath: 录制的 .jsonl 路径。
        :param event_bus: 目标总线（默认取单例）。
        :param speed_multiplier: 回放速度倍率（>1 快放，<1 慢放）。
        :param on_complete: 回放结束（非循环）时的回调。
        :param loop: 是否循环回放。
        """
        self._path = Path(filepath)
        self._bus: EventBus = event_bus or EventBus()
        self._speed: float = max(1e-3, float(speed_multiplier))
        self._on_complete = on_complete
        self._loop = loop

        self._entries: List[_Entry] = []
        self._thread: Optional[threading.Thread] = None
        self._stop_event: threading.Event = threading.Event()
        self._published: int = 0

    @property
    def published(self) -> int:
        """已重放发布的事件数。"""
        return self._published

    # -----------------------------------------------------------------
    # 解析
    # -----------------------------------------------------------------
    def load(self) -> int:
        """解析 .jsonl 到内存，返回有效记录数。非法行被跳过并告警。"""
        if not self._path.exists():
            raise FileNotFoundError(str(self._path))

        entries: List[_Entry] = []
        with self._path.open("r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    topic = Topic[obj["topic"]]
                    entries.append(_Entry(topic=topic, ts=float(obj["ts"]), data=obj["data"]))
                except (json.JSONDecodeError, KeyError, ValueError) as exc:
                    _LOGGER.warning("跳过非法回放行 #%d: %s", lineno, exc)
        # 按 ts 升序，确保时序单调（容忍录制端轻微乱序）。
        entries.sort(key=lambda e: e.ts)
        self._entries = entries
        _LOGGER.info("已加载 %d 条回放记录 <- %s", len(entries), self._path)
        return len(entries)

    # -----------------------------------------------------------------
    # 生命周期
    # -----------------------------------------------------------------
    def start(self) -> None:
        """启动后台回放线程（幂等）。若尚未加载则先 load()。"""
        if self._thread is not None and self._thread.is_alive():
            return
        if not self._entries:
            self.load()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="event-replayer", daemon=True)
        self._thread.start()
        _LOGGER.info("EventReplayer 开始回放（speed=%.2fx, loop=%s）。", self._speed, self._loop)

    def stop(self, timeout: float = 5.0) -> None:
        """通知回放线程退出并等待回收（幂等）。"""
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None

    def wait(self, timeout: Optional[float] = None) -> bool:
        """阻塞等待回放线程自然结束（用于一次性回放后退出）。"""
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    # -----------------------------------------------------------------
    # 回放主循环（绝对时间调度）
    # -----------------------------------------------------------------
    def _run(self) -> None:
        if not self._entries:
            _LOGGER.warning("无回放记录，直接结束。")
            self._fire_complete()
            return

        while not self._stop_event.is_set():
            ts0 = self._entries[0].ts
            real0 = time.monotonic()

            for entry in self._entries:
                if self._stop_event.is_set():
                    break
                # 目标墙钟时刻 = 实际起点 + 录制相对偏移 / 速度
                target = real0 + (entry.ts - ts0) / self._speed
                dt = target - time.monotonic()
                if dt > 0:
                    if self._stop_event.wait(timeout=dt):
                        break
                payload = self._reconstruct(entry)
                if payload is not None:
                    self._bus.publish(entry.topic, payload)
                    self._published += 1

            if not self._loop:
                break

        _LOGGER.info("EventReplayer 回放结束：共发布 %d 个事件。", self._published)
        self._fire_complete()

    def _fire_complete(self) -> None:
        if self._on_complete is not None and not self._stop_event.is_set():
            try:
                self._on_complete()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("on_complete 回调异常: %s", exc)

    # -----------------------------------------------------------------
    # 反序列化：dict -> 原始数据类
    # -----------------------------------------------------------------
    @staticmethod
    def _reconstruct(entry: _Entry) -> Any:
        topic, data = entry.topic, entry.data
        try:
            if topic is Topic.CV_UPDATE and CVUpdate is not None:
                return _build(CVUpdate, data)
            if topic is Topic.FUSION_RESULT and FusionResult is not None:
                return _build(FusionResult, data)
            if topic is Topic.AUDIO_TICK and AudioTick is not None:
                # 波形已在录制时剥离：以空数组(0, channels)重建纯元数据切片。
                channels = int(data.get("channels", 1))
                arr = np.zeros((0, max(1, channels)), dtype=np.float32)
                return AudioTick(
                    audio_array=arr,
                    timestamp=float(data.get("timestamp", 0.0)),
                    frame_id=int(data.get("frame_id", -1)),
                    rms=float(data.get("rms", 0.0)),
                    is_speech_start=bool(data.get("is_speech_start", False)),
                    is_speech_end=bool(data.get("is_speech_end", False)),
                )
        except (TypeError, ValueError) as exc:
            _LOGGER.warning("回放重建失败 topic=%s: %s", topic.name, exc)
        return None


def _build(cls: type, data: Dict[str, Any]) -> Any:
    """仅取 dataclass 声明的字段构造实例，忽略多余键，缺失键交由默认值。"""
    valid = {f.name for f in dataclasses.fields(cls)}
    kwargs = {k: v for k, v in data.items() if k in valid}
    return cls(**kwargs)
