# -*- coding: utf-8 -*-
"""
web_server 的单元测试。

覆盖：
- 总线回调 -> 序列化 -> 入 outbox（player / enemy 两类消息）
- outbox 有界背压：队满丢最旧
- HTTP 路由（首页 HTML、/health）
- WebSocket 端到端：发布总线事件 -> 前端收到 JSON
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config_manager as cm  # noqa: E402
from cv_engine import CVUpdate  # noqa: E402
from event_bus import EventBus, Topic  # noqa: E402
from fusion_engine import FusionResult  # noqa: E402
from web_server import WebServer, _RADAR_HTML  # noqa: E402


@pytest.fixture()
def bus():
    EventBus._reset_singleton()
    instance = EventBus(max_workers=4)
    yield instance
    instance.shutdown(wait=True)
    EventBus._reset_singleton()


class _FakeConfigManager:
    def __init__(self) -> None:
        self._cfg = cm.AppConfig()

    @property
    def config(self) -> cm.AppConfig:
        return self._cfg

    def register_change_listener(self, fn) -> None:  # 接口兼容
        pass


def _cv(x=1.0, y=2.0, z=3.0, heading=45.0) -> CVUpdate:
    return CVUpdate(x=x, y=y, z=z, heading=heading, frame_id=1, timestamp=0.0, confidence=0.9)


def _fusion() -> FusionResult:
    return FusionResult(enemy_x=5.0, enemy_y=6.0, enemy_z=7.0, confidence=0.91,
                        material_type="mock", theta_world_deg=12.0, range_m=20.0,
                        source_frame_id=3, timestamp=0.0)


# ---------------------------------------------------------------------------
# 1. 总线 -> outbox 序列化
# ---------------------------------------------------------------------------
def test_cv_update_enqueues_player_message(bus):
    ws = WebServer(event_bus=bus, config_manager=_FakeConfigManager())
    bus.publish(Topic.CV_UPDATE, _cv(x=1.0, y=2.0, z=3.0, heading=45.0))
    assert bus.join(timeout=2.0)

    msg = json.loads(ws._outbox.get(timeout=1.0))
    assert msg["type"] == "player"
    assert msg["x"] == 1.0 and msg["y"] == 2.0 and msg["z"] == 3.0
    assert msg["heading"] == 45.0


def test_fusion_result_enqueues_enemy_message(bus):
    ws = WebServer(event_bus=bus, config_manager=_FakeConfigManager())
    bus.publish(Topic.FUSION_RESULT, _fusion())
    assert bus.join(timeout=2.0)

    msg = json.loads(ws._outbox.get(timeout=1.0))
    assert msg["type"] == "enemy"
    assert msg["x"] == 5.0 and msg["y"] == 6.0 and msg["z"] == 7.0
    assert msg["confidence"] == pytest.approx(0.91)
    assert msg["material"] == "mock"


def test_outbox_backpressure_drops_oldest(bus):
    """outbox 满时应丢弃最旧消息，保留最新态势。"""
    ws = WebServer(event_bus=bus, config_manager=_FakeConfigManager(), outbox_maxsize=3)
    # 直接灌入超过容量的消息（绕过总线，专测背压逻辑）
    for i in range(10):
        ws._enqueue({"type": "player", "seq": i})

    drained = []
    while not ws._outbox.empty():
        drained.append(json.loads(ws._outbox.get_nowait())["seq"])

    assert len(drained) == 3, "队列容量应被限制在 3"
    assert drained == [7, 8, 9], "应保留最新的 3 条，丢弃更旧的"


# ---------------------------------------------------------------------------
# 2. HTTP 路由
# ---------------------------------------------------------------------------
def test_index_returns_radar_html(bus):
    from fastapi.testclient import TestClient
    ws = WebServer(event_bus=bus, config_manager=_FakeConfigManager())
    with TestClient(ws.app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "FPS 态势雷达" in resp.text
        assert "<canvas" in resp.text


def test_health_endpoint(bus):
    from fastapi.testclient import TestClient
    ws = WebServer(event_bus=bus, config_manager=_FakeConfigManager())
    with TestClient(ws.app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# 3. WebSocket 端到端
# ---------------------------------------------------------------------------
def test_websocket_receives_broadcast(bus):
    """发布总线事件后，已连接的 WebSocket 客户端应收到对应 JSON。"""
    from fastapi.testclient import TestClient
    ws = WebServer(event_bus=bus, config_manager=_FakeConfigManager())

    with TestClient(ws.app) as client:
        with client.websocket_connect("/ws") as conn:
            bus.publish(Topic.CV_UPDATE, _cv(x=11.0, y=22.0, heading=90.0))
            # 广播任务异步扇出，循环读取直到拿到 player 消息或超时
            got = None
            for _ in range(50):
                data = json.loads(conn.receive_text())
                if data.get("type") == "player":
                    got = data
                    break
            assert got is not None, "WebSocket 未收到 player 广播"
            assert got["x"] == 11.0 and got["y"] == 22.0 and got["heading"] == 90.0
