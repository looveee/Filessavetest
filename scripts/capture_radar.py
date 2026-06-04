# -*- coding: utf-8 -*-
"""
capture_radar.py
================================================================================
雷达可视化凭证生成器（无头浏览器截图）

流程：
1. 在后台线程用 uvicorn 拉起 WebServer。
2. 注入若干合成 CV_UPDATE / FUSION_RESULT 事件，点亮雷达。
3. 用 Playwright (Chromium headless) 打开页面，等待 Canvas 绘制，截图保存。

用法::
    python scripts/capture_radar.py [输出路径]
    # 默认输出 docs/radar_demo.png
"""

from __future__ import annotations

import glob
import math
import os
import sys
import threading
import time
from pathlib import Path

# 让脚本能从 src/ 导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import uvicorn  # noqa: E402

from cv_engine import CVUpdate  # noqa: E402
from event_bus import EventBus, Topic  # noqa: E402
from fusion_engine import FusionResult  # noqa: E402
from web_server import WebServer  # noqa: E402

HOST, PORT = "127.0.0.1", 8077


def _inject(bus: EventBus, stop: threading.Event) -> None:
    """持续注入合成态势，让截图时雷达上有自机箭头与多个敌人光点。"""
    frame = 0
    while not stop.is_set():
        frame += 1
        # 自机：固定在世界原点附近，朝向缓慢旋转
        heading = (frame * 3) % 360
        bus.publish(
            Topic.CV_UPDATE,
            CVUpdate(x=0.0, y=0.0, z=1.8, heading=float(heading),
                     frame_id=frame, timestamp=time.monotonic(), confidence=0.97),
        )
        # 每隔几帧抛一个敌人，分布在不同方位/距离，制造多个余辉光点
        if frame % 3 == 0:
            for k in range(3):
                ang = math.radians((frame * 5 + k * 120) % 360)
                rng = 8.0 + 4.0 * k
                bus.publish(
                    Topic.FUSION_RESULT,
                    FusionResult(
                        enemy_x=rng * math.cos(ang), enemy_y=rng * math.sin(ang),
                        enemy_z=1.6, confidence=0.80 + 0.06 * k, material_type="mesh",
                        theta_world_deg=math.degrees(ang), range_m=rng,
                        source_frame_id=frame, timestamp=time.monotonic(),
                    ),
                )
        stop.wait(0.1)


def _find_chromium() -> str | None:
    """定位可用的 Chromium 可执行文件（兼容预装/缓存路径，避免运行时下载）。"""
    candidates = []
    candidates += glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")
    candidates += glob.glob(
        os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome")
    )
    for path in sorted(candidates, reverse=True):  # 取较新的构建
        if os.path.exists(path):
            return path
    return None


def main() -> int:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/radar_demo.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    bus = EventBus()
    server = WebServer(event_bus=bus, host=HOST, port=PORT)
    config = uvicorn.Config(server.app, host=HOST, port=PORT, log_level="warning")
    uv = uvicorn.Server(config)

    server_thread = threading.Thread(target=uv.run, name="uvicorn", daemon=True)
    server_thread.start()

    # 等待服务就绪
    for _ in range(100):
        if uv.started:
            break
        time.sleep(0.05)

    stop = threading.Event()
    injector = threading.Thread(target=_inject, args=(bus, stop), name="injector", daemon=True)
    injector.start()

    try:
        from playwright.sync_api import sync_playwright

        chromium_exe = _find_chromium()
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=chromium_exe, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 760, "height": 820})
            page.goto(f"http://{HOST}:{PORT}/", wait_until="networkidle")
            # 让 WebSocket 连上、注入数据累积、Canvas 绘制若干帧后再截图
            time.sleep(2.5)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out_path))
            browser.close()
        print(f"✓ 雷达截图已保存: {out_path}")
        rc = 0
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 截图失败: {exc}")
        rc = 1
    finally:
        stop.set()
        uv.should_exit = True
        server_thread.join(timeout=5.0)
        bus.shutdown(wait=False)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
