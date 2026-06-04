# -*- coding: utf-8 -*-
"""
event_logger.py
================================================================================
FPS 多模态态势感知系统 —— 态势数据流录制器 (Event Logger / Recorder)

把 EventBus 的关键流以 JSON Lines (.jsonl) 持久化落盘，供离线复盘与可视化回放。

设计要点
--------
1. **写盘不阻塞总线**：订阅回调（运行在总线工作线程）仅做轻量序列化并入队即返回；
   一个独立的 writer 守护线程负责真正的磁盘 I/O。慢磁盘绝不会拖慢实时态势分发。
2. **剥离音频波形**：AUDIO_TICK 内含 numpy 原始波形，直接落盘体积巨大且无谓——
   复盘雷达只看态势不听声音，故只保留 rms / frame_id / is_speech_* 等时序对齐元数据。
3. **自描述行格式**：每行 `{"topic": ..., "ts": <捕获单调时刻>, "data": {...}}`，
   回放器据相邻行 ts 之差精确还原时间间距。
================================================================================
"""

from __future__ import annotations

import dataclasses
import json
import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from event_bus import EventBus, Topic

_LOGGER: logging.Logger = logging.getLogger("event_logger")

# 录制订阅的主题集合
_RECORDED_TOPICS = (Topic.CV_UPDATE, Topic.AUDIO_TICK, Topic.FUSION_RESULT)


class EventLogger:
    """订阅关键主题并将事件流异步追加写入 .jsonl 文件。"""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        output_dir: str | Path = "records",
        filename: Optional[str] = None,
        queue_maxsize: int = 4096,
    ) -> None:
        """
        :param event_bus: 事件总线（默认取单例）。
        :param output_dir: 录制文件目录（不存在则自动创建）。
        :param filename: 文件名；缺省按 session_YYYYMMDD_HHMM.jsonl 生成。
        :param queue_maxsize: 写队列上限；满时丢弃最旧行（背压，保活优先）。
        """
        self._bus: EventBus = event_bus or EventBus()
        self._dir = Path(output_dir)
        self._filename = filename or f"session_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
        self._path = self._dir / self._filename

        self._queue: "queue.Queue[Optional[str]]" = queue.Queue(maxsize=max(1, queue_maxsize))
        self._writer: Optional[threading.Thread] = None
        self._fh = None
        self._started: bool = False
        self._dropped: int = 0
        self._written: int = 0
        # 每主题一个回调实例，便于精确退订
        self._handlers: Dict[Topic, Any] = {
            topic: (lambda payload, t=topic: self._enqueue(t, payload))
            for topic in _RECORDED_TOPICS
        }

    @property
    def path(self) -> Path:
        """录制文件的完整路径。"""
        return self._path

    @property
    def written(self) -> int:
        """已落盘行数。"""
        return self._written

    # -----------------------------------------------------------------
    # 生命周期
    # -----------------------------------------------------------------
    def start(self) -> None:
        """打开文件、启动 writer 线程并订阅总线（幂等）。"""
        if self._started:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        self._fh = self._path.open("a", encoding="utf-8")
        self._writer = threading.Thread(target=self._writer_loop, name="event-logger", daemon=True)
        self._writer.start()
        self._started = True

        for topic, handler in self._handlers.items():
            self._bus.subscribe(topic, handler)
        _LOGGER.info("EventLogger 开始录制 -> %s", self._path)

    def stop(self) -> None:
        """注销订阅、排空队列并关闭文件（幂等）。"""
        if not self._started:
            return
        self._started = False
        for topic in _RECORDED_TOPICS:
            self._bus.unsubscribe(topic, self._handlers[topic])

        self._queue.put(None)  # 哨兵，通知 writer 收尾
        if self._writer is not None:
            self._writer.join(timeout=5.0)
        if self._fh is not None:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:  # noqa: BLE001
                pass
        _LOGGER.info(
            "EventLogger 已停止：写入 %d 行（丢弃 %d 行）-> %s",
            self._written,
            self._dropped,
            self._path,
        )

    # -----------------------------------------------------------------
    # 订阅回调（总线工作线程）—— 仅序列化 + 入队
    # -----------------------------------------------------------------
    def _enqueue(self, topic: Topic, payload: Any) -> None:
        try:
            data = self._payload_to_dict(topic, payload)
            line = json.dumps(
                {"topic": topic.name, "ts": time.monotonic(), "data": data},
                separators=(",", ":"),
                ensure_ascii=False,
            )
        except (TypeError, ValueError) as exc:
            _LOGGER.warning("录制序列化失败 topic=%s: %s", topic.name, exc)
            return
        try:
            self._queue.put_nowait(line)
        except queue.Full:
            # 背压：丢最旧，保最新
            self._dropped += 1
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(line)
            except (queue.Empty, queue.Full):
                pass

    # -----------------------------------------------------------------
    # 序列化：剥离 AUDIO_TICK 波形，仅留时序元数据
    # -----------------------------------------------------------------
    @staticmethod
    def _payload_to_dict(topic: Topic, payload: Any) -> Dict[str, Any]:
        if topic is Topic.AUDIO_TICK:
            arr = getattr(payload, "audio_array", None)
            n_frames, channels = 0, 1
            if isinstance(arr, np.ndarray) and arr.ndim == 2:
                n_frames, channels = int(arr.shape[0]), int(arr.shape[1])
            return {
                "timestamp": float(payload.timestamp),
                "frame_id": int(payload.frame_id),
                "rms": float(payload.rms),
                "is_speech_start": bool(payload.is_speech_start),
                "is_speech_end": bool(payload.is_speech_end),
                "n_frames": n_frames,
                "channels": channels,
            }
        # CV_UPDATE / FUSION_RESULT：纯标量 dataclass，直接 asdict 即 JSON 安全。
        if dataclasses.is_dataclass(payload):
            return {k: _json_scalar(v) for k, v in dataclasses.asdict(payload).items()}
        # 兜底：普通 dict
        if isinstance(payload, dict):
            return {k: _json_scalar(v) for k, v in payload.items()}
        raise TypeError(f"不支持的 payload 类型: {type(payload).__name__}")

    # -----------------------------------------------------------------
    # writer 线程
    # -----------------------------------------------------------------
    def _writer_loop(self) -> None:
        assert self._fh is not None
        while True:
            item = self._queue.get()
            if item is None:
                break
            try:
                self._fh.write(item)
                self._fh.write("\n")
                self._fh.flush()
                self._written += 1
            except Exception as exc:  # noqa: BLE001  磁盘异常不得杀死线程
                _LOGGER.error("录制写盘异常: %s", exc)


def _json_scalar(v: Any) -> Any:
    """把可能的 numpy 标量收敛为原生 JSON 类型。"""
    if isinstance(v, np.generic):
        return v.item()
    return v
