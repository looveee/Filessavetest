# -*- coding: utf-8 -*-
"""
audio_engine 的单元测试。

策略
----
彻底 Mock 掉底层声卡采集（注入假的 recorder_factory），使测试在无声卡 / CI
环境下稳定运行。覆盖：
- VAD 状态机：对 "静音 → 高能量突变 → 回落" 序列能正确识别起止
- AUDIO_TICK 事件成功发布，且 payload 为 AudioTick
- pre-roll 起音回补：事件开头不被裁掉
- 配置热重载即时改变能量阈值，进而改变 VAD 行为
- 端到端：经后台线程 + mock 采集后端跑通并发布事件
"""

import sys
import threading
import time
from pathlib import Path
from typing import List

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config_manager as cm  # noqa: E402
from audio_engine import AudioEngine, AudioTick, _VadState  # noqa: E402
from event_bus import EventBus, Topic  # noqa: E402


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
@pytest.fixture()
def bus():
    EventBus._reset_singleton()
    instance = EventBus(max_workers=4)
    yield instance
    instance.shutdown(wait=True)
    EventBus._reset_singleton()


def _make_config(**audio_overrides) -> cm.AppConfig:
    """构造一个内存配置对象（不依赖磁盘文件），便于注入引擎。"""
    cfg = cm.AppConfig()
    for k, v in audio_overrides.items():
        setattr(cfg.audio, k, v)
    return cfg


class _FakeConfigManager:
    """最小化的 ConfigManager 替身：提供 config 快照与变更监听注册。"""

    def __init__(self, cfg: cm.AppConfig) -> None:
        self._cfg = cfg
        self._listeners: List = []

    @property
    def config(self) -> cm.AppConfig:
        return self._cfg

    def register_change_listener(self, fn) -> None:
        self._listeners.append(fn)

    def push(self, new_cfg: cm.AppConfig) -> None:
        """模拟一次热重载，通知所有监听器。"""
        self._cfg = new_cfg
        for fn in self._listeners:
            fn(new_cfg)


def _silence(frames: int = 256, channels: int = 2) -> np.ndarray:
    """一段静音（全 0）。"""
    return np.zeros((frames, channels), dtype=np.float32)


def _loud(frames: int = 256, channels: int = 2, amp: float = 0.5) -> np.ndarray:
    """一段高能量随机噪声。"""
    rng = np.random.default_rng(42)
    return (rng.standard_normal((frames, channels)) * amp).astype(np.float32)


# ---------------------------------------------------------------------------
# 1. VAD 状态机（直接驱动 _process_chunk，确定性强）
# ---------------------------------------------------------------------------
def test_vad_detects_burst_and_publishes(bus):
    """静音→高能量突变→回落：应识别事件并发布 AUDIO_TICK。"""
    received: List[AudioTick] = []
    bus.subscribe(Topic.AUDIO_TICK, lambda t: received.append(t))

    cfg = _make_config(
        energy_threshold=0.05,
        vad_start_chunks=3,
        vad_silence_duration_ms=300.0,
        chunk_size=256,
        sample_rate=48000,
    )
    engine = AudioEngine(event_bus=bus, config_manager=_FakeConfigManager(cfg))

    # chunk=256@48kHz => 每块约 5.33ms；300ms 静音需约 57 块，故尾部给足 60 块静音。
    seq = (
        [_silence() for _ in range(5)]
        + [_loud() for _ in range(8)]
        + [_silence() for _ in range(60)]
    )
    for chunk in seq:
        engine._process_chunk(chunk)

    assert bus.join(timeout=3.0)

    assert len(received) > 0, "高能量突变未触发任何 AUDIO_TICK"
    assert all(isinstance(t, AudioTick) for t in received)
    # 应恰好有一次事件开始与一次事件结束标记
    starts = [t for t in received if t.is_speech_start]
    ends = [t for t in received if t.is_speech_end]
    assert len(starts) == 1, f"事件开始标记数异常: {len(starts)}"
    assert len(ends) == 1, f"事件结束标记数异常: {len(ends)}"
    # 结束后状态机应回到 IDLE
    assert engine._state is _VadState.IDLE


def test_pure_silence_emits_nothing(bus):
    """全程静音不应产生任何事件。"""
    received: List[AudioTick] = []
    bus.subscribe(Topic.AUDIO_TICK, lambda t: received.append(t))

    cfg = _make_config(energy_threshold=0.05, vad_start_chunks=3)
    engine = AudioEngine(event_bus=bus, config_manager=_FakeConfigManager(cfg))

    for _ in range(50):
        engine._process_chunk(_silence())

    assert bus.join(timeout=2.0)
    assert received == [], "纯静音不应发布任何 AUDIO_TICK"


def test_preroll_recovers_onset(bus):
    """pre-roll 应回补起音：事件首个 tick 的 frame_id 早于触发判定点。"""
    received: List[AudioTick] = []
    bus.subscribe(Topic.AUDIO_TICK, lambda t: received.append(t))

    cfg = _make_config(energy_threshold=0.05, vad_start_chunks=3, chunk_size=256)
    engine = AudioEngine(event_bus=bus, config_manager=_FakeConfigManager(cfg))

    # 前 2 个高能量不足以触发（需连续 3 个），第 3 个触发；pre-roll 应回补前两个
    for chunk in [_loud(), _loud(), _loud(), _loud()]:
        engine._process_chunk(chunk)

    assert bus.join(timeout=2.0)
    start_tick = next(t for t in received if t.is_speech_start)
    # 起始 tick 应是 frame_id==1（第一个高能量 chunk），证明起音被回补而非从第3个才开始
    assert start_tick.frame_id == 1, f"起音未被回补，首 tick frame_id={start_tick.frame_id}"


# ---------------------------------------------------------------------------
# 2. 配置热重载即时改变 VAD 阈值
# ---------------------------------------------------------------------------
def test_hot_reload_changes_threshold(bus):
    """热重载提高能量阈值后，原本可触发的能量不再触发。"""
    fake_mgr = _FakeConfigManager(_make_config(energy_threshold=0.05, vad_start_chunks=2))
    engine = AudioEngine(event_bus=bus, config_manager=fake_mgr)
    assert engine._energy_threshold == 0.05

    # 中等能量信号：RMS 约 0.2，高于 0.05、低于 0.9
    mid = np.ones((256, 2), dtype=np.float32) * 0.2

    received: List[AudioTick] = []
    bus.subscribe(Topic.AUDIO_TICK, lambda t: received.append(t))

    # 阈值 0.05 下，连续 2 个中能量应触发
    engine._process_chunk(mid)
    engine._process_chunk(mid)
    assert bus.join(timeout=2.0)
    assert len(received) >= 1, "低阈值下中能量信号应触发事件"

    # 让事件自然结束并清空
    for _ in range(30):
        engine._process_chunk(_silence())
    assert bus.join(timeout=2.0)
    received.clear()

    # 热重载：把阈值抬高到 0.9，中能量信号(0.2)不应再触发
    fake_mgr.push(_make_config(energy_threshold=0.9, vad_start_chunks=2))
    assert engine._energy_threshold == 0.9, "热重载未即时更新能量阈值"

    for _ in range(10):
        engine._process_chunk(mid)
    assert bus.join(timeout=2.0)
    assert received == [], "高阈值热更新后，中能量信号不应再触发事件"


def test_hot_switch_force_closes_active_event(bus):
    """设备重开边界：处于 ACTIVE 时强制闭合应补发 is_end=True 并复位到 IDLE。"""
    received: List[AudioTick] = []
    bus.subscribe(Topic.AUDIO_TICK, lambda t: received.append(t))

    cfg = _make_config(energy_threshold=0.05, vad_start_chunks=2, chunk_size=256)
    engine = AudioEngine(event_bus=bus, config_manager=_FakeConfigManager(cfg))

    # 驱动进入 ACTIVE（连续 2 个高能量块）
    engine._process_chunk(_loud())
    engine._process_chunk(_loud())
    assert engine._state is _VadState.ACTIVE
    assert bus.join(timeout=2.0)

    # 模拟采样参数变更触发的设备重开边界：强制闭合
    engine._force_close_active_event()
    assert bus.join(timeout=2.0)

    # 状态机彻底复位，且恰好补发了一个事件结束帧
    assert engine._state is _VadState.IDLE
    ends = [t for t in received if t.is_speech_end]
    assert len(ends) == 1, f"强制闭合应补发恰好一个 is_end，实际 {len(ends)}"
    # 哨兵帧为纯控制边界，不携带伪造音频样本
    assert ends[0].audio_array.shape[0] == 0, "is_end 哨兵帧不应注入伪造样本"

    # 闭合后无悬挂：再来高能量应开启一次全新事件（新的 is_start）
    received.clear()
    engine._process_chunk(_loud())
    engine._process_chunk(_loud())
    assert bus.join(timeout=2.0)
    assert any(t.is_speech_start for t in received), "闭合后应能正常开启新事件"


def test_force_close_is_noop_when_idle(bus):
    """IDLE 状态下强制闭合应为空操作，不发任何事件。"""
    received: List[AudioTick] = []
    bus.subscribe(Topic.AUDIO_TICK, lambda t: received.append(t))

    cfg = _make_config(energy_threshold=0.05)
    engine = AudioEngine(event_bus=bus, config_manager=_FakeConfigManager(cfg))
    assert engine._state is _VadState.IDLE

    engine._force_close_active_event()
    assert bus.join(timeout=1.0)
    assert received == [], "IDLE 下强制闭合不应发布任何事件"


def test_chunk_is_copied_not_aliased(bus):
    """_as_float32 必须切断与底层缓冲的别名：就地覆写源数组不得污染已发出的切片。"""
    cfg = _make_config()
    engine = AudioEngine(event_bus=bus, config_manager=_FakeConfigManager(cfg))

    # 模拟采集库复用的环形缓冲
    shared_buffer = np.ones((256, 2), dtype=np.float32) * 0.5
    out = engine._as_float32(shared_buffer)
    out_snapshot = out.copy()

    # 采集线程"下一次 record" 就地覆写同一缓冲
    shared_buffer[:] = -9.0

    assert np.array_equal(out, out_snapshot), "切片被底层缓冲覆写污染——存在别名！"
    assert not np.shares_memory(out, shared_buffer), "切片仍与源缓冲共享内存"


def test_hot_reload_marks_params_dirty_on_samplerate_change(bus):
    """采样率变化应置脏标志，以驱动采集线程重开设备。"""
    fake_mgr = _FakeConfigManager(_make_config(sample_rate=48000))
    engine = AudioEngine(event_bus=bus, config_manager=fake_mgr)
    # 初始化后消费掉首次置脏（构造时 _apply_config 也会比较，但初值相等不置脏）
    assert engine._params_dirty is False

    fake_mgr.push(_make_config(sample_rate=44100))
    assert engine._params_dirty is True, "采样率变化应置脏以触发设备重开"


# ---------------------------------------------------------------------------
# 3. 端到端：后台线程 + mock 采集后端
# ---------------------------------------------------------------------------
def test_end_to_end_with_mocked_recorder(bus):
    """经真实后台线程 + 注入的 mock 采集后端，验证 AUDIO_TICK 被发布。"""
    received: List[AudioTick] = []
    lock = threading.Lock()

    def on_tick(t):
        with lock:
            received.append(t)

    bus.subscribe(Topic.AUDIO_TICK, on_tick)

    # 构造 mock 采集后端：吐出 静音→高能量→静音 序列，耗尽后持续吐静音。
    seq = (
        [_silence() for _ in range(3)]
        + [_loud() for _ in range(6)]
        + [_silence() for _ in range(40)]
    )
    idx = {"i": 0}
    seq_lock = threading.Lock()

    class _FakeRecorder:
        def record(self, numframes: int) -> np.ndarray:
            with seq_lock:
                i = idx["i"]
                idx["i"] += 1
            if i < len(seq):
                return seq[i]
            time.sleep(0.001)  # 耗尽后轻微让出，模拟阻塞式采集
            return _silence()

    from contextlib import contextmanager

    @contextmanager
    def fake_factory(sample_rate, chunk_size, channels):
        # 断言引擎确实按配置参数请求设备（无声卡也不触碰真实 API）
        assert sample_rate == 48000 and channels == 2
        yield _FakeRecorder()

    cfg = _make_config(
        energy_threshold=0.05, vad_start_chunks=3, vad_silence_duration_ms=200.0, chunk_size=256
    )
    engine = AudioEngine(
        event_bus=bus,
        config_manager=_FakeConfigManager(cfg),
        recorder_factory=fake_factory,
    )
    engine.start()

    # 轮询等待事件产生（最多 ~3s）
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        with lock:
            has_start = any(t.is_speech_start for t in received)
            has_end = any(t.is_speech_end for t in received)
        if has_start and has_end:
            break
        time.sleep(0.02)

    engine.stop()
    bus.join(timeout=3.0)

    with lock:
        assert any(t.is_speech_start for t in received), "端到端未捕获事件开始"
        assert any(t.is_speech_end for t in received), "端到端未捕获事件结束"
        assert all(isinstance(t, AudioTick) for t in received)
