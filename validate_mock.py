"""无硬件环境下的捕获层逻辑验证。

通过注入伪造的 mss / soundcard 模块，验证 VideoCapture / AudioCapture
的线程启停、时间戳生成、队列投递与帧率控制逻辑是否正确。
（真实屏幕/音频需在有显示与扬声器的环境运行 main.py。）
"""

from __future__ import annotations

import queue
import sys
import time
import types

import numpy as np


# --- 伪造 mss 模块 ---------------------------------------------------------
def _install_fake_mss() -> None:
    fake = types.ModuleType("mss")

    class _FakeSct:
        def grab(self, region):
            h, w = region["height"], region["width"]
            # 返回 BGRA uint8 数组，模拟 mss 输出。
            return np.zeros((h, w, 4), dtype=np.uint8)

        def close(self):
            pass

    fake.mss = lambda: _FakeSct()
    sys.modules["mss"] = fake


# --- 伪造 soundcard 模块 ---------------------------------------------------
def _install_fake_soundcard() -> None:
    fake = types.ModuleType("soundcard")

    class _FakeRecorder:
        def __init__(self, channels):
            self._channels = channels

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def record(self, numframes):
            # 模拟阻塞读取耗时，并返回 float32 数据。
            time.sleep(numframes / 48000)
            return np.random.uniform(-0.5, 0.5, (numframes, self._channels)).astype(np.float32)

    class _FakeMic:
        def __init__(self, channels):
            self._channels = channels

        def recorder(self, samplerate, channels, blocksize):
            return _FakeRecorder(channels)

    class _FakeSpeaker:
        name = "FakeSpeaker"

    fake.default_speaker = lambda: _FakeSpeaker()
    fake.get_microphone = lambda name, include_loopback=False: _FakeMic(2)
    sys.modules["soundcard"] = fake


def main() -> int:
    _install_fake_mss()
    _install_fake_soundcard()

    from core.capture import AudioCapture, VideoCapture

    video_q: "queue.Queue" = queue.Queue(maxsize=120)
    audio_q: "queue.Queue" = queue.Queue(maxsize=200)

    vc = VideoCapture(video_q, roi={"left": 0, "top": 0, "width": 320, "height": 240}, fps_limit=30)
    ac = AudioCapture(audio_q, sample_rate=48000, channels=2, chunk_duration_ms=20)

    vc.start()
    ac.start()
    time.sleep(2.0)
    vc.stop()
    ac.stop()

    frames = []
    while not video_q.empty():
        frames.append(video_q.get_nowait())
    chunks = []
    while not audio_q.empty():
        chunks.append(audio_q.get_nowait())

    ok = True

    # 视频：约 2 秒 * 30fps ≈ 60 帧（允许调度抖动）。
    print(f"[video] frames captured = {len(frames)}")
    if not (40 <= len(frames) <= 70):
        print("  !! 帧数偏离预期 ~60"); ok = False
    if frames:
        ids = [f.frame_id for f in frames]
        if ids != list(range(ids[0], ids[0] + len(ids))):
            print("  !! frame_id 非连续自增"); ok = False
        ts = [f.timestamp_ns for f in frames]
        if any(b <= a for a, b in zip(ts, ts[1:])):
            print("  !! 时间戳非单调递增"); ok = False
        if frames[0].image.shape != (240, 320, 4) or frames[0].image.dtype != np.uint8:
            print("  !! 图像形状/类型不符 (期望 (240,320,4) uint8)"); ok = False
        print(f"  frame_id 范围 = {ids[0]}..{ids[-1]}, shape={frames[0].image.shape}, dtype={frames[0].image.dtype}")

    # 音频：约 2 秒 / 20ms ≈ 100 块。
    print(f"[audio] chunks captured = {len(chunks)}")
    if not (70 <= len(chunks) <= 110):
        print("  !! 块数偏离预期 ~100"); ok = False
    if chunks:
        cids = [c.chunk_id for c in chunks]
        if cids != list(range(cids[0], cids[0] + len(cids))):
            print("  !! chunk_id 非连续自增"); ok = False
        c0 = chunks[0]
        if c0.samples.dtype != np.float32:
            print("  !! 音频非 float32"); ok = False
        if c0.frames != 960:  # 48000 * 20ms
            print(f"  !! 帧数不符，期望 960 实际 {c0.frames}"); ok = False
        print(f"  chunk_id 范围 = {cids[0]}..{cids[-1]}, frames/chunk={c0.frames}, dtype={c0.samples.dtype}")

    # 线程已退出。
    if vc._thread.is_alive() or ac._thread.is_alive():
        print("  !! 线程未在 stop() 后退出"); ok = False

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
