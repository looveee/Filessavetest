# -*- coding: utf-8 -*-
"""
cv_engine.py
================================================================================
FPS 多模态态势感知系统 —— 视觉捕获引擎 (CV Engine)

职责
----
以低延迟、稳定帧率从屏幕指定 ROI（小地图区域）抓取画面，做轻量预处理（灰度化），
解析出本机坐标 (X, Y, Z) 与朝向 (Heading)，并通过 EventBus 发布到 ``CV_UPDATE`` 主题。

关键设计
--------
1. **高性能抓取**：使用 ``mss``（基于系统原生截图 API，显著快于 PIL.ImageGrab）。
   ``mss`` 实例非线程安全，故在抓取线程内部创建、线程内独享。
2. **稳定帧率**：主循环依据 ``ConfigManager.cv.fps_limit`` 计算目标帧时长，
   循环尾部按"目标帧时长 - 实际耗时"做精确 sleep 补偿，避免帧率漂移。
3. **配置热响应**：向 ConfigManager 注册变更监听，运行中修改 fps_limit / roi_bbox
   可即时生效，无需重启引擎。
4. **解析钩子**：``_mock_ocr_and_parse`` 为占位实现，后续可无缝替换为真实 OCR / 模板匹配。
5. **容错**：抓取或解析异常被隔离并上报 ``SYS_ERROR``，单帧失败不影响后续循环。
================================================================================
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

import numpy as np

from event_bus import EventBus, Topic

# cv2 / mss 为重型/平台相关依赖，延迟在使用处校验，便于在无图形环境下做单元测试。
try:
    import cv2  # type: ignore
except ImportError:  # pragma: no cover - 依赖缺失时的友好降级
    cv2 = None  # type: ignore

try:
    import mss  # type: ignore
except ImportError:  # pragma: no cover
    mss = None  # type: ignore

# 与 ConfigManager 解耦：仅类型提示需要，运行期通过单例获取。
try:
    from config_manager import AppConfig, ConfigManager
except ImportError:  # pragma: no cover
    AppConfig = Any  # type: ignore
    ConfigManager = None  # type: ignore

_LOGGER: logging.Logger = logging.getLogger("cv_engine")


# ===========================================================================
#  一、视觉更新数据模型
# ===========================================================================
@dataclass(frozen=True)
class CVUpdate:
    """单帧视觉解析结果，作为 CV_UPDATE 主题的标准 payload。"""

    x: float                 # 本机世界坐标 X (米)
    y: float                 # 本机世界坐标 Y (米)
    z: float                 # 本机世界坐标 Z (米)
    heading: float           # 朝向角 (度, 0~360)
    frame_id: int            # 单调递增的帧序号
    timestamp: float         # 抓取时刻 (time.monotonic, 秒)
    confidence: float = 1.0  # 解析置信度 (0.0~1.0)


# ===========================================================================
#  二、ROI 抓取区域
# ===========================================================================
@dataclass
class _Region:
    """mss 抓取所需的屏幕区域（左上角偏移 + 宽高）。"""
    left: int
    top: int
    width: int
    height: int

    def as_mss_monitor(self) -> Dict[str, int]:
        """转换为 mss.grab() 所需的 monitor 字典。"""
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}


# 抓取函数类型：给定区域返回 BGRA/HxWxC 的 numpy 数组（便于测试注入 mock）。
GrabFn = Callable[[_Region], np.ndarray]


# ===========================================================================
#  三、视觉引擎
# ===========================================================================
class CVEngine:
    """
    视觉捕获引擎。后台线程稳定循环抓取 ROI -> 灰度化 -> 解析 -> 发布 CV_UPDATE。

    用法::

        engine = CVEngine()
        engine.start()
        ...
        engine.stop()
    """

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        config_manager: Optional["ConfigManager"] = None,
        grab_fn: Optional[GrabFn] = None,
    ) -> None:
        """
        :param event_bus: 事件总线（默认取单例）。
        :param config_manager: 配置中心（默认取单例）。
        :param grab_fn: 可选的抓取函数注入，便于无显示环境下做单元测试；
                        为 None 时使用内置的 mss 抓取实现。
        """
        self._bus: EventBus = event_bus or EventBus()
        self._cfg_mgr = config_manager or (ConfigManager() if ConfigManager else None)
        self._grab_fn: Optional[GrabFn] = grab_fn

        # 运行期可变参数，受 _param_lock 保护（配置热更新线程与抓取线程并发读写）。
        self._param_lock: threading.Lock = threading.Lock()
        self._fps_limit: int = 30
        self._region: _Region = _Region(left=0, top=0, width=320, height=320)

        # 线程控制
        self._thread: Optional[threading.Thread] = None
        self._stop_event: threading.Event = threading.Event()
        self._frame_id: int = 0

        # 从配置中心拉取初始参数，并订阅后续热更新。
        if self._cfg_mgr is not None:
            self._apply_config(self._cfg_mgr.config)
            self._cfg_mgr.register_change_listener(self._on_config_changed)

        _LOGGER.info(
            "CVEngine 初始化完成 (fps_limit=%d, roi=%s)。", self._fps_limit, self._region
        )

    # -----------------------------------------------------------------
    # 3.1 配置应用 / 热更新响应
    # -----------------------------------------------------------------
    def _apply_config(self, cfg: "AppConfig") -> None:
        """从配置快照提取 fps_limit 与 roi_bbox 并原子更新运行期参数。"""
        roi = cfg.cv.roi_bbox
        with self._param_lock:
            self._fps_limit = max(1, int(cfg.cv.fps_limit))  # 下限 1，避免除零
            self._region = _Region(left=int(roi.x), top=int(roi.y),
                                    width=int(roi.w), height=int(roi.h))

    def _on_config_changed(self, cfg: "AppConfig") -> None:
        """ConfigManager 热更新回调：实时刷新抓取参数。"""
        self._apply_config(cfg)
        with self._param_lock:
            fps, region = self._fps_limit, self._region
        _LOGGER.info("CVEngine 已响应配置热更新 (fps_limit=%d, roi=%s)。", fps, region)

    # -----------------------------------------------------------------
    # 3.2 生命周期
    # -----------------------------------------------------------------
    def start(self) -> None:
        """启动后台抓取线程（幂等）。"""
        if self._thread is not None and self._thread.is_alive():
            _LOGGER.debug("CVEngine 已在运行，忽略重复 start。")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run, name="cv-engine", daemon=True)
        self._thread.start()
        _LOGGER.info("CVEngine 后台抓取线程已启动。")

    def stop(self, timeout: float = 5.0) -> None:
        """通知抓取线程退出并等待回收（幂等）。"""
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None
        _LOGGER.info("CVEngine 已停止。")

    # -----------------------------------------------------------------
    # 3.3 主循环
    # -----------------------------------------------------------------
    def run(self) -> None:
        """抓取主循环：在独立线程中以稳定帧率运行，直至收到停止信号。"""
        grab = self._grab_fn or self._build_mss_grab()
        if grab is None:
            _LOGGER.error("无可用的抓取后端（mss 未安装且未注入 grab_fn），CVEngine 退出。")
            self._bus.publish(Topic.SYS_ERROR, {"source": "cv_engine", "error": "no grab backend"})
            return

        _LOGGER.info("CVEngine 进入主循环。")
        while not self._stop_event.is_set():
            loop_start = time.monotonic()

            with self._param_lock:
                fps_limit, region = self._fps_limit, self._region
            target_dt = 1.0 / fps_limit

            try:
                frame = grab(region)                     # ① 抓取 ROI（BGRA）
                gray = self._to_gray(frame)              # ② 预处理：灰度化
                update = self._mock_ocr_and_parse(gray)  # ③ 解析（当前为 mock 钩子）
                self._bus.publish(Topic.CV_UPDATE, update)  # ④ 发布事件
            except Exception as exc:  # noqa: BLE001  单帧失败不得中断主循环
                _LOGGER.exception("CVEngine 单帧处理异常: %s", exc)
                self._bus.publish(
                    Topic.SYS_ERROR, {"source": "cv_engine", "error": repr(exc)}
                )

            # ⑤ 精确 sleep 补偿：扣除本帧实际耗时，稳定帧率、降低漂移。
            elapsed = time.monotonic() - loop_start
            remaining = target_dt - elapsed
            if remaining > 0:
                # 用带超时的 wait 替代 sleep，使 stop() 能即时打断等待。
                self._stop_event.wait(timeout=remaining)
            else:
                _LOGGER.debug("帧处理超时 %.2fms，未达目标帧率。", -remaining * 1000)

        _LOGGER.info("CVEngine 主循环退出。")

    # -----------------------------------------------------------------
    # 3.4 抓取后端
    # -----------------------------------------------------------------
    def _build_mss_grab(self) -> Optional[GrabFn]:
        """构造基于 mss 的抓取函数；mss 实例在抓取线程内惰性创建并复用。"""
        if mss is None:
            return None

        thread_local = threading.local()

        def _grab(region: _Region) -> np.ndarray:
            sct = getattr(thread_local, "sct", None)
            if sct is None:
                # mss 实例非线程安全，绑定到当前抓取线程。
                sct = mss.mss()
                thread_local.sct = sct
            shot = sct.grab(region.as_mss_monitor())
            # mss 返回 BGRA 像素，转为 numpy 数组 (H, W, 4)。
            return np.asarray(shot, dtype=np.uint8)

        return _grab

    # -----------------------------------------------------------------
    # 3.5 预处理与解析
    # -----------------------------------------------------------------
    @staticmethod
    def _to_gray(frame: np.ndarray) -> np.ndarray:
        """将抓取帧转为灰度图。优先用 cv2，缺失时回退到 numpy 加权。"""
        if frame.ndim == 2:
            return frame  # 已是灰度
        if cv2 is not None:
            code = cv2.COLOR_BGRA2GRAY if frame.shape[2] == 4 else cv2.COLOR_BGR2GRAY
            return cv2.cvtColor(frame, code)
        # 回退：BT.601 加权（取前三通道 B,G,R）。
        b, g, r = frame[..., 0], frame[..., 1], frame[..., 2]
        return (0.114 * b + 0.587 * g + 0.299 * r).astype(np.uint8)

    def _mock_ocr_and_parse(self, image_array: np.ndarray) -> CVUpdate:
        """
        【占位钩子】模拟从灰度小地图解析坐标与朝向。

        当前返回随幻数据，后续将替换为真实 OCR / 模板匹配 / 关键点检测。
        保留 image_array 入参以固化未来真实实现的方法签名。

        :param image_array: 预处理后的灰度 ROI 图像。
        :return: 填充了假数据的 CVUpdate。
        """
        # 用图像内容派生一个伪随机但确定的扰动，让 mock 数据"看起来在动"。
        seed = int(image_array.mean() * 1000) if image_array.size else 0
        rng = np.random.default_rng(seed + self._frame_id)
        self._frame_id += 1

        return CVUpdate(
            x=float(rng.uniform(0.0, 1000.0)),
            y=float(rng.uniform(0.0, 1000.0)),
            z=float(rng.uniform(0.0, 50.0)),
            heading=float(rng.uniform(0.0, 360.0)),
            frame_id=self._frame_id,
            timestamp=time.monotonic(),
            confidence=0.99,
        )
