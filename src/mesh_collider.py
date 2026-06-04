# -*- coding: utf-8 -*-
"""
mesh_collider.py
================================================================================
FPS 多模态态势感知系统 —— 真实 3D 网格碰撞体 (Trimesh Collider)

放弃"无限平地"假设：用 trimesh 对真实地图网格 (.obj/.stl/...) 做射线求交，
让声源射线真正撞在掩体/墙面上，并剔除穿墙打到背面的远端交点。

设计要点
--------
1. **同接口**：实现 `MockMapCollider.raycast(origin, direction_unit) -> CollisionHit | None`，
   可与 FixedDistanceWallCollider 无缝互换。
2. **模型只加载一次**：网格在构造时载入并缓存；FusionEngine 仅在初始化/热重载时重建实例。
3. **最近交点**：`intersects_location` 可能返回多个交点，取沿射线正方向、距原点最近者
   （负方向/背面交点被剔除）。
4. **加速结构**：trimesh 在装有 `rtree`/`pyembree` 时自动启用空间索引，大幅加速求交。
================================================================================
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

# 复用 fusion_engine 中定义的碰撞接口与命中数据结构，保证类型一致、可互换。
from fusion_engine import CollisionHit, MockMapCollider

try:
    import trimesh  # type: ignore
except Exception:  # pragma: no cover - 依赖缺失时友好降级
    trimesh = None  # type: ignore

_LOGGER: logging.Logger = logging.getLogger("mesh_collider")
_EPS: float = 1e-9


class TrimeshCollider(MockMapCollider):
    """基于 trimesh 的真实 3D 网格射线碰撞体。"""

    def __init__(self, model_path: str | Path, material_type: str = "mesh") -> None:
        """
        :param model_path: 本地 3D 模型文件路径 (.obj/.stl/.ply/.glb 等 trimesh 支持格式)。
        :param material_type: 命中点上报的材质标签。
        :raises RuntimeError: trimesh 未安装。
        :raises FileNotFoundError: 模型文件不存在。
        :raises ValueError: 模型加载后不含可用三角面。
        """
        if trimesh is None:
            raise RuntimeError("trimesh 未安装，无法使用 TrimeshCollider（pip install trimesh rtree）")

        self._path = Path(model_path)
        self._material = material_type
        if not self._path.exists():
            raise FileNotFoundError(f"地图模型文件不存在: {self._path}")

        mesh = trimesh.load(self._path, force="mesh")
        # 某些容器格式会载入为 Scene，统一收敛为单一 Trimesh。
        if isinstance(mesh, trimesh.Scene):  # pragma: no cover - 取决于具体文件
            mesh = mesh.dump(concatenate=True)
        if not isinstance(mesh, trimesh.Trimesh) or mesh.faces.shape[0] == 0:
            raise ValueError(f"模型不含可用三角面: {self._path}")

        self._mesh: "trimesh.Trimesh" = mesh
        # 触发并缓存射线求交器（装有 rtree/pyembree 时为加速版本）。
        self._ray = self._mesh.ray
        _LOGGER.info(
            "TrimeshCollider 已加载: %s (顶点=%d, 面=%d, 求交器=%s)",
            self._path.name, len(self._mesh.vertices), len(self._mesh.faces),
            type(self._ray).__name__,
        )

    @property
    def path(self) -> Path:
        """已加载的模型路径。"""
        return self._path

    def raycast(self, origin: np.ndarray, direction_unit: np.ndarray) -> Optional[CollisionHit]:
        """从 origin 沿 direction_unit 投射射线，返回最近的正向交点或 None。"""
        origin = np.asarray(origin, dtype=np.float64).reshape(3)
        direction = np.asarray(direction_unit, dtype=np.float64).reshape(3)

        norm = float(np.linalg.norm(direction))
        if norm < _EPS:
            return None
        direction = direction / norm  # 归一化，保证"距离"以米为单位

        # trimesh 期望批量输入：(n,3)。
        locations, index_ray, index_tri = self._ray.intersects_location(
            ray_origins=origin[None, :],
            ray_directions=direction[None, :],
            multiple_hits=True,
        )
        if locations is None or len(locations) == 0:
            return None

        # 计算每个交点沿射线正方向的有符号距离，剔除背向（负距离）交点。
        deltas = locations - origin[None, :]
        signed = deltas @ direction               # 在射线方向上的投影 = 有符号距离
        forward = signed > _EPS
        if not np.any(forward):
            return None

        cand_locs = locations[forward]
        cand_dist = signed[forward]
        nearest = int(np.argmin(cand_dist))       # 最近的正向交点（剔除穿墙背面点）

        point = cand_locs[nearest].astype(np.float64)
        distance = float(cand_dist[nearest])
        return CollisionHit(point=point, distance=distance, material_type=self._material)
