# -*- coding: utf-8 -*-
"""
mesh_collider / FusionEngine 真实网格集成的单元测试。

覆盖：
- TrimeshCollider 射线求交返回最近的正向交点（剔除穿墙背面点）
- 背向/未命中返回 None
- 缺失文件抛 FileNotFoundError
- FusionEngine 配置 map_model_path 时启用 TrimeshCollider，敌人落在网格表面
- 模型只加载一次（同路径热重载不重建）
- 非法模型路径回退到 FixedDistanceWallCollider
"""

import sys
import threading
from pathlib import Path
from typing import List

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import trimesh  # noqa: E402

import config_manager as cm  # noqa: E402
from audio_engine import AudioTick  # noqa: E402
from cv_engine import CVUpdate  # noqa: E402
from event_bus import EventBus, Topic  # noqa: E402
from fusion_engine import FixedDistanceWallCollider, FusionEngine, FusionResult  # noqa: E402
from mesh_collider import TrimeshCollider  # noqa: E402


@pytest.fixture()
def bus():
    EventBus._reset_singleton()
    instance = EventBus(max_workers=4)
    yield instance
    instance.shutdown(wait=True)
    EventBus._reset_singleton()


def _box_obj(tmp_path: Path, center=(0.0, 0.0, 0.0), size=2.0, name="box.obj") -> Path:
    """生成一个居中于 center、边长 size 的立方体网格文件，返回路径。"""
    tf = trimesh.transformations.translation_matrix(center)
    mesh = trimesh.creation.box(extents=[size, size, size], transform=tf)
    path = tmp_path / name
    mesh.export(path)
    return path


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


def _cv(x=0.0, y=0.0, z=0.0, heading=0.0, conf=1.0) -> CVUpdate:
    return CVUpdate(x=x, y=y, z=z, heading=heading, frame_id=1, timestamp=0.0, confidence=conf)


def _zero_delay_tick(n=8192, ch=2) -> AudioTick:
    rng = np.random.default_rng(0)
    left = rng.standard_normal(n).astype(np.float32)
    audio = np.stack([left, left], axis=1)  # 零时延 -> θ_rel≈0
    return AudioTick(audio_array=audio, timestamp=0.0, frame_id=9, rms=0.5,
                     is_speech_start=False, is_speech_end=True)


# ---------------------------------------------------------------------------
# 1. TrimeshCollider 单元
# ---------------------------------------------------------------------------
def test_collider_returns_nearest_forward_hit(tmp_path):
    """箱体 x∈[-1,1]，从 (-5,0,0) 向 +X，应命中前面 x=-1（而非背面 x=1）。"""
    path = _box_obj(tmp_path)
    col = TrimeshCollider(path)
    hit = col.raycast(np.array([-5.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]))
    assert hit is not None
    assert hit.point[0] == pytest.approx(-1.0, abs=1e-6), "应命中最近的前表面，而非穿墙到背面"
    assert hit.distance == pytest.approx(4.0, abs=1e-6)
    assert hit.material_type == "mesh"


def test_collider_ray_pointing_away_misses(tmp_path):
    path = _box_obj(tmp_path)
    col = TrimeshCollider(path)
    hit = col.raycast(np.array([-5.0, 0.0, 0.0]), np.array([-1.0, 0.0, 0.0]))
    assert hit is None, "背向射线不应命中"


def test_collider_ray_misses_geometry(tmp_path):
    path = _box_obj(tmp_path)
    col = TrimeshCollider(path)
    # y=5 完全在箱体外侧
    hit = col.raycast(np.array([-5.0, 5.0, 0.0]), np.array([1.0, 0.0, 0.0]))
    assert hit is None


def test_collider_unnormalized_direction(tmp_path):
    """方向向量未归一化也应正确返回米制距离。"""
    path = _box_obj(tmp_path)
    col = TrimeshCollider(path)
    hit = col.raycast(np.array([-5.0, 0.0, 0.0]), np.array([7.0, 0.0, 0.0]))  # 非单位向量
    assert hit is not None
    assert hit.distance == pytest.approx(4.0, abs=1e-6)


def test_missing_model_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        TrimeshCollider(tmp_path / "nope.obj")


# ---------------------------------------------------------------------------
# 2. FusionEngine 集成
# ---------------------------------------------------------------------------
def test_fusion_uses_mesh_collider(bus, tmp_path):
    """配置 map_model_path 后，融合结果应落在网格表面、材质为 mesh。"""
    # 箱体居中于 (10,0,0)，x∈[9,11]
    path = _box_obj(tmp_path, center=(10.0, 0.0, 0.0))
    cfg = _make_config(map_model_path=str(path), min_confidence=0.85)
    fusion = FusionEngine(event_bus=bus, config_manager=_FakeConfigManager(cfg))
    assert isinstance(fusion._collider, TrimeshCollider)

    results: List[FusionResult] = []
    lock = threading.Lock()
    bus.subscribe(Topic.FUSION_RESULT, lambda r: (lock.acquire(), results.append(r), lock.release()))
    fusion.start()

    # 玩家在原点朝 +X，零时延音频 -> 射线 +X 命中箱体前表面 x=9
    bus.publish(Topic.CV_UPDATE, _cv(x=0.0, y=0.0, z=0.0, heading=0.0, conf=1.0))
    bus.publish(Topic.AUDIO_TICK, _zero_delay_tick())
    assert bus.join(timeout=5.0)

    with lock:
        assert len(results) == 1
        r = results[0]
    assert r.enemy_x == pytest.approx(9.0, abs=0.3), "敌人应落在网格前表面 x=9"
    assert r.enemy_y == pytest.approx(0.0, abs=0.3)
    assert r.material_type == "mesh"
    assert r.range_m == pytest.approx(9.0, abs=0.3)
    fusion.stop()


def test_mesh_loaded_only_once(bus, tmp_path):
    """同一模型路径的重复热重载不应重建碰撞体（只加载一次）。"""
    path = _box_obj(tmp_path)
    fake = _FakeConfigManager(_make_config(map_model_path=str(path)))
    fusion = FusionEngine(event_bus=bus, config_manager=fake)
    c1 = fusion._collider
    assert isinstance(c1, TrimeshCollider)

    # 推送相同路径的配置（仅改无关参数）
    fake.push(_make_config(map_model_path=str(path), min_confidence=0.5))
    c2 = fusion._collider
    assert c1 is c2, "相同模型路径不应触发重新加载"


def test_invalid_model_falls_back_to_wall(bus, tmp_path):
    """非法模型路径应回退到假想墙碰撞体，系统不崩溃。"""
    cfg = _make_config(map_model_path=str(tmp_path / "does_not_exist.obj"),
                       collider_distance_m=20.0)
    fusion = FusionEngine(event_bus=bus, config_manager=_FakeConfigManager(cfg))
    assert isinstance(fusion._collider, FixedDistanceWallCollider), "加载失败应回退假想墙"


def test_switch_from_wall_to_mesh_on_hot_reload(bus, tmp_path):
    """运行中从假想墙热切换到真实网格。"""
    path = _box_obj(tmp_path, center=(10.0, 0.0, 0.0))
    fake = _FakeConfigManager(_make_config(map_model_path=""))  # 起初无模型
    fusion = FusionEngine(event_bus=bus, config_manager=fake)
    assert isinstance(fusion._collider, FixedDistanceWallCollider)

    fake.push(_make_config(map_model_path=str(path)))
    assert isinstance(fusion._collider, TrimeshCollider), "热重载后应切换到网格碰撞体"
