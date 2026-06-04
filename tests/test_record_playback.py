# -*- coding: utf-8 -*-
"""
event_logger / event_replayer 的单元测试（录制与回放）。

覆盖：
- 录制为 .jsonl：CV/AUDIO/FUSION 三类各一行，AUDIO 波形被剥离、仅留元数据
- 录制 I/O 不阻塞总线（回调侧仅入队）
- 回放往返：三类事件被准确重建并按主题重新发布
- 变速回放：speed_multiplier 影响总时长，时序单调
- 回放可被 stop() 即时打断
"""

import json
import sys
import threading
import time
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audio_engine import AudioTick  # noqa: E402
from cv_engine import CVUpdate  # noqa: E402
from event_bus import EventBus, Topic  # noqa: E402
from event_logger import EventLogger  # noqa: E402
from event_replayer import EventReplayer  # noqa: E402
from fusion_engine import FusionResult  # noqa: E402


@pytest.fixture()
def bus():
    EventBus._reset_singleton()
    instance = EventBus(max_workers=4)
    yield instance
    instance.shutdown(wait=True)
    EventBus._reset_singleton()


def _cv(x=1.0, y=2.0, heading=30.0, fid=1) -> CVUpdate:
    return CVUpdate(x=x, y=y, z=3.0, heading=heading, frame_id=fid,
                    timestamp=0.0, confidence=0.9)


def _audio(rms=0.4, fid=2, start=True, end=False, n=256, ch=2) -> AudioTick:
    return AudioTick(audio_array=np.random.rand(n, ch).astype(np.float32),
                     timestamp=0.0, frame_id=fid, rms=rms,
                     is_speech_start=start, is_speech_end=end)


def _fusion(fid=3) -> FusionResult:
    return FusionResult(enemy_x=5.0, enemy_y=6.0, enemy_z=7.0, confidence=0.9,
                        material_type="mock", theta_world_deg=12.0, range_m=20.0,
                        source_frame_id=fid, timestamp=0.0)


# ---------------------------------------------------------------------------
# 1. 录制
# ---------------------------------------------------------------------------
def test_logger_writes_jsonl_and_strips_waveform(bus, tmp_path):
    logger = EventLogger(event_bus=bus, output_dir=tmp_path, filename="t.jsonl")
    logger.start()

    bus.publish(Topic.CV_UPDATE, _cv())
    bus.publish(Topic.AUDIO_TICK, _audio(rms=0.4, n=256, ch=2))
    bus.publish(Topic.FUSION_RESULT, _fusion())
    assert bus.join(timeout=2.0)
    logger.stop()

    lines = [json.loads(l) for l in logger.path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 3
    by_topic = {l["topic"]: l for l in lines}

    # AUDIO_TICK：无波形，仅元数据
    audio = by_topic["AUDIO_TICK"]["data"]
    assert "audio_array" not in audio
    assert audio["rms"] == pytest.approx(0.4)
    assert audio["n_frames"] == 256 and audio["channels"] == 2
    assert audio["is_speech_start"] is True

    # CV / FUSION：标量字段齐全
    assert by_topic["CV_UPDATE"]["data"]["heading"] == pytest.approx(30.0)
    assert by_topic["FUSION_RESULT"]["data"]["enemy_x"] == pytest.approx(5.0)
    # 每行都有调度用的 ts
    assert all(isinstance(l["ts"], (int, float)) for l in lines)


def test_logger_callback_is_non_blocking(bus, tmp_path):
    """录制回调应仅入队即返回，不被磁盘 I/O 阻塞。"""
    logger = EventLogger(event_bus=bus, output_dir=tmp_path, filename="nb.jsonl")
    logger.start()

    t0 = time.monotonic()
    for i in range(200):
        logger._enqueue(Topic.CV_UPDATE, _cv(x=float(i)))
    elapsed = time.monotonic() - t0

    assert elapsed < 0.2, f"录制入队耗时过长 {elapsed:.3f}s，疑似阻塞"
    logger.stop()


# ---------------------------------------------------------------------------
# 2. 回放往返
# ---------------------------------------------------------------------------
def _write_jsonl(path: Path, rows: List[Tuple[str, float, dict]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for topic, ts, data in rows:
            fh.write(json.dumps({"topic": topic, "ts": ts, "data": data}) + "\n")


def test_replay_roundtrip_reconstructs_all_topics(bus, tmp_path):
    path = tmp_path / "rec.jsonl"
    _write_jsonl(path, [
        ("CV_UPDATE", 100.0, {"x": 1.0, "y": 2.0, "z": 3.0, "heading": 30.0,
                              "frame_id": 1, "timestamp": 0.0, "confidence": 0.9}),
        ("AUDIO_TICK", 100.02, {"timestamp": 0.0, "frame_id": 2, "rms": 0.4,
                                "is_speech_start": True, "is_speech_end": False,
                                "n_frames": 256, "channels": 2}),
        ("FUSION_RESULT", 100.05, {"enemy_x": 5.0, "enemy_y": 6.0, "enemy_z": 7.0,
                                   "confidence": 0.9, "material_type": "mock",
                                   "theta_world_deg": 12.0, "range_m": 20.0,
                                   "source_frame_id": 3, "timestamp": 0.0}),
    ])

    received = {"cv": [], "audio": [], "fusion": []}
    lock = threading.Lock()
    bus.subscribe(Topic.CV_UPDATE, lambda p: (lock.acquire(), received["cv"].append(p), lock.release()))
    bus.subscribe(Topic.AUDIO_TICK, lambda p: (lock.acquire(), received["audio"].append(p), lock.release()))
    bus.subscribe(Topic.FUSION_RESULT, lambda p: (lock.acquire(), received["fusion"].append(p), lock.release()))

    replayer = EventReplayer(path, event_bus=bus, speed_multiplier=5.0)
    replayer.start()
    assert replayer.wait(timeout=5.0)
    assert bus.join(timeout=3.0)

    with lock:
        assert len(received["cv"]) == 1
        assert len(received["audio"]) == 1
        assert len(received["fusion"]) == 1
        # 类型与字段正确重建
        assert isinstance(received["cv"][0], CVUpdate)
        assert received["cv"][0].heading == pytest.approx(30.0)
        assert isinstance(received["audio"][0], AudioTick)
        # 波形剥离 -> 空数组，但元数据保留
        assert received["audio"][0].audio_array.shape == (0, 2)
        assert received["audio"][0].rms == pytest.approx(0.4)
        assert isinstance(received["fusion"][0], FusionResult)
        assert received["fusion"][0].enemy_x == pytest.approx(5.0)
    assert replayer.published == 3


def test_replay_respects_speed_multiplier(bus, tmp_path):
    """录制相邻间隔 0.2s，2x 回放总时长应约 0.1s。"""
    path = tmp_path / "timed.jsonl"
    _write_jsonl(path, [
        ("CV_UPDATE", 10.0, {"x": 0.0, "y": 0.0, "z": 0.0, "heading": 0.0,
                             "frame_id": 1, "timestamp": 0.0, "confidence": 1.0}),
        ("CV_UPDATE", 10.2, {"x": 1.0, "y": 0.0, "z": 0.0, "heading": 0.0,
                             "frame_id": 2, "timestamp": 0.0, "confidence": 1.0}),
    ])

    stamps: List[float] = []
    bus.subscribe(Topic.CV_UPDATE, lambda p: stamps.append(time.monotonic()))

    t0 = time.monotonic()
    replayer = EventReplayer(path, event_bus=bus, speed_multiplier=2.0)
    replayer.start()
    assert replayer.wait(timeout=5.0)
    assert bus.join(timeout=2.0)
    total = time.monotonic() - t0

    # 0.2s / 2x = 0.1s（给足调度容差）
    assert 0.05 < total < 0.5, f"变速回放总时长异常: {total:.3f}s"
    assert len(stamps) == 2


def test_replay_can_be_stopped(bus, tmp_path):
    """长间隔回放应能被 stop() 即时打断。"""
    path = tmp_path / "long.jsonl"
    _write_jsonl(path, [
        ("CV_UPDATE", 0.0, {"x": 0.0, "y": 0.0, "z": 0.0, "heading": 0.0,
                            "frame_id": 1, "timestamp": 0.0, "confidence": 1.0}),
        ("CV_UPDATE", 100.0, {"x": 1.0, "y": 0.0, "z": 0.0, "heading": 0.0,
                              "frame_id": 2, "timestamp": 0.0, "confidence": 1.0}),
    ])

    replayer = EventReplayer(path, event_bus=bus, speed_multiplier=1.0)
    replayer.start()
    time.sleep(0.1)
    t0 = time.monotonic()
    replayer.stop(timeout=3.0)   # 第二个事件在 100s 后，必须被打断
    assert time.monotonic() - t0 < 2.0, "stop() 未能即时打断回放"


def test_replay_missing_file_raises(bus, tmp_path):
    replayer = EventReplayer(tmp_path / "nope.jsonl", event_bus=bus)
    with pytest.raises(FileNotFoundError):
        replayer.load()
