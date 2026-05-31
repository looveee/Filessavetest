# -*- coding: utf-8 -*-
"""
audio_engine.py
================================================================================
FPS 多模态态势感知系统 —— 音频捕获引擎 (Audio Engine)

职责
----
以 WASAPI 环回 (loopback) 方式无损抓取系统扬声器输出的双声道音频流，
在高优先级后台线程中做轻量 VAD（能量端点检测）前置过滤，
仅将"声音事件"期间的切片通过 EventBus 发布到 ``AUDIO_TICK`` 主题，
避免把系统底噪倾泻给下游融合模块。

与 CVEngine 对称的设计
----------------------
1. **明确生命周期**：``start() / stop()``，I/O 跑在独立守护线程，绝不阻塞他人。
2. **配置热响应**：向 ConfigManager 注册监听，``sample_rate / chunk_size /
   energy_threshold / vad_start_chunks / vad_silence_duration_ms`` 改动即时生效；
   采样参数变化时自动重开采集设备。
3. **依赖注入友好**：底层采集后端通过 ``recorder_factory`` 注入，
   无声卡 / CI 环境可用 mock 完整跑通逻辑。
4. **故障隔离**：采集或处理异常被捕获并上报 ``SYS_ERROR``，不拖垮引擎。

VAD 状态机（IDLE ⇄ ACTIVE）
---------------------------
- 每个 chunk 计算 RMS 能量；``rms >= energy_threshold`` 记为 active。
- IDLE：连续 ``vad_start_chunks`` 个 active chunk → 触发"事件开始"。
  借助 pre-roll 预缓冲，回补起音瞬间，避免裁掉事件开头。
- ACTIVE：逐 chunk 发布 ``AUDIO_TICK``；能量回落并持续 ``vad_silence_duration_ms``
  对应的 chunk 数 → 触发"事件结束"，回到 IDLE。
================================================================================
"""

from __future__ import annotations

import logging
import math
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, ContextManager, Iterator, List, Optional, Tuple

import numpy as np

from event_bus import EventBus, Topic

# soundcard 为平台相关依赖，延迟在使用处校验，便于无声卡环境单测。
try:
    import soundcard as sc  # type: ignore
except Exception:  # pragma: no cover - 依赖缺失/无音频子系统时友好降级
    sc = None  # type: ignore

try:
    from config_manager import AppConfig, ConfigManager
except ImportError:  # pragma: no cover
    AppConfig = Any  # type: ignore
    ConfigManager = None  # type: ignore

_LOGGER: logging.Logger = logging.getLogger("audio_engine")


# ===========================================================================
#  一、音频切片数据模型
# ===========================================================================
@dataclass(frozen=True, eq=False)
class AudioTick:
    """单个音频切片，作为 AUDIO_TICK 主题的标准 payload。

    注：含 np.ndarray 字段，故关闭自动 __eq__（数组比较会产生歧义真值）。
    """

    audio_array: np.ndarray   # 切片音频数据，形状 (frames, channels)，float32
    timestamp: float          # 抓取时刻 (time.monotonic, 秒)
    frame_id: int             # 单调递增的切片序号
    rms: float = 0.0          # 本切片 RMS 能量（便于下游快速分级）
    is_speech_start: bool = False  # 是否为某次声音事件的起始切片
    is_speech_end: bool = False    # 是否为某次声音事件的结束切片


# ===========================================================================
#  二、VAD 状态
# ===========================================================================
class _VadState(Enum):
    IDLE = auto()    # 静默：等待事件起音
    ACTIVE = auto()  # 活动：声音事件进行中


# 单个已捕获 chunk 的内部记录：(数据, 时间戳, 帧号, rms)
_ChunkRecord = Tuple[np.ndarray, float, int, float]

# 采集后端：给定 (sample_rate, chunk_size, channels)，返回一个产出 Recorder 的上下文管理器。
# Recorder 需提供 record(numframes) -> np.ndarray (frames, channels)。
RecorderFactory = Callable[[int, int, int], ContextManager[Any]]


# ===========================================================================
#  三、音频引擎
# ===========================================================================
class AudioEngine:
    """
    音频捕获引擎。后台线程环回采集 -> RMS/VAD 过滤 -> 发布 AUDIO_TICK。

    用法::

        engine = AudioEngine()
        engine.start()
        ...
        engine.stop()
    """

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        config_manager: Optional["ConfigManager"] = None,
        recorder_factory: Optional[RecorderFactory] = None,
    ) -> None:
        """
        :param event_bus: 事件总线（默认取单例）。
        :param config_manager: 配置中心（默认取单例）。
        :param recorder_factory: 采集后端注入；为 None 时使用内置 soundcard 环回实现。
                                 便于在无声卡 / CI 环境注入 mock。
        """
        self._bus: EventBus = event_bus or EventBus()
        self._cfg_mgr = config_manager or (ConfigManager() if ConfigManager else None)
        self._recorder_factory: RecorderFactory = (
            recorder_factory or self._default_recorder_factory
        )

        # ---- 运行期可变参数（受 _param_lock 保护，配置线程与采集线程并发读写）----
        self._param_lock: threading.Lock = threading.Lock()
        self._sample_rate: int = 48000
        self._chunk_size: int = 1024
        self._channels: int = 2
        self._energy_threshold: float = 0.015
        self._vad_start_chunks: int = 3
        self._vad_silence_chunks: int = 9  # 由静音时长换算而来
        self._params_dirty: bool = False    # 采样参数变更标志，触发采集设备重开

        # ---- VAD 状态（仅在采集线程内变更，无需加锁）----
        self._state: _VadState = _VadState.IDLE
        self._active_run: int = 0
        self._silence_run: int = 0
        self._preroll: List[_ChunkRecord] = []  # 起音预缓冲
        self._frame_id: int = 0

        # ---- 线程控制 ----
        self._thread: Optional[threading.Thread] = None
        self._stop_event: threading.Event = threading.Event()

        # 拉取初始参数并订阅热更新。
        if self._cfg_mgr is not None:
            self._apply_config(self._cfg_mgr.config)
            self._cfg_mgr.register_change_listener(self._on_config_changed)

        _LOGGER.info(
            "AudioEngine 初始化完成 (sr=%d, chunk=%d, ch=%d, thr=%.4f, start=%d, silence=%d chunks)。",
            self._sample_rate, self._chunk_size, self._channels,
            self._energy_threshold, self._vad_start_chunks, self._vad_silence_chunks,
        )

    # -----------------------------------------------------------------
    # 3.1 配置应用 / 热更新响应
    # -----------------------------------------------------------------
    def _apply_config(self, cfg: "AppConfig") -> None:
        """从配置快照提取音频参数并原子更新；采样相关变化置脏以触发重开设备。"""
        a = cfg.audio
        sample_rate = max(1, int(a.sample_rate))
        chunk_size = max(1, int(a.chunk_size))
        # chunk 时长(ms) -> 静音判定所需的 chunk 数（向上取整，至少 1）。
        chunk_ms = chunk_size / sample_rate * 1000.0
        silence_chunks = max(1, math.ceil(float(a.vad_silence_duration_ms) / chunk_ms))

        with self._param_lock:
            if (sample_rate != self._sample_rate
                    or chunk_size != self._chunk_size
                    or int(a.channels) != self._channels):
                self._params_dirty = True  # 采样参数变化，需重开采集设备
            self._sample_rate = sample_rate
            self._chunk_size = chunk_size
            self._channels = max(1, int(a.channels))
            self._energy_threshold = float(a.energy_threshold)
            self._vad_start_chunks = max(1, int(a.vad_start_chunks))
            self._vad_silence_chunks = silence_chunks

    def _on_config_changed(self, cfg: "AppConfig") -> None:
        """ConfigManager 热更新回调：实时刷新采集与 VAD 参数。"""
        self._apply_config(cfg)
        with self._param_lock:
            snap = (self._sample_rate, self._chunk_size, self._channels,
                    self._energy_threshold, self._vad_start_chunks, self._vad_silence_chunks)
        _LOGGER.info(
            "AudioEngine 已响应配置热更新 (sr=%d, chunk=%d, ch=%d, thr=%.4f, start=%d, silence=%d)。",
            *snap,
        )

    # -----------------------------------------------------------------
    # 3.2 生命周期
    # -----------------------------------------------------------------
    def start(self) -> None:
        """启动后台采集线程（幂等）。"""
        if self._thread is not None and self._thread.is_alive():
            _LOGGER.debug("AudioEngine 已在运行，忽略重复 start。")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run, name="audio-engine", daemon=True)
        self._thread.start()
        _LOGGER.info("AudioEngine 后台采集线程已启动。")

    def stop(self, timeout: float = 5.0) -> None:
        """通知采集线程退出并等待回收（幂等）。"""
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None
        _LOGGER.info("AudioEngine 已停止。")

    # -----------------------------------------------------------------
    # 3.3 主循环（采集线程）
    # -----------------------------------------------------------------
    def run(self) -> None:
        """采集主循环：高优先级线程持续环回采集并做 VAD 过滤，直至停止信号。"""
        self._boost_thread_priority()
        _LOGGER.info("AudioEngine 进入采集主循环。")

        # 外层循环负责（在采样参数变化时）重开采集设备。
        while not self._stop_event.is_set():
            with self._param_lock:
                sr, chunk, ch = self._sample_rate, self._chunk_size, self._channels
                self._params_dirty = False

            try:
                with self._recorder_factory(sr, chunk, ch) as recorder:
                    _LOGGER.info("采集设备已打开 (sr=%d, chunk=%d, ch=%d)。", sr, chunk, ch)
                    # 内层循环：持续读取 chunk，直到停止或采样参数变更需重开。
                    while not self._stop_event.is_set():
                        with self._param_lock:
                            dirty = self._params_dirty
                        if dirty:
                            _LOGGER.info("检测到采样参数变更，重开采集设备。")
                            break

                        data = recorder.record(numframes=chunk)
                        self._process_chunk(self._as_float32(data))
            except Exception as exc:  # noqa: BLE001  设备异常不得使线程静默死亡
                _LOGGER.exception("音频采集异常: %s", exc)
                self._bus.publish(
                    Topic.SYS_ERROR, {"source": "audio_engine", "error": repr(exc)}
                )
                # 短暂退避后重试重开，避免在硬件持续异常时空转刷屏。
                if self._stop_event.wait(timeout=0.5):
                    break

        _LOGGER.info("AudioEngine 采集主循环退出。")

    # -----------------------------------------------------------------
    # 3.4 VAD 状态机（核心）—— 仅在采集线程内调用，状态无锁
    # -----------------------------------------------------------------
    def _process_chunk(self, chunk: np.ndarray) -> None:
        """对单个 chunk 计算 RMS 并驱动 VAD 状态机，按需发布 AUDIO_TICK。"""
        self._frame_id += 1
        fid = self._frame_id
        ts = time.monotonic()
        rms = self._rms(chunk)

        with self._param_lock:
            threshold = self._energy_threshold
            start_n = self._vad_start_chunks
            silence_n = self._vad_silence_chunks

        active = rms >= threshold
        record: _ChunkRecord = (chunk, ts, fid, rms)

        if self._state is _VadState.IDLE:
            # 维护起音预缓冲：保留最近 start_n 个 chunk，以便事件开始时回补。
            self._preroll.append(record)
            if len(self._preroll) > start_n:
                self._preroll.pop(0)

            if active:
                self._active_run += 1
                if self._active_run >= start_n:
                    self._enter_active()
            else:
                self._active_run = 0  # 能量未达连续要求，计数清零

        else:  # _VadState.ACTIVE
            if active:
                self._silence_run = 0
                self._emit_tick(record, is_start=False, is_end=False)
            else:
                self._silence_run += 1
                if self._silence_run >= silence_n:
                    # 静音持续达标：本 chunk 作为事件结束切片发布，回到 IDLE。
                    self._emit_tick(record, is_start=False, is_end=True)
                    self._reset_vad()
                else:
                    # 静音尚未达标（hangover 期内），仍属事件，照常发布。
                    self._emit_tick(record, is_start=False, is_end=False)

    def _enter_active(self) -> None:
        """从 IDLE 进入 ACTIVE：冲刷 pre-roll 预缓冲，回补事件起音。"""
        self._state = _VadState.ACTIVE
        self._silence_run = 0
        buffered = self._preroll
        self._preroll = []
        for i, rec in enumerate(buffered):
            self._emit_tick(rec, is_start=(i == 0), is_end=False)
        _LOGGER.debug("VAD: 声音事件开始 (回补 %d 个 pre-roll chunk)。", len(buffered))

    def _reset_vad(self) -> None:
        """事件结束：重置状态机回到 IDLE。"""
        self._state = _VadState.IDLE
        self._active_run = 0
        self._silence_run = 0
        self._preroll = []
        _LOGGER.debug("VAD: 声音事件结束，回到 IDLE。")

    def _emit_tick(self, record: _ChunkRecord, is_start: bool, is_end: bool) -> None:
        """打包 AudioTick 并发布到 AUDIO_TICK 主题。"""
        chunk, ts, fid, rms = record
        tick = AudioTick(
            audio_array=chunk,
            timestamp=ts,
            frame_id=fid,
            rms=rms,
            is_speech_start=is_start,
            is_speech_end=is_end,
        )
        self._bus.publish(Topic.AUDIO_TICK, tick)

    # -----------------------------------------------------------------
    # 3.5 工具方法
    # -----------------------------------------------------------------
    @staticmethod
    def _rms(chunk: np.ndarray) -> float:
        """计算 chunk 的 RMS（均方根）能量；空数组返回 0。"""
        if chunk is None or chunk.size == 0:
            return 0.0
        # 以 float64 计算避免溢出；对多声道整体求 RMS。
        x = chunk.astype(np.float64, copy=False)
        return float(np.sqrt(np.mean(np.square(x))))

    @staticmethod
    def _as_float32(data: np.ndarray) -> np.ndarray:
        """规整采集数据为 float32 的二维数组 (frames, channels)。"""
        arr = np.asarray(data, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr.reshape(-1, 1)
        return arr

    @staticmethod
    def _boost_thread_priority() -> None:
        """尽力提升当前采集线程优先级（跨平台 best-effort，失败静默）。"""
        try:
            import os
            if hasattr(os, "sched_getparam") and hasattr(os, "SCHED_RR"):
                # POSIX：尝试实时轮转调度（通常需要权限，失败则忽略）。
                param = os.sched_param(min(10, os.sched_get_priority_max(os.SCHED_RR)))  # type: ignore[attr-defined]
                os.sched_setscheduler(0, os.SCHED_RR, param)  # type: ignore[attr-defined]
                _LOGGER.debug("采集线程已设为实时调度 (SCHED_RR)。")
        except Exception:  # noqa: BLE001  无权限或不支持时降级，不影响功能
            _LOGGER.debug("提升采集线程优先级失败（权限/平台不支持），按普通优先级运行。")

    # -----------------------------------------------------------------
    # 3.6 默认 soundcard 环回采集后端
    # -----------------------------------------------------------------
    @staticmethod
    @contextmanager
    def _default_recorder_factory(
        sample_rate: int, chunk_size: int, channels: int
    ) -> Iterator[Any]:
        """基于 soundcard 的 WASAPI 环回采集上下文。

        取默认扬声器对应的 loopback 麦克风，以指定参数开录。
        """
        if sc is None:
            raise RuntimeError("soundcard 未安装，无法进行环回采集（可注入 recorder_factory 用于测试）")

        speaker = sc.default_speaker()
        # include_loopback=True 取得扬声器的环回采集设备（WASAPI loopback）。
        loopback = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        with loopback.recorder(
            samplerate=sample_rate, channels=channels, blocksize=chunk_size
        ) as recorder:
            yield recorder
