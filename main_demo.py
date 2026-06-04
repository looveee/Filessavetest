# -*- coding: utf-8 -*-
"""
main_demo.py
================================================================================
FPS 多模态态势感知系统 —— 上帝级联调启动器 (End-to-End Demo Launcher)

依次拉起全部模块并挂载 Web 雷达看板：
    ConfigManager -> EventBus -> CVEngine -> AudioEngine -> FusionEngine -> WebServer

运行::
    python main_demo.py                  # 真实引擎 + Web 看板
    python main_demo.py --simulate       # 注入合成态势（无需声卡/显示器即可点亮雷达）
    python main_demo.py --no-engines      # 仅 Web + 合成态势（跳过需要硬件的 CV/Audio）
    python main_demo.py --host 0.0.0.0 --port 8000

浏览器打开 http://<host>:<port>/ 即见雷达。Ctrl+C 优雅退出（逐个 stop 所有引擎）。
================================================================================
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import threading
import time
from pathlib import Path
from typing import List

# 确保 src 在导入路径中
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from config_manager import ConfigManager  # noqa: E402
from event_bus import EventBus, Topic  # noqa: E402

_LOGGER = logging.getLogger("main_demo")


# ---------------------------------------------------------------------------
# 合成态势模拟器：用真实数据类走真实链路，便于无硬件环境演示雷达
# ---------------------------------------------------------------------------
class _Simulator:
    """以固定频率发布合成 CV_UPDATE / FUSION_RESULT，驱动雷达可视化。"""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="demo-simulator", daemon=True)

    def start(self) -> None:
        self._thread.start()
        _LOGGER.info("合成态势模拟器已启动。")

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)
        _LOGGER.info("合成态势模拟器已停止。")

    def _run(self) -> None:
        from cv_engine import CVUpdate
        from fusion_engine import FusionResult

        t0 = time.monotonic()
        frame = 0
        last_enemy = 0.0
        while not self._stop.is_set():
            t = time.monotonic() - t0
            frame += 1
            # 玩家绕世界原点缓慢巡逻，朝向随运动切线旋转
            px, py = 30.0 * math.cos(t * 0.2), 30.0 * math.sin(t * 0.2)
            heading = (math.degrees(t * 0.2) + 90.0) % 360.0
            self._bus.publish(
                Topic.CV_UPDATE,
                CVUpdate(
                    x=px,
                    y=py,
                    z=1.8,
                    heading=heading,
                    frame_id=frame,
                    timestamp=time.monotonic(),
                    confidence=0.97,
                ),
            )

            # 每 ~1.2s 在玩家周围抛一个敌人接触点
            if t - last_enemy > 1.2:
                last_enemy = t
                ang = math.radians((heading + 40.0 * math.sin(t)) % 360.0)
                rng = 15.0 + 5.0 * math.sin(t * 1.3)
                ex, ey = px + rng * math.cos(ang), py + rng * math.sin(ang)
                self._bus.publish(
                    Topic.FUSION_RESULT,
                    FusionResult(
                        enemy_x=ex,
                        enemy_y=ey,
                        enemy_z=1.6,
                        confidence=0.85 + 0.14 * abs(math.sin(t * 2)),
                        material_type="mock",
                        theta_world_deg=math.degrees(ang),
                        range_m=rng,
                        source_frame_id=frame,
                        timestamp=time.monotonic(),
                    ),
                )

            self._stop.wait(timeout=0.05)  # ~20Hz


# ---------------------------------------------------------------------------
# 启动编排
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="FPS 态势感知系统 端到端 Demo")
    parser.add_argument("--host", default="127.0.0.1", help="Web 服务监听地址")
    parser.add_argument("--port", type=int, default=8000, help="Web 服务端口")
    parser.add_argument(
        "--simulate", action="store_true", help="注入合成态势（叠加在真实引擎之上）"
    )
    parser.add_argument(
        "--no-engines",
        action="store_true",
        help="跳过需要硬件的 CVEngine/AudioEngine，仅跑 Web + 合成态势",
    )
    parser.add_argument(
        "--record", action="store_true", help="挂载 EventLogger，将态势流录制到 records/*.jsonl"
    )
    parser.add_argument(
        "--replay", metavar="FILE", help="回放指定 .jsonl：禁用真实引擎，重放态势到雷达"
    )
    parser.add_argument(
        "--speed", type=float, default=1.0, help="回放速度倍率（配合 --replay，>1 快放，<1 慢放）"
    )
    args = parser.parse_args()

    replay_mode = args.replay is not None

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # 1) 基础设施
    cfg_mgr = ConfigManager()
    cfg_mgr.start_watching()  # 配置热重载
    bus = EventBus()

    stoppables: List = []  # 逆序优雅停止

    # 录制器最先挂载并订阅，确保从第一个事件起就被完整捕获。
    if args.record:
        from event_logger import EventLogger

        logger = EventLogger(event_bus=bus)
        logger.start()
        stoppables.append(logger)
        _LOGGER.info("录制已挂载 -> %s", logger.path)

    if replay_mode:
        # ===== 回放模式：禁用真实引擎/融合/合成，直接重放录制流到总线 =====
        from event_replayer import EventReplayer

        replayer = EventReplayer(args.replay, event_bus=bus, speed_multiplier=args.speed)
        replayer.start()
        stoppables.append(replayer)
        _LOGGER.info("回放模式：重放 %s（speed=%.2fx）", args.replay, args.speed)
    else:
        # ===== 实时模式：按依赖顺序拉起引擎 =====
        if not args.no_engines:
            try:
                from cv_engine import CVEngine
                from audio_engine import AudioEngine

                cv = CVEngine(event_bus=bus, config_manager=cfg_mgr)
                audio = AudioEngine(event_bus=bus, config_manager=cfg_mgr)
                cv.start()
                stoppables.append(cv)
                audio.start()
                stoppables.append(audio)
            except Exception as exc:  # noqa: BLE001  缺少硬件/依赖时降级为仅 Web
                _LOGGER.warning("CV/Audio 引擎启动失败（缺硬件/依赖？），降级运行: %s", exc)

        # FusionEngine 纯计算，始终启动
        from fusion_engine import FusionEngine

        fusion = FusionEngine(event_bus=bus, config_manager=cfg_mgr)
        fusion.start()
        stoppables.append(fusion)

        # 合成态势（显式开启，或在无引擎时自动开启以便雷达有内容）
        if args.simulate or args.no_engines:
            simulator = _Simulator(bus)
            simulator.start()
            stoppables.append(simulator)

    # Web 看板（阻塞运行；uvicorn 接管 Ctrl+C 信号）
    from web_server import WebServer

    server = WebServer(event_bus=bus, config_manager=cfg_mgr, host=args.host, port=args.port)

    _LOGGER.info("=" * 60)
    _LOGGER.info(" 雷达看板就绪 ->  http://%s:%d/", args.host, args.port)
    _LOGGER.info(" 按 Ctrl+C 优雅退出")
    _LOGGER.info("=" * 60)

    try:
        server.run()  # 阻塞，直到收到 SIGINT
    except KeyboardInterrupt:  # pragma: no cover - 双保险
        pass
    finally:
        _LOGGER.info("正在优雅退出，逐个停止模块…")
        for s in reversed(stoppables):
            try:
                s.stop()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("停止模块 %s 失败: %s", type(s).__name__, exc)
        try:
            cfg_mgr.stop_watching()
        except Exception:  # noqa: BLE001
            pass
        bus.shutdown(wait=False)
        _LOGGER.info("已全部停止，再见。")


if __name__ == "__main__":
    main()
