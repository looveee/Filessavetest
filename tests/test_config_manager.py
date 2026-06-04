# -*- coding: utf-8 -*-
"""
config_manager 的单元测试。

覆盖：
- 单例语义
- 默认值兜底
- YAML 正常加载
- 语法错误 / 文件缺失时的 fallback（不抛异常、保留旧配置）
"""

import sys
import time
from pathlib import Path

import pytest

# 将 src 加入导入路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config_manager as cm  # noqa: E402


@pytest.fixture()
def manager(monkeypatch, tmp_path):
    """提供一个指向临时配置文件的全新 ConfigManager 实例。"""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "default_config.yaml"

    # 重定向模块级路径常量到临时目录
    monkeypatch.setattr(cm, "_CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(cm, "_CONFIG_FILE", cfg_file)

    # 重置单例，避免跨用例污染
    cm.ConfigManager._instance = None

    def _make(yaml_text: str | None = None) -> cm.ConfigManager:
        if yaml_text is not None:
            cfg_file.write_text(yaml_text, encoding="utf-8")
        cm.ConfigManager._instance = None
        return cm.ConfigManager()

    yield _make
    cm.ConfigManager._instance = None


def test_singleton_identity():
    """两次实例化应返回同一对象。"""
    a = cm.ConfigManager()
    b = cm.ConfigManager()
    assert a is b


def test_defaults_when_file_missing(manager):
    """文件缺失时应回退到全默认值且不抛异常。"""
    inst = manager(None)  # 不写文件
    cfg = inst.config
    assert cfg.audio.sample_rate == 48000
    assert cfg.cv.fps_limit == 30
    assert cfg.fusion.min_confidence == 0.85


def test_load_valid_yaml(manager):
    """合法 YAML 应被正确解析并覆盖默认值。"""
    inst = manager("""
audio:
  sample_rate: 44100
  channels: 1
cv:
  fps_limit: 60
  roi_bbox: {x: 10, y: 20, w: 100, h: 200}
fusion:
  min_confidence: 0.5
""")
    cfg = inst.config
    assert cfg.audio.sample_rate == 44100
    assert cfg.audio.channels == 1
    assert cfg.cv.fps_limit == 60
    assert cfg.cv.roi_bbox.w == 100
    assert cfg.fusion.min_confidence == 0.5
    # 未指定字段仍走默认值
    assert cfg.audio.chunk_size == 1024


def test_fallback_on_bad_yaml(manager):
    """语法错误的 YAML 不应崩溃，应保留先前的有效配置。"""
    inst = manager("audio:\n  sample_rate: 32000\n")
    assert inst.config.audio.sample_rate == 32000

    # 写入非法 YAML 后 reload 应返回 False 且保留旧值
    cm._CONFIG_FILE.write_text("audio: : : [unbalanced", encoding="utf-8")
    ok = inst.reload()
    assert ok is False
    assert inst.config.audio.sample_rate == 32000  # 旧配置保留


def test_config_is_snapshot(manager):
    """config 属性应返回深拷贝，修改它不影响内部状态。"""
    inst = manager("audio:\n  sample_rate: 16000\n")
    snap = inst.config
    snap.audio.sample_rate = 999
    assert inst.config.audio.sample_rate == 16000


def test_hot_reload_via_watchdog(manager):
    """端到端验证 watchdog 热重载（文件修改后内存配置自动刷新）。"""
    inst = manager("audio:\n  sample_rate: 8000\n")
    inst.start_watching()
    try:
        assert inst.config.audio.sample_rate == 8000
        cm._CONFIG_FILE.write_text("audio:\n  sample_rate: 22050\n", encoding="utf-8")

        # 轮询等待热重载生效（最多 ~5s），避免依赖固定 sleep。
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if inst.config.audio.sample_rate == 22050:
                break
            time.sleep(0.1)
        assert inst.config.audio.sample_rate == 22050
    finally:
        inst.stop_watching()
