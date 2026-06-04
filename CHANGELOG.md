# 变更日志 (Changelog)

本项目的所有重要变更都记录在此文件中。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/)，版本号遵循语义化版本。

---

## [1.0.0] — 2026-06-04 · 🚀 Project Echo · Sprint 完美落幕

7 天极限冲刺，从零构建工业级 FPS 多模态态势感知系统。**55 项单元测试全绿**，
代码经 `ruff` + `black` 全量对齐，TODO/FIXME 清零，端到端链路（含 Web 雷达、录制回放、
真实 3D 网格碰撞）均经真实运行验证。

### Sprint 里程碑

- **Day 1 · 配置中心** — 单例 + watchdog 热重载；`get_type_hints` 修复嵌套 dataclass
  反射 bug；编辑器原子保存去抖。
- **Day 2 · 事件总线 + 视觉引擎** — 非阻塞发布 + 线程池异步分发 + `SYS_ERROR` 防递归风暴；
  mss 高性能 ROI 抓取 + 精确帧率补偿（实测 1s@30FPS = 30 帧）。
- **Day 3 · 音频引擎 + VAD** — WASAPI 环回采集 + 能量端点检测状态机 + pre-roll 起音回补。
  经架构评审加固三项致命隐患：①音频切片数据别名竞态（强制 `copy`）；②采样参数热切换导致
  的状态机撕裂（边界强制闭合补发 `is_end`）；③pre-roll 语义（独立 `vad_preroll_chunks`）。
- **Day 4 · 融合引擎 (FusionCore)** — 时间轴缝合 + GCC-PHAT 声源测角 + 射线投射。
  修复置信度标定导致的"死管道"：放弃对带宽敏感的 PHAT 峰值显著度，改用有界、稳健的
  时域归一化互相关系数。
- **Day 5 · Web 雷达看板** — FastAPI + WebSocket + 单文件 Canvas 雷达（自机箭头、敌人余辉
  残影、距离环）。有界队列背压解耦同步总线回调与异步 WebSocket 推送。
- **Day 6 · 录制 / 回放** — JSONL 异步落盘（剥离音频波形仅留时序元数据）；回放采用绝对时间
  调度根除累积漂移；支持变速 (`--speed`) 复盘。
- **Day 7 · 真实 3D 网格碰撞** — trimesh 射线求交，取最近正向交点（剔除穿墙背面点）；
  网格模型只加载一次；加载失败优雅回退假想墙。配套生成专业 `README.md`。

### 收尾

- 建立 `pyproject.toml` 统一格式规范（black / ruff，line-length=100）。
- `scripts/capture_radar.py`：无头 Chromium 截图脚本，生成 `docs/radar_demo.png` 可视化凭证。
- PR #3 "Project Echo" 完成元数据包装（架构总览 + 里程碑 + 测试矩阵）。

### 模块清单

| 模块 | 文件 | 职责 |
|---|---|---|
| 配置中心 | `src/config_manager.py` | 单例 + 热重载 |
| 事件总线 | `src/event_bus.py` | 线程安全 Pub/Sub |
| 视觉引擎 | `src/cv_engine.py` | 屏幕捕获 → 坐标 |
| 音频引擎 | `src/audio_engine.py` | 环回采集 + VAD 切片 |
| 融合引擎 | `src/fusion_engine.py` | GCC-PHAT + 射线投射 |
| 网格碰撞 | `src/mesh_collider.py` | trimesh 真实 3D 求交 |
| Web 看板 | `src/web_server.py` | FastAPI + WebSocket + Canvas |
| 录制器 | `src/event_logger.py` | JSONL 异步落盘 |
| 回放器 | `src/event_replayer.py` | 绝对时间调度重放 |
| 启动器 | `main_demo.py` | 上帝级联调编排 |
