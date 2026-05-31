# -*- coding: utf-8 -*-
"""
config_manager.py
================================================================================
FPS 多模态态势感知系统 —— 配置中心 (Configuration Hub)

设计目标
--------
1. **单例模式 (Singleton)**：全局唯一配置入口，任意模块 `ConfigManager()` 取到同一实例。
2. **热重载 (Hot-reload)**：基于 `watchdog` 监听配置目录，文件保存后自动刷新内存配置，
   业务进程无需重启。
3. **可观测性 (Observability)**：基于标准 `logging`，同时输出到终端 (INFO) 与
   `logs/app.log` (DEBUG)，完整记录加载与变更事件。
4. **健壮性 (Robustness)**：对文件缺失、YAML 语法错误、字段缺省等情形提供严格的
   异常捕获与 fallback 逻辑——任何异常都不会导致配置中心崩溃，旧配置始终可用。

线程安全
--------
配置的读取/替换通过 `threading.RLock` 保护；watchdog 在独立线程回调，
读写均加锁，保证业务线程读到的始终是一份自洽的快照。
================================================================================
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field, fields, is_dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional, get_type_hints

import yaml
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

# ---------------------------------------------------------------------------
# 路径常量：以本文件位置为锚点推导工程根目录，避免依赖运行时的 CWD。
# ---------------------------------------------------------------------------
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
_CONFIG_DIR: Path = _PROJECT_ROOT / "config"
_CONFIG_FILE: Path = _CONFIG_DIR / "default_config.yaml"
_LOG_DIR: Path = _PROJECT_ROOT / "logs"
_LOG_FILE: Path = _LOG_DIR / "app.log"


# ===========================================================================
#  一、配置数据模型 (Typed Config Schema)
#     使用 dataclass 提供类型提示、IDE 自动补全与默认值兜底。
# ===========================================================================
@dataclass
class AudioConfig:
    """音频提取模块配置。"""
    sample_rate: int = 48000        # 采样率 (Hz)
    channels: int = 2               # 声道数
    chunk_size: int = 1024          # 缓冲区块大小（帧）
    energy_threshold: float = 0.015  # 静音能量阈值
    vad_threshold: float = 0.6      # 活动检测置信度阈值


@dataclass
class RoiBBox:
    """小地图感兴趣区域 (ROI) 的像素边界框。"""
    x: int = 40   # 左上角 X 偏移 (px)
    y: int = 40   # 左上角 Y 偏移 (px)
    w: int = 320  # 宽度 (px)
    h: int = 320  # 高度 (px)


@dataclass
class CVConfig:
    """计算机视觉模块配置。"""
    fps_limit: int = 30                       # 视觉处理帧率上限
    roi_bbox: RoiBBox = field(default_factory=RoiBBox)  # 小地图 ROI


@dataclass
class FusionConfig:
    """多模态融合模块配置。"""
    tolerance_radius_meters: float = 1.5  # 融合容差半径 (米)
    min_confidence: float = 0.85          # 最小可信度阈值


@dataclass
class AppConfig:
    """顶层配置聚合根，对应整个 YAML 文档。"""
    audio: AudioConfig = field(default_factory=AudioConfig)
    cv: CVConfig = field(default_factory=CVConfig)
    fusion: FusionConfig = field(default_factory=FusionConfig)


# ===========================================================================
#  二、日志初始化
#     终端输出 INFO，文件 (含轮转) 记录 DEBUG，全局只初始化一次。
# ===========================================================================
def _build_logger() -> logging.Logger:
    """构造并返回配置中心专用 logger（幂等，可重复调用）。"""
    logger = logging.getLogger("config_manager")
    logger.setLevel(logging.DEBUG)

    # 防止重复挂载 handler（例如单例被多次实例化或模块被重复 import）。
    if logger.handlers:
        return logger

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 终端 handler：面向操作者，输出 INFO 及以上。
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)

    # 文件 handler：面向审计/排障，输出 DEBUG 及以上，并按大小轮转。
    file_handler = RotatingFileHandler(
        _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    logger.addHandler(console)
    logger.addHandler(file_handler)
    logger.propagate = False  # 避免日志冒泡到 root 造成重复输出
    return logger


_LOGGER: logging.Logger = _build_logger()


# ===========================================================================
#  三、YAML -> dataclass 反序列化工具
#     仅填充 schema 中声明的字段，未知字段被忽略并告警；缺失字段回退默认值。
# ===========================================================================
def _from_dict(cls: type, data: Optional[Dict[str, Any]]) -> Any:
    """将字典递归映射为指定 dataclass 实例，做严格字段过滤与缺省兜底。"""
    if not is_dataclass(cls):
        # 标量类型直接返回（由调用方保证语义正确）。
        return data

    payload: Dict[str, Any] = data if isinstance(data, dict) else {}
    kwargs: Dict[str, Any] = {}

    # 因启用了 `from __future__ import annotations`，dataclass 的 f.type 为字符串，
    # 需用 get_type_hints 解析为真实类型对象，才能正确识别嵌套 dataclass。
    hints = get_type_hints(cls)

    for f in fields(cls):
        if f.name not in payload:
            # 字段缺失：交由 dataclass 的 default / default_factory 兜底。
            continue
        raw_value = payload[f.name]
        field_type = hints.get(f.name, f.type)
        if is_dataclass(field_type):
            # 嵌套 dataclass（如 CVConfig.roi_bbox），递归构造。
            kwargs[f.name] = _from_dict(field_type, raw_value)  # type: ignore[arg-type]
        else:
            kwargs[f.name] = raw_value

    # 对 YAML 中存在但 schema 未声明的多余字段进行告警，便于及早发现拼写错误。
    unknown = set(payload.keys()) - {f.name for f in fields(cls)}
    if unknown:
        _LOGGER.warning("配置节 [%s] 含未知字段，已忽略: %s", cls.__name__, sorted(unknown))

    return cls(**kwargs)


# ===========================================================================
#  四、watchdog 文件事件处理器
#     仅关注目标配置文件，带去抖动 (debounce) 以合并编辑器的多次写事件。
# ===========================================================================
class _ConfigFileEventHandler(FileSystemEventHandler):
    """监听 default_config.yaml 的修改/创建/移动事件并触发重载。"""

    def __init__(self, manager: "ConfigManager", debounce_seconds: float = 0.4) -> None:
        super().__init__()
        self._manager = manager
        self._debounce = debounce_seconds
        self._last_trigger: float = 0.0
        self._lock = threading.Lock()

    def _matches(self, raw_path: Any) -> bool:
        """判断事件路径是否为我们关心的配置文件。"""
        if not raw_path:
            return False
        try:
            return Path(str(raw_path)).resolve() == _CONFIG_FILE.resolve()
        except OSError:
            return False

    def _maybe_reload(self, event: FileSystemEvent) -> None:
        """去抖后触发一次热重载。"""
        # 编辑器保存常伴随 "临时文件 -> rename" 序列，需同时检查 src 与 dest 路径。
        dest = getattr(event, "dest_path", None)
        if not (self._matches(event.src_path) or self._matches(dest)):
            return

        now = time.monotonic()
        with self._lock:
            if now - self._last_trigger < self._debounce:
                return  # 处于去抖窗口内，丢弃这次抖动事件
            self._last_trigger = now

        _LOGGER.info("检测到配置文件变更事件 (%s)，准备热重载……", event.event_type)
        self._manager.reload()

    # watchdog 回调入口 —— 覆盖关注的事件类型。
    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe_reload(event)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe_reload(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe_reload(event)


# ===========================================================================
#  五、配置中心单例 (ConfigManager)
# ===========================================================================
class ConfigManager:
    """
    线程安全的单例配置中心。

    用法::

        cfg = ConfigManager()          # 任意位置获取同一实例
        rate = cfg.config.audio.sample_rate
        cfg.start_watching()           # 开启热重载（可选）
        ...
        cfg.stop_watching()            # 退出前优雅停止
    """

    # ---- 单例机制 ----
    _instance: Optional["ConfigManager"] = None
    _singleton_lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "ConfigManager":
        # 双重检查锁定 (Double-Checked Locking)，保证多线程下仅创建一个实例。
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # 借助标志位保证 __init__ 的重负载逻辑只执行一次。
        if getattr(self, "_initialized", False):
            return

        self._state_lock: threading.RLock = threading.RLock()
        self._config: AppConfig = AppConfig()  # 先以全默认值兜底，确保任何时刻 config 可用
        self._observer: Optional[BaseObserver] = None
        self._watching: bool = False
        self._initialized: bool = True

        _LOGGER.info("ConfigManager 初始化，工程根目录: %s", _PROJECT_ROOT)
        # 首次主动加载磁盘配置（失败时保留默认值）。
        self.reload()

    # -----------------------------------------------------------------
    # 5.1 配置读取
    # -----------------------------------------------------------------
    @property
    def config(self) -> AppConfig:
        """返回当前内存中配置的**深拷贝快照**，避免调用方意外修改全局状态。"""
        with self._state_lock:
            return copy.deepcopy(self._config)

    # -----------------------------------------------------------------
    # 5.2 配置加载 / 热重载
    # -----------------------------------------------------------------
    def reload(self) -> bool:
        """
        从磁盘重新加载配置文件并原子替换内存配置。

        :return: 加载成功返回 True；发生任何异常时回退（保留旧配置）并返回 False。
        """
        try:
            raw = self._read_yaml(_CONFIG_FILE)
            new_config = _from_dict(AppConfig, raw)
        except FileNotFoundError:
            _LOGGER.error("配置文件缺失: %s —— 沿用当前内存配置 (fallback)。", _CONFIG_FILE)
            return False
        except yaml.YAMLError as exc:
            _LOGGER.error("YAML 语法解析失败: %s —— 沿用当前内存配置 (fallback)。", exc)
            return False
        except (TypeError, ValueError) as exc:
            _LOGGER.error("配置字段类型/取值非法: %s —— 沿用当前内存配置 (fallback)。", exc)
            return False
        except Exception as exc:  # noqa: BLE001  兜底：配置中心绝不因加载异常而崩溃
            _LOGGER.exception("加载配置时发生未预期异常: %s —— 沿用当前内存配置。", exc)
            return False

        # 原子替换：在锁内一次性切换引用，业务线程不会读到半成品。
        with self._state_lock:
            old_config = self._config
            self._config = new_config

        changed = old_config != new_config
        if changed:
            _LOGGER.info("配置已热更新 ✔  %s", self._summarize(new_config))
        else:
            _LOGGER.info("配置重载完成，内容无变化。")
        return True

    @staticmethod
    def _read_yaml(path: Path) -> Dict[str, Any]:
        """读取并解析 YAML 文件，返回顶层字典。"""
        if not path.exists():
            raise FileNotFoundError(str(path))
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if data is None:
            # 空文件：视为合法的空配置，全部走默认值。
            _LOGGER.warning("配置文件为空: %s —— 将使用全部默认值。", path)
            return {}
        if not isinstance(data, dict):
            raise TypeError(f"配置根节点必须为映射(dict)，实际为 {type(data).__name__}")
        return data

    @staticmethod
    def _summarize(cfg: AppConfig) -> str:
        """生成一行式配置摘要，便于日志快速核对关键项。"""
        return (
            f"audio.sample_rate={cfg.audio.sample_rate}, "
            f"cv.fps_limit={cfg.cv.fps_limit}, "
            f"fusion.min_confidence={cfg.fusion.min_confidence}"
        )

    # -----------------------------------------------------------------
    # 5.3 热重载监听生命周期
    # -----------------------------------------------------------------
    def start_watching(self) -> None:
        """启动 watchdog，开始监听 config 目录的文件变更。"""
        with self._state_lock:
            if self._watching:
                _LOGGER.debug("热重载监听已在运行，忽略重复启动。")
                return

            if not _CONFIG_DIR.exists():
                _LOGGER.error("配置目录不存在，无法启动监听: %s", _CONFIG_DIR)
                return

            handler = _ConfigFileEventHandler(self)
            observer = Observer()
            # 仅递归监听 config 目录即可覆盖目标文件（recursive=False 降低开销）。
            observer.schedule(handler, str(_CONFIG_DIR), recursive=False)
            observer.daemon = True
            observer.start()

            self._observer = observer
            self._watching = True
            _LOGGER.info("已启动配置热重载监听，目标目录: %s", _CONFIG_DIR)

    def stop_watching(self) -> None:
        """停止 watchdog 监听并释放线程资源（幂等）。"""
        with self._state_lock:
            if not self._watching or self._observer is None:
                return
            observer = self._observer
            self._observer = None
            self._watching = False

        # 在锁外执行可能阻塞的 join，避免长时间持锁。
        observer.stop()
        observer.join(timeout=5.0)
        _LOGGER.info("配置热重载监听已停止。")

    # -----------------------------------------------------------------
    # 5.4 上下文管理器支持：with ConfigManager() as cfg: ...
    # -----------------------------------------------------------------
    def __enter__(self) -> "ConfigManager":
        self.start_watching()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.stop_watching()


# ===========================================================================
#  六、模块级便捷入口
# ===========================================================================
def get_config() -> AppConfig:
    """便捷函数：返回当前配置快照（等价于 ConfigManager().config）。"""
    return ConfigManager().config


# ===========================================================================
#  七、手动验证入口 (Demo)
#     直接运行本文件即可观察热重载：启动后修改 config/default_config.yaml 并保存。
# ===========================================================================
if __name__ == "__main__":
    manager = ConfigManager()
    manager.start_watching()
    _LOGGER.info("当前配置摘要: %s", manager._summarize(manager.config))
    _LOGGER.info("演示模式：请修改 config/default_config.yaml 并保存以观察热重载，Ctrl+C 退出。")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _LOGGER.info("收到中断信号，正在退出……")
    finally:
        manager.stop_watching()
