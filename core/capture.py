"""捕获层（Capture Layer）。

职责单一：抓取屏幕与系统音频，打上高精度时间戳后投递到队列。
本层**不做任何重计算**，所有解析/编码/推理由下游消费者完成。

设计要点：
- Video / Audio 各自独立线程运行，互不阻塞。
- 统一使用 ``time.perf_counter_ns()`` 生成纳秒级时间戳。
- 通过 ``queue.Queue`` 与主线程解耦，线程安全。
- ``start()`` / ``stop()`` 优雅启停，``stop_event`` 可被外部安全中断。
- 单次捕获异常被隔离，仅记录日志，不会让线程崩溃。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Optional

import numpy as np

from core.types import RawAudioChunk, RawVideoFrame

logger = logging.getLogger(__name__)


class VideoCapture:
    """基于 mss 的屏幕区域捕获器。

    在独立线程中按 ``fps_limit`` 稳定抓取 ``roi`` 指定区域，
    每帧封装为 :class:`RawVideoFrame` 投递到 ``output_queue``。
    """

    def __init__(
        self,
        output_queue: "queue.Queue[RawVideoFrame]",
        roi: dict[str, int],
        fps_limit: int = 30,
        monitor: int = 1,
    ) -> None:
        if fps_limit <= 0:
            raise ValueError("fps_limit 必须为正整数")
        for key in ("left", "top", "width", "height"):
            if key not in roi:
                raise ValueError(f"roi 缺少必需字段: {key!r}")

        self.output_queue = output_queue
        self.roi = dict(roi)
        self.fps_limit = fps_limit
        self.monitor = monitor

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._frame_id = 0
        # mss 实例非线程安全，使用 thread-local 保证每线程独占一个。
        self._local = threading.local()

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """启动捕获线程（幂等：已运行则忽略）。"""
        if self._thread and self._thread.is_alive():
            logger.debug("VideoCapture 已在运行，忽略重复 start()")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="VideoCapture",
            daemon=True,
        )
        self._thread.start()
        logger.info("VideoCapture 已启动 (roi=%s, fps_limit=%d)", self.roi, self.fps_limit)

    def stop(self, timeout: float = 2.0) -> None:
        """请求停止并等待线程退出。"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("VideoCapture 线程在 %.1fs 内未退出", timeout)
        logger.info("VideoCapture 已停止")

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _get_sct(self):
        """获取当前线程独享的 mss 实例（延迟创建）。"""
        sct = getattr(self._local, "sct", None)
        if sct is None:
            import mss  # 延迟导入，避免无显示环境在模块加载时即报错

            sct = mss.mss()
            self._local.sct = sct
        return sct

    def _capture_loop(self) -> None:
        """捕获主循环，运行于独立线程。"""
        frame_interval = 1.0 / self.fps_limit
        region = {
            "left": self.roi["left"],
            "top": self.roi["top"],
            "width": self.roi["width"],
            "height": self.roi["height"],
        }

        try:
            sct = self._get_sct()
        except Exception:  # noqa: BLE001 - 启动失败需记录全栈
            logger.exception("VideoCapture 初始化 mss 失败，线程退出")
            return

        while not self._stop_event.is_set():
            start_time = time.perf_counter()

            try:
                # mss 返回的是 BGRA；保持原始格式交由下游决定是否转换。
                img = np.array(sct.grab(region))
                timestamp_ns = time.perf_counter_ns()
                self._frame_id += 1

                frame = RawVideoFrame(
                    image=img,
                    timestamp_ns=timestamp_ns,
                    frame_id=self._frame_id,
                    roi=region.copy(),
                )
                try:
                    self.output_queue.put_nowait(frame)
                except queue.Full:
                    # 队列满说明消费者跟不上，丢弃当前帧以保证实时性。
                    logger.debug("video_queue 已满，丢弃 frame_id=%d", self._frame_id)
            except Exception:  # noqa: BLE001 - 单帧失败不应中断循环
                logger.exception("VideoCapture 抓取单帧失败，跳过")

            # 精确帧率控制：用剩余时间做可中断的 wait。
            elapsed = time.perf_counter() - start_time
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                self._stop_event.wait(sleep_time)

        # 清理 thread-local 资源。
        sct = getattr(self._local, "sct", None)
        if sct is not None:
            try:
                sct.close()
            except Exception:  # noqa: BLE001
                logger.debug("关闭 mss 实例时出错", exc_info=True)
            self._local.sct = None


class AudioCapture:
    """基于 soundcard 的系统音频（loopback）捕获器。

    在独立线程中以 ``chunk_duration_ms`` 为粒度读取默认扬声器的
    loopback 流，归一化为 ``float32`` 后封装为 :class:`RawAudioChunk`
    投递到 ``output_queue``。
    """

    def __init__(
        self,
        output_queue: "queue.Queue[RawAudioChunk]",
        sample_rate: int = 48000,
        channels: int = 2,
        chunk_duration_ms: int = 20,
    ) -> None:
        if sample_rate <= 0 or channels <= 0 or chunk_duration_ms <= 0:
            raise ValueError("sample_rate / channels / chunk_duration_ms 必须为正")

        self.output_queue = output_queue
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_duration_ms = chunk_duration_ms
        # 每个数据块的采样帧数。
        self.chunk_frames = int(sample_rate * chunk_duration_ms / 1000)

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._chunk_id = 0

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """启动捕获线程（幂等）。"""
        if self._thread and self._thread.is_alive():
            logger.debug("AudioCapture 已在运行，忽略重复 start()")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="AudioCapture",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "AudioCapture 已启动 (sample_rate=%d, channels=%d, chunk=%dms)",
            self.sample_rate,
            self.channels,
            self.chunk_duration_ms,
        )

    def stop(self, timeout: float = 2.0) -> None:
        """请求停止并等待线程退出。"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("AudioCapture 线程在 %.1fs 内未退出", timeout)
        logger.info("AudioCapture 已停止")

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _open_loopback_mic(self):
        """获取默认扬声器的 loopback 录音设备。"""
        import soundcard as sc  # 延迟导入

        speaker = sc.default_speaker()
        # include_loopback=True 让我们能录制系统正在播放的声音。
        mic = sc.get_microphone(speaker.name, include_loopback=True)
        return mic

    def _capture_loop(self) -> None:
        """捕获主循环，运行于独立线程。"""
        try:
            mic = self._open_loopback_mic()
        except Exception:  # noqa: BLE001
            logger.exception("AudioCapture 初始化 loopback 设备失败，线程退出")
            return

        try:
            with mic.recorder(
                samplerate=self.sample_rate,
                channels=self.channels,
                blocksize=self.chunk_frames,
            ) as recorder:
                while not self._stop_event.is_set():
                    try:
                        # 阻塞读取固定大小的数据块。
                        data = recorder.record(numframes=self.chunk_frames)
                        timestamp_ns = time.perf_counter_ns()

                        # soundcard 已返回 float32；确保类型并裁剪到 [-1, 1]。
                        samples = np.asarray(data, dtype=np.float32)
                        np.clip(samples, -1.0, 1.0, out=samples)

                        self._chunk_id += 1
                        chunk = RawAudioChunk(
                            samples=samples,
                            timestamp_ns=timestamp_ns,
                            chunk_id=self._chunk_id,
                            sample_rate=self.sample_rate,
                            channels=self.channels,
                        )
                        try:
                            self.output_queue.put_nowait(chunk)
                        except queue.Full:
                            logger.debug(
                                "audio_queue 已满，丢弃 chunk_id=%d", self._chunk_id
                            )
                    except Exception:  # noqa: BLE001 - 单块失败不中断循环
                        logger.exception("AudioCapture 读取单块失败，跳过")
        except Exception:  # noqa: BLE001 - recorder 上下文创建失败
            logger.exception("AudioCapture recorder 运行异常，线程退出")
