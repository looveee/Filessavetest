# -*- coding: utf-8 -*-
"""
fusion_engine 的单元测试。

覆盖：
- GCC-PHAT 能从已知时延恢复 TDE，并满足声道互换时延符号翻转
- 零时延 -> 零相对角
- FixedDistanceWallCollider 射线投射几何正确
- 端到端：CV 状态 + 音频事件缝合 -> 解算敌人世界坐标 -> 发布 FUSION_RESULT
- 朝向偏移下的世界坐标解算（heading 旋转射线方向）
- 无 CV 状态时跳过；低置信度被 min_confidence 过滤
- 配置热重载即时刷新 DSP / 融合参数
"""

import math
import sys
import threading
from pathlib import Path
from typing import List

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config_manager as cm  # noqa: E402
from audio_engine import AudioTick  # noqa: E402
from cv_engine import CVUpdate  # noqa: E402
from event_bus import EventBus, Topic  # noqa: E402
from fusion_engine import (  # noqa: E402
    AudioProcessor,
    FixedDistanceWallCollider,
    FusionEngine,
    FusionResult,
)


# ---------------------------------------------------------------------------
# 夹具与工具
# ---------------------------------------------------------------------------
@pytest.fixture()
def bus():
    EventBus._reset_singleton()
    instance = EventBus(max_workers=4)
    yield instance
    instance.shutdown(wait=True)
    EventBus._reset_singleton()


def _make_config(**fusion_overrides) -> cm.AppConfig:
    cfg = cm.AppConfig()
    for k, v in fusion_overrides.items():
        setattr(cfg.fusion, k, v)
    return cfg


class _FakeConfigManager:
    def __init__(self, cfg: cm.AppConfig) -> None:
        self._cfg = cfg
        self._listeners: List = []

    @property
    def config(self) -> cm.AppConfig:
        return self._cfg

    def register_change_listener(self, fn) -> None:
        self._listeners.append(fn)

    def push(self, new_cfg: cm.AppConfig) -> None:
        self._cfg = new_cfg
        for fn in self._listeners:
            fn(new_cfg)


def _stereo(delay: int = 0, n: int = 4096, seed: int = 0) -> np.ndarray:
    """构造双声道片段：右声道相对左声道延迟 delay 个采样（负值为提前）。"""
    rng = np.random.default_rng(seed)
    left = rng.standard_normal(n).astype(np.float32)
    right = np.zeros(n, dtype=np.float32)
    if delay == 0:
        right[:] = left
    elif delay > 0:
        right[delay:] = left[: n - delay]
    else:
        d = -delay
        right[: n - d] = left[d:]
    return np.stack([left, right], axis=1)


def _cv(x=10.0, y=20.0, z=5.0, heading=0.0, confidence=1.0, fid=1) -> CVUpdate:
    return CVUpdate(x=x, y=y, z=z, heading=heading, frame_id=fid,
                    timestamp=0.0, confidence=confidence)


def _tick(audio: np.ndarray, is_end: bool, fid: int = 1) -> AudioTick:
    return AudioTick(audio_array=audio, timestamp=1.23, frame_id=fid,
                     rms=0.5, is_speech_start=not is_end, is_speech_end=is_end)


# 宽带处理器，使白噪声测试的相关峰更锐利、便于精确恢复时延。
def _wide_processor() -> AudioProcessor:
    return AudioProcessor(sample_rate=48000, mic_distance_m=0.18,
                          speed_of_sound_mps=343.0, band_lowcut_hz=50.0,
                          band_highcut_hz=20000.0, interp=4)


# ---------------------------------------------------------------------------
# 1. GCC-PHAT
# ---------------------------------------------------------------------------
def test_gcc_phat_recovers_known_delay():
    """已知 k 采样时延应被准确恢复，且置信度高。"""
    fs, k = 48000, 10
    proc = _wide_processor()
    audio = _stereo(delay=k, n=8192, seed=7)

    tde, theta, conf = proc.estimate(audio)

    assert abs(abs(tde) - k / fs) < 2.0 / fs, f"TDE 偏差过大: {tde}"
    expected_theta = math.asin(np.clip(343.0 * (k / fs) / 0.18, -1, 1))
    assert abs(abs(theta) - expected_theta) < math.radians(3.0)
    assert conf > 0.5, f"清晰时延信号置信度过低: {conf}"


def test_gcc_phat_sign_flips_on_channel_swap():
    """互换左右声道，估计时延符号应翻转（方位镜像）。"""
    proc = _wide_processor()
    a = _stereo(delay=12, n=8192, seed=3)
    a_swapped = a[:, ::-1].copy()

    tde_a, _, _ = proc.estimate(a)
    tde_b, _, _ = proc.estimate(a_swapped)

    assert tde_a != 0.0
    assert math.copysign(1, tde_a) != math.copysign(1, tde_b), "声道互换未翻转时延符号"
    assert abs(abs(tde_a) - abs(tde_b)) < 2.0 / 48000


def test_zero_delay_gives_zero_angle():
    """完全相同的双声道（零时延）应解出近零相对角，且高置信。"""
    proc = _wide_processor()
    tde, theta, conf = proc.estimate(_stereo(delay=0, n=8192, seed=1))
    assert abs(tde) < 1.0 / 48000
    assert abs(theta) < math.radians(1.0)
    assert conf > 0.5


def test_mono_audio_returns_zero():
    """单声道无法测角，应返回零角零置信。"""
    proc = _wide_processor()
    mono = np.ones((4096, 1), dtype=np.float32)
    tde, theta, conf = proc.estimate(mono)
    assert (tde, theta, conf) == (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# 2. 射线投射
# ---------------------------------------------------------------------------
def test_fixed_distance_collider_raycast():
    """固定墙碰撞体应在射线前方 distance 处返回交点。"""
    collider = FixedDistanceWallCollider(distance=20.0)
    origin = np.array([1.0, 2.0, 3.0])
    direction = np.array([1.0, 0.0, 0.0])
    hit = collider.raycast(origin, direction)
    assert hit is not None
    assert np.allclose(hit.point, [21.0, 2.0, 3.0])
    assert hit.distance == 20.0
    assert hit.material_type == "mock"


# ---------------------------------------------------------------------------
# 3. 端到端融合
# ---------------------------------------------------------------------------
def test_fusion_end_to_end_publishes_result(bus):
    """CV 状态 + 音频事件缝合 -> 解算敌人世界坐标 -> 发布 FUSION_RESULT。"""
    results: List[FusionResult] = []
    lock = threading.Lock()
    bus.subscribe(Topic.FUSION_RESULT, lambda r: (lock.acquire(), results.append(r), lock.release()))

    cfg = _make_config(collider_distance_m=20.0, min_confidence=0.85)
    fusion = FusionEngine(event_bus=bus, config_manager=_FakeConfigManager(cfg))
    fusion.start()

    # 朝向 0°（指向 +X），零时延音频 -> θ_rel≈0 -> 射线沿 +X
    bus.publish(Topic.CV_UPDATE, _cv(x=10.0, y=20.0, z=5.0, heading=0.0, confidence=1.0))
    bus.publish(Topic.AUDIO_TICK, _tick(_stereo(delay=0, n=8192, seed=2), is_end=True))
    assert bus.join(timeout=5.0)

    with lock:
        assert len(results) == 1, f"应恰好产出一个融合结果，实际 {len(results)}"
        r = results[0]
    # origin(10,20,5) 沿 +X 走 20m -> (30,20,5)
    assert r.enemy_x == pytest.approx(30.0, abs=0.5)
    assert r.enemy_y == pytest.approx(20.0, abs=0.5)
    assert r.enemy_z == pytest.approx(5.0, abs=1e-6)
    assert r.material_type == "mock"
    assert r.confidence >= 0.85
    assert r.range_m == pytest.approx(20.0)

    fusion.stop()


def test_fusion_respects_heading_rotation(bus):
    """朝向 90° 时，射线应旋转到 +Y 方向。"""
    results: List[FusionResult] = []
    lock = threading.Lock()
    bus.subscribe(Topic.FUSION_RESULT, lambda r: (lock.acquire(), results.append(r), lock.release()))

    cfg = _make_config(collider_distance_m=20.0, min_confidence=0.85)
    fusion = FusionEngine(event_bus=bus, config_manager=_FakeConfigManager(cfg))
    fusion.start()

    bus.publish(Topic.CV_UPDATE, _cv(x=10.0, y=20.0, z=5.0, heading=90.0, confidence=1.0))
    bus.publish(Topic.AUDIO_TICK, _tick(_stereo(delay=0, n=8192, seed=4), is_end=True))
    assert bus.join(timeout=5.0)

    with lock:
        assert len(results) == 1
        r = results[0]
    # 朝向 +Y：origin(10,20,5) 走 20m -> (10,40,5)
    assert r.enemy_x == pytest.approx(10.0, abs=0.5)
    assert r.enemy_y == pytest.approx(40.0, abs=0.5)
    fusion.stop()


def test_no_cv_state_skips_fusion(bus):
    """尚无 CV 状态时，音频事件不应产出融合结果。"""
    results: List[FusionResult] = []
    bus.subscribe(Topic.FUSION_RESULT, lambda r: results.append(r))

    fusion = FusionEngine(event_bus=bus, config_manager=_FakeConfigManager(_make_config()))
    fusion.start()

    bus.publish(Topic.AUDIO_TICK, _tick(_stereo(delay=5, n=4096), is_end=True))
    assert bus.join(timeout=3.0)

    assert results == [], "无 CV 状态时不应融合"
    fusion.stop()


def test_low_confidence_is_filtered(bus):
    """融合置信度低于 min_confidence 应被丢弃（这里靠低 CV 置信度压低乘积）。"""
    results: List[FusionResult] = []
    bus.subscribe(Topic.FUSION_RESULT, lambda r: results.append(r))

    # 即便音频置信度≈1，cv_conf=0.5 -> 乘积≈0.5 < 0.99 阈值
    cfg = _make_config(min_confidence=0.99)
    fusion = FusionEngine(event_bus=bus, config_manager=_FakeConfigManager(cfg))
    fusion.start()

    bus.publish(Topic.CV_UPDATE, _cv(confidence=0.5))
    bus.publish(Topic.AUDIO_TICK, _tick(_stereo(delay=0, n=8192, seed=9), is_end=True))
    assert bus.join(timeout=3.0)

    assert results == [], "低置信度结果应被 min_confidence 过滤"
    fusion.stop()


def test_empty_event_no_audio_skips(bus):
    """只有空哨兵结束帧（无有效音频）时应跳过融合。"""
    results: List[FusionResult] = []
    bus.subscribe(Topic.FUSION_RESULT, lambda r: results.append(r))

    fusion = FusionEngine(event_bus=bus, config_manager=_FakeConfigManager(_make_config()))
    fusion.start()

    bus.publish(Topic.CV_UPDATE, _cv())
    # 空音频哨兵（0 帧）+ is_end
    empty = np.zeros((0, 2), dtype=np.float32)
    bus.publish(Topic.AUDIO_TICK, _tick(empty, is_end=True))
    assert bus.join(timeout=3.0)

    assert results == [], "无有效音频不应融合"
    fusion.stop()


# ---------------------------------------------------------------------------
# 4. 配置热重载
# ---------------------------------------------------------------------------
def test_hot_reload_updates_fusion_params(bus):
    """热重载应即时刷新 DSP 与融合参数。"""
    fake = _FakeConfigManager(_make_config(
        min_confidence=0.85, mic_distance_m=0.18, collider_distance_m=20.0
    ))
    fusion = FusionEngine(event_bus=bus, config_manager=fake)
    assert fusion._min_confidence == 0.85
    assert fusion._processor.mic_distance_m == pytest.approx(0.18)

    fake.push(_make_config(
        min_confidence=0.5, mic_distance_m=0.25, collider_distance_m=50.0,
        speed_of_sound_mps=340.0,
    ))
    assert fusion._min_confidence == 0.5
    assert fusion._processor.mic_distance_m == pytest.approx(0.25)
    assert fusion._processor.speed_of_sound_mps == pytest.approx(340.0)
    assert fusion._collider_distance == pytest.approx(50.0)


def test_multi_chunk_event_is_stitched(bus):
    """跨多个 AUDIO_TICK 的事件应被拼接后再融合（只产出一个结果）。"""
    results: List[FusionResult] = []
    lock = threading.Lock()
    bus.subscribe(Topic.FUSION_RESULT, lambda r: (lock.acquire(), results.append(r), lock.release()))

    fusion = FusionEngine(event_bus=bus, config_manager=_FakeConfigManager(_make_config()))
    fusion.start()

    bus.publish(Topic.CV_UPDATE, _cv(heading=0.0, confidence=1.0))
    # 三个连续 chunk（同一事件），最后一个 is_end
    seg = _stereo(delay=0, n=4096, seed=5)
    bus.publish(Topic.AUDIO_TICK, _tick(seg, is_end=False, fid=1))
    bus.publish(Topic.AUDIO_TICK, _tick(seg, is_end=False, fid=2))
    bus.publish(Topic.AUDIO_TICK, _tick(seg, is_end=True, fid=3))
    assert bus.join(timeout=5.0)

    with lock:
        assert len(results) == 1, "一次事件应仅产出一个融合结果"
        assert results[0].source_frame_id == 3
    fusion.stop()
