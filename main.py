"""捕获层验证脚本。

启动 VideoCapture / AudioCapture 两个线程，运行约 10 秒，
打印带高精度时间戳的输出，确认两路捕获稳定工作。
"""

from __future__ import annotations

import logging
import queue
import time

from core.capture import AudioCapture, VideoCapture
from core.types import RawAudioChunk, RawVideoFrame


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(threadName)s] %(levelname)s %(name)s: %(message)s",
    )

    video_queue: "queue.Queue[RawVideoFrame]" = queue.Queue(maxsize=30)
    audio_queue: "queue.Queue[RawAudioChunk]" = queue.Queue(maxsize=50)

    video_cap = VideoCapture(
        video_queue,
        roi={"left": 100, "top": 100, "width": 400, "height": 300},
        fps_limit=30,
    )
    audio_cap = AudioCapture(audio_queue)

    video_cap.start()
    audio_cap.start()

    video_count = 0
    audio_count = 0

    try:
        # 运行约 10 秒（300 次 * 30ms）。
        for _ in range(300):
            try:
                frame = video_queue.get_nowait()
                video_count += 1
                print(f"Video Frame {frame.frame_id} @ {frame.timestamp_ns} "
                      f"shape={frame.image.shape}")
            except queue.Empty:
                pass

            try:
                chunk = audio_queue.get_nowait()
                audio_count += 1
                print(f"Audio Chunk {chunk.chunk_id} @ {chunk.timestamp_ns} "
                      f"frames={chunk.frames}")
            except queue.Empty:
                pass

            time.sleep(0.03)
    finally:
        video_cap.stop()
        audio_cap.stop()
        print(f"Capture stopped. video_frames={video_count}, audio_chunks={audio_count}")


if __name__ == "__main__":
    main()
