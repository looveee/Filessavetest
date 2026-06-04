# -*- coding: utf-8 -*-
"""
fusion_engine.py
================================================================================
FPS 多模态态势感知系统 —— 多模态时空融合引擎 (FusionCore)

职责
----
在**绝对世界坐标系**下，把两路异步模态缝合成对敌人的 3D 定位：
- 视觉 (CV_UPDATE)：本机绝对坐标 (X,Y,Z) 与朝向 heading（高频 ~30FPS）。
- 听觉 (AUDIO_TICK)：双声道音频切片流（事件驱动，频率不定）。

数据流水线
----------
1. **时间轴对齐**：实时缓存最新 CV 状态 `_latest_cv_state`；累积一次完整音频事件
   （从首个 tick 到 `is_speech_end`），事件闭合时取当时的 CV 快照与音频片段"缝合"。
2. **声源相对测角 (GCC-PHAT)**：对拼接后的双声道片段做广义互相关-相位变换，
   估计声道间时延 (TDE)，结合麦克风间距换算相对夹角 θ_rel。
3. **射线投射 (Raycasting)**：世界发射角 θ_world = heading + θ_rel，从本机坐标
   沿该方向发射射线，与 `MockMapCollider` 求交，得敌人世界坐标 E_world。
4. **事件抛出**：打包 `FusionResult` 发布到 `FUSION_RESULT`（低于 min_confidence 丢弃）。

并发模型
--------
EventBus 串行分发事件，但回调运行在线程池工作线程上；本引擎仍以 `RLock` 保护
`_latest_cv_state` 与音频累积缓冲，做到不依赖总线内部实现的自洽线程安全。
================================================================================
"""

from __future__ import annotations

import logging
import math
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import numpy as np

from event_bus import EventBus, Topic

# 仅取数据类用于类型提示与字段访问；这些模块的重型依赖已各自做了导入降级。
try:
    from cv_engine import CVUpdate
    from audio_engine import AudioTick
    from config_manager import AppConfig, ConfigManager
except ImportError:  # pragma: no cover
    CVUpdate = Any        # type: ignore
    AudioTick = Any       # type: ignore
    AppConfig = Any       # type: ignore
    ConfigManager = None  # type: ignore

_LOGGER: logging.Logger = logging.getLogger("fusion_engine")
_EPS: float = 1e-12


# ===========================================================================
#  一、融合结果数据模型
# ===========================================================================
@dataclass(frozen=True)
class FusionResult:
    """一次融合推导出的敌人世界定位，作为 FUSION_RESULT 主题的标准 payload。"""

    enemy_x: float                 # 敌人世界坐标 X (米)
    enemy_y: float                 # 敌人世界坐标 Y (米)
    enemy_z: float                 # 敌人世界坐标 Z (米)
    confidence: float              # 融合置信度 (0~1)
    material_type: str = "mock"    # 命中材质类型（Mock 阶段固定 'mock'）
    # --- 诊断信息（便于下游/回放分析）---
    theta_rel_deg: float = 0.0     # 声源相对本机朝向的夹角 (度)
    theta_world_deg: float = 0.0   # 声源世界绝对发射角 (度)
    tde_seconds: float = 0.0       # 估计的声道间时延 (秒)
    range_m: float = 0.0           # 命中距离 (米)
    source_frame_id: int = -1      # 触发本次融合的音频事件结束帧号
    timestamp: float = 0.0         # 音频事件结束时刻 (monotonic, 秒)


# ===========================================================================
#  二、Mock 3D 碰撞体接口
# ===========================================================================
@dataclass(frozen=True)
class CollisionHit:
    """射线与碰撞体的交点。"""
    point: np.ndarray      # 交点世界坐标 (3,)
    distance: float        # 沿射线的命中距离 (米)
    material_type: str     # 命中材质


class MockMapCollider(ABC):
    """3D 地图碰撞体抽象接口。后续可由真实点云/网格碰撞实现无缝替换。"""

    @abstractmethod
    def raycast(self, origin: np.ndarray, direction_unit: np.ndarray) -> Optional[CollisionHit]:
        """从 origin 沿单位方向 direction_unit 投射射线，返回首个交点或 None。"""
        raise NotImplementedError


class FixedDistanceWallCollider(MockMapCollider):
    """最简 Mock：假设在射线前方固定距离 R 处有一堵墙，必定命中。"""

    def __init__(self, distance: float = 20.0, material_type: str = "mock") -> None:
        self._distance = float(distance)
        self._material = material_type

    def raycast(self, origin: np.ndarray, direction_unit: np.ndarray) -> Optional[CollisionHit]:
        point = origin + self._distance * direction_unit
        return CollisionHit(point=point, distance=self._distance, material_type=self._material)


# ===========================================================================
#  三、声源测角处理器 (GCC-PHAT)
# ===========================================================================
class AudioProcessor:
    """基于 GCC-PHAT 的双声道声源相对测角器。

    参数运行期可变（随配置热更新刷新），全部为标量，赋值具原子性。
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        mic_distance_m: float = 0.18,
        speed_of_sound_mps: float = 343.0,
        band_lowcut_hz: float = 300.0,
        band_highcut_hz: float = 3500.0,
        interp: int = 4,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.mic_distance_m = float(mic_distance_m)
        self.speed_of_sound_mps = float(speed_of_sound_mps)
        self.band_lowcut_hz = float(band_lowcut_hz)
        self.band_highcut_hz = float(band_highcut_hz)
        self.interp = max(1, int(interp))

    # -----------------------------------------------------------------
    def estimate(self, audio: np.ndarray) -> Tuple[float, float, float]:
        """对双声道音频片段估计 (TDE 秒, 相对角 弧度, 置信度 0~1)。

        约定：sig=左声道(ch0), ref=右声道(ch1)。正的 θ_rel 表示声源偏向 +X 一侧
        （即朝向的右手方向），符号随 TDE 正负翻转。单声道或空片段返回零角、零置信。
        """
        if audio is None or audio.ndim != 2 or audio.shape[1] < 2 or audio.shape[0] < 2:
            return 0.0, 0.0, 0.0

        left = audio[:, 0].astype(np.float64, copy=False)
        right = audio[:, 1].astype(np.float64, copy=False)

        # 物理上可能的最大时延（声程差不超过麦克间距），用于约束搜索窗口。
        max_tau = self.mic_distance_m / max(self.speed_of_sound_mps, _EPS)
        tde = self._gcc_phat(left, right, max_tau)

        # TDE -> 相对角：sinθ = (c·τ)/d，限幅到 [-1,1] 防 arcsin 越界。
        sin_theta = (self.speed_of_sound_mps * tde) / max(self.mic_distance_m, _EPS)
        sin_theta = float(np.clip(sin_theta, -1.0, 1.0))
        theta_rel = math.asin(sin_theta)

        # 置信度：在估计时延处对齐后的时域归一化互相关系数 (0~1)。
        # 该指标有界、物理意义清晰，且不随带宽/片段长度漂移——远比"峰值显著度"稳健。
        confidence = self._alignment_confidence(left, right, tde)
        return tde, theta_rel, confidence

    @staticmethod
    def _normalized_corr(a: np.ndarray, b: np.ndarray) -> float:
        """两段等长信号的 Pearson 归一化相关系数 (-1~1)；退化时返回 0。"""
        if a.size < 2 or b.size < 2:
            return 0.0
        a = a - a.mean()
        b = b - b.mean()
        denom = math.sqrt(float(np.dot(a, a)) * float(np.dot(b, b)))
        if denom < _EPS:
            return 0.0
        return float(np.dot(a, b) / denom)

    def _alignment_confidence(self, left: np.ndarray, right: np.ndarray, tde: float) -> float:
        """按估计时延对齐左右声道后求归一化互相关，作为测角置信度。

        对齐方向的正负由 GCC-PHAT 的符号约定决定，这里两个方向都试、取较大者，
        使置信度与符号约定解耦、稳健可靠。
        """
        n = left.size
        lag = min(abs(int(round(tde * self.sample_rate))), n - 1)
        if lag == 0:
            return float(np.clip(abs(self._normalized_corr(left, right)), 0.0, 1.0))
        c1 = self._normalized_corr(left[lag:], right[: n - lag])
        c2 = self._normalized_corr(left[: n - lag], right[lag:])
        return float(np.clip(max(abs(c1), abs(c2)), 0.0, 1.0))

    # -----------------------------------------------------------------
    def _gcc_phat(self, sig: np.ndarray, ref: np.ndarray, max_tau: float) -> float:
        """广义互相关-相位变换，返回 TDE (秒)。

        全程基于 rfft/irfft，复杂度 O(n log n)；在频域施加带通掩码以截断高低频底噪。
        """
        n = sig.size + ref.size  # 线性相关所需的零填充长度
        fs = self.sample_rate

        SIG = np.fft.rfft(sig, n=n)
        REF = np.fft.rfft(ref, n=n)
        R = SIG * np.conj(REF)  # 互功率谱

        # 频域带通掩码：仅保留 [lowcut, highcut]，抑制低频嗡鸣与高频嘶声。
        freqs = np.fft.rfftfreq(n, d=1.0 / fs)
        band = (freqs >= self.band_lowcut_hz) & (freqs <= self.band_highcut_hz)
        R = R * band

        # PHAT 加权：除以幅度只保留相位信息，对混响/幅度差更鲁棒。
        denom = np.abs(R)
        denom[denom < _EPS] = _EPS
        R_phat = R / denom

        interp = self.interp
        cc = np.fft.irfft(R_phat, n=interp * n)

        # 仅在物理可行的时延窗口内找峰，并兼容负时延（信号回绕拼接）。
        max_shift = int(interp * n // 2)
        if max_tau > 0:
            max_shift = min(int(interp * fs * max_tau), max_shift)
        max_shift = max(max_shift, 1)
        cc = np.concatenate((cc[-max_shift:], cc[: max_shift + 1]))

        cc_abs = np.abs(cc)
        idx = int(np.argmax(cc_abs))
        shift = idx - max_shift
        tde = shift / float(interp * fs)
        return tde


# ===========================================================================
#  四、融合引擎
# ===========================================================================
class FusionEngine:
    """多模态时空融合引擎。订阅 CV_UPDATE / AUDIO_TICK，产出 FUSION_RESULT。

    用法::

        fusion = FusionEngine()
        fusion.start()          # 注册订阅
        ...
        fusion.stop()           # 注销订阅
    """

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        config_manager: Optional["ConfigManager"] = None,
        collider: Optional[MockMapCollider] = None,
        audio_processor: Optional[AudioProcessor] = None,
    ) -> None:
        self._bus: EventBus = event_bus or EventBus()
        self._cfg_mgr = config_manager or (ConfigManager() if ConfigManager else None)

        self._lock: threading.RLock = threading.RLock()

        # ---- 时间轴对齐状态 ----
        self._latest_cv_state: Optional["CVUpdate"] = None
        self._audio_buffer: List[np.ndarray] = []  # 当前音频事件的 chunk 累积
        self._in_event: bool = False

        # ---- 融合参数（随配置热更新）----
        self._min_confidence: float = 0.85
        self._collider_distance: float = 20.0

        # ---- DSP 与碰撞后端 ----
        self._processor: AudioProcessor = audio_processor or AudioProcessor()
        self._collider: MockMapCollider = collider or FixedDistanceWallCollider(
            distance=self._collider_distance
        )
        self._external_collider: bool = collider is not None  # 注入的碰撞体不被配置覆盖
        self._loaded_model_path: str = ""   # 已加载的网格模型路径（用于"只加载一次"判定）

        self._started: bool = False

        if self._cfg_mgr is not None:
            self._apply_config(self._cfg_mgr.config)
            self._cfg_mgr.register_change_listener(self._on_config_changed)

        _LOGGER.info(
            "FusionEngine 初始化 (min_conf=%.2f, mic=%.3fm, c=%.1f, wall=%.1fm)。",
            self._min_confidence, self._processor.mic_distance_m,
            self._processor.speed_of_sound_mps, self._collider_distance,
        )

    # -----------------------------------------------------------------
    # 4.1 配置应用 / 热更新
    # -----------------------------------------------------------------
    def _apply_config(self, cfg: "AppConfig") -> None:
        """从配置快照刷新融合参数、DSP 参数与（非注入的）碰撞体。"""
        f = cfg.fusion
        wall_dist = float(f.collider_distance_m)
        model_path = str(getattr(f, "map_model_path", "") or "").strip()

        # 碰撞体决策（含可能较慢的网格加载）放在锁外，避免长时间持锁阻塞回调。
        new_collider = self._resolve_collider(model_path, wall_dist)

        with self._lock:
            self._min_confidence = float(f.min_confidence)
            self._collider_distance = wall_dist
            # 刷新 GCC-PHAT 处理器参数（采样率取自 audio 配置，保证与采集端一致）。
            p = self._processor
            p.sample_rate = int(cfg.audio.sample_rate)
            p.mic_distance_m = float(f.mic_distance_m)
            p.speed_of_sound_mps = float(f.speed_of_sound_mps)
            p.band_lowcut_hz = float(f.band_lowcut_hz)
            p.band_highcut_hz = float(f.band_highcut_hz)
            p.interp = max(1, int(f.gcc_interp))
            if new_collider is not None:
                self._collider = new_collider

    def _resolve_collider(self, model_path: str, wall_dist: float) -> Optional[MockMapCollider]:
        """决定是否需要替换碰撞体。返回新碰撞体，或 None 表示沿用当前。

        - 注入式碰撞体：永不被配置覆盖。
        - 配了模型路径：仅在路径变化时加载一次 TrimeshCollider；加载失败回退假想墙。
        - 未配模型：使用假想墙。
        """
        if self._external_collider:
            return None

        if model_path:
            # 已加载同一模型则复用，满足"只加载一次"。
            if model_path == self._loaded_model_path and isinstance(self._collider, MockMapCollider):
                from mesh_collider import TrimeshCollider  # 局部导入，避免循环依赖
                if isinstance(self._collider, TrimeshCollider):
                    return None
            try:
                from mesh_collider import TrimeshCollider
                collider = TrimeshCollider(model_path)
                self._loaded_model_path = model_path
                _LOGGER.info("FusionEngine 启用真实网格碰撞体: %s", model_path)
                return collider
            except Exception as exc:  # noqa: BLE001  加载失败不得致命，回退假想墙
                _LOGGER.error("加载地图网格失败 (%s)，回退假想墙: %s", model_path, exc)
                self._loaded_model_path = ""
                return FixedDistanceWallCollider(distance=wall_dist)

        # 未配模型：假想墙（保留旧行为）。
        self._loaded_model_path = ""
        return FixedDistanceWallCollider(distance=wall_dist)

    def _on_config_changed(self, cfg: "AppConfig") -> None:
        self._apply_config(cfg)
        _LOGGER.info("FusionEngine 已响应配置热更新 (min_conf=%.2f)。", self._min_confidence)

    # -----------------------------------------------------------------
    # 4.2 生命周期：订阅 / 注销
    # -----------------------------------------------------------------
    def start(self) -> None:
        """注册对 CV_UPDATE 与 AUDIO_TICK 的订阅（幂等）。"""
        with self._lock:
            if self._started:
                return
            self._started = True
        self._bus.subscribe(Topic.CV_UPDATE, self._on_cv_update)
        self._bus.subscribe(Topic.AUDIO_TICK, self._on_audio_tick)
        _LOGGER.info("FusionEngine 已订阅 CV_UPDATE / AUDIO_TICK。")

    def stop(self) -> None:
        """注销订阅（幂等）。"""
        with self._lock:
            if not self._started:
                return
            self._started = False
        self._bus.unsubscribe(Topic.CV_UPDATE, self._on_cv_update)
        self._bus.unsubscribe(Topic.AUDIO_TICK, self._on_audio_tick)
        _LOGGER.info("FusionEngine 已注销订阅。")

    # -----------------------------------------------------------------
    # 4.3 事件回调
    # -----------------------------------------------------------------
    def _on_cv_update(self, cv: "CVUpdate") -> None:
        """缓存最新视觉状态（本机绝对坐标 + 朝向）。"""
        with self._lock:
            self._latest_cv_state = cv

    def _on_audio_tick(self, tick: "AudioTick") -> None:
        """累积音频事件；事件闭合 (is_speech_end) 时触发一次融合。"""
        finalize_args: Optional[Tuple[np.ndarray, "AudioTick", "CVUpdate"]] = None

        with self._lock:
            # 非空音频块才入缓冲（哨兵结束帧为空数组，仅作控制边界）。
            arr = tick.audio_array
            if arr is not None and arr.ndim == 2 and arr.shape[0] > 0:
                self._audio_buffer.append(arr)
                self._in_event = True

            if tick.is_speech_end:
                cv_snapshot = self._latest_cv_state
                buffered = self._audio_buffer
                self._audio_buffer = []
                self._in_event = False

                if not buffered:
                    _LOGGER.debug("音频事件结束但无有效音频，跳过融合。")
                elif cv_snapshot is None:
                    _LOGGER.debug("音频事件结束但尚无 CV 状态，无法地理配准，跳过融合。")
                else:
                    stitched = np.concatenate(buffered, axis=0)
                    finalize_args = (stitched, tick, cv_snapshot)

        # 在锁外执行 DSP / 射线计算 / 发布，避免长时间持锁阻塞其他回调。
        if finalize_args is not None:
            self._fuse(*finalize_args)

    # -----------------------------------------------------------------
    # 4.4 融合核心：测角 -> 射线投射 -> 发布
    # -----------------------------------------------------------------
    def _fuse(self, audio: np.ndarray, end_tick: "AudioTick", cv: "CVUpdate") -> None:
        """缝合一次音频事件与 CV 快照，解算敌人世界坐标并发布。"""
        try:
            tde, theta_rel, audio_conf = self._processor.estimate(audio)

            # 世界绝对发射角 = 本机朝向 + 相对角（heading 单位为度）。
            heading_rad = math.radians(float(cv.heading))
            theta_world = heading_rad + theta_rel
            direction = np.array(
                [math.cos(theta_world), math.sin(theta_world), 0.0], dtype=np.float64
            )

            origin = np.array([float(cv.x), float(cv.y), float(cv.z)], dtype=np.float64)
            # 在锁内仅快照碰撞体引用，射线求交（可能较慢）放到锁外执行。
            with self._lock:
                collider = self._collider
            hit = collider.raycast(origin, direction)
            if hit is None:
                _LOGGER.debug("射线未命中任何碰撞体，跳过。")
                return

            # 融合置信度 = 视觉置信度 × 听觉置信度。
            cv_conf = float(getattr(cv, "confidence", 1.0))
            confidence = cv_conf * audio_conf

            with self._lock:
                min_conf = self._min_confidence

            if confidence < min_conf:
                _LOGGER.debug(
                    "融合置信度 %.3f < 阈值 %.3f，丢弃该结果。", confidence, min_conf
                )
                return

            result = FusionResult(
                enemy_x=float(hit.point[0]),
                enemy_y=float(hit.point[1]),
                enemy_z=float(hit.point[2]),
                confidence=confidence,
                material_type=hit.material_type,
                theta_rel_deg=math.degrees(theta_rel),
                theta_world_deg=math.degrees(theta_world),
                tde_seconds=tde,
                range_m=hit.distance,
                source_frame_id=int(getattr(end_tick, "frame_id", -1)),
                timestamp=float(getattr(end_tick, "timestamp", 0.0)),
            )
            self._bus.publish(Topic.FUSION_RESULT, result)
            _LOGGER.info(
                "融合命中 ✔ E=(%.2f, %.2f, %.2f) conf=%.3f θ_rel=%.1f° θ_world=%.1f°",
                result.enemy_x, result.enemy_y, result.enemy_z,
                confidence, result.theta_rel_deg, result.theta_world_deg,
            )
        except Exception as exc:  # noqa: BLE001  融合异常不得拖垮总线分发线程
            _LOGGER.exception("融合计算异常: %s", exc)
            self._bus.publish(
                Topic.SYS_ERROR, {"source": "fusion_engine", "error": repr(exc)}
            )
