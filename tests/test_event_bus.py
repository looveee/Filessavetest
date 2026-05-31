# -*- coding: utf-8 -*-
"""
event_bus 的单元测试。

重点验证：
- 基本发布/订阅与 payload 透传
- **多线程高并发下不丢消息、无竞态**（多生产者 × 多订阅者）
- 订阅者异常被隔离并转投 SYS_ERROR
- 退订生效
- 非阻塞发布语义
"""

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import event_bus as eb  # noqa: E402
from event_bus import EventBus, Topic  # noqa: E402


@pytest.fixture()
def bus():
    """提供一个全新的 EventBus 实例，并在用例结束后优雅关闭。"""
    EventBus._reset_singleton()
    instance = EventBus(max_workers=8)
    yield instance
    instance.shutdown(wait=True)
    EventBus._reset_singleton()


def test_basic_pub_sub(bus):
    """单订阅者应收到与发布完全一致的 payload。"""
    received = []
    bus.subscribe(Topic.CV_UPDATE, lambda p: received.append(p))
    bus.publish(Topic.CV_UPDATE, {"x": 1, "y": 2})
    assert bus.join(timeout=2.0)
    assert received == [{"x": 1, "y": 2}]


def test_multiple_subscribers_same_topic(bus):
    """同一主题的多个订阅者都应被通知。"""
    hits = {"a": 0, "b": 0}
    lock = threading.Lock()

    def make(key):
        def _cb(_p):
            with lock:
                hits[key] += 1
        return _cb

    bus.subscribe(Topic.AUDIO_TICK, make("a"))
    bus.subscribe(Topic.AUDIO_TICK, make("b"))
    bus.publish(Topic.AUDIO_TICK, None)
    assert bus.join(timeout=2.0)
    assert hits == {"a": 1, "b": 1}


def test_no_message_loss_under_concurrency(bus):
    """
    核心用例：多生产者并发高频发布，验证零丢失、无竞态。

    4 个生产者线程各发布 1000 条，共 4000 条；单订阅者用线程安全计数器累加，
    并把每条 payload 收集进集合以校验完整性。
    """
    n_producers = 4
    per_producer = 1000
    total = n_producers * per_producer

    counter = {"n": 0}
    seen = set()
    lock = threading.Lock()

    def on_event(payload):
        # 故意制造极小的临界区，放大潜在竞态窗口。
        with lock:
            counter["n"] += 1
            seen.add(payload)

    bus.subscribe(Topic.CV_UPDATE, on_event)

    def producer(pid: int):
        for i in range(per_producer):
            bus.publish(Topic.CV_UPDATE, (pid, i))

    threads = [
        threading.Thread(target=producer, args=(pid,)) for pid in range(n_producers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 等待全部在途事件分发完成
    assert bus.join(timeout=10.0), "事件未在超时时间内全部分发完成"

    assert counter["n"] == total, f"消息丢失/重复：期望 {total}，实际 {counter['n']}"
    assert len(seen) == total, f"payload 完整性受损：唯一项 {len(seen)} != {total}"


def test_subscriber_exception_isolated_and_reported(bus):
    """某订阅者抛异常时：不影响其他订阅者，且转投 SYS_ERROR。"""
    good_hits = []
    errors = []

    def bad(_p):
        raise RuntimeError("boom")

    bus.subscribe(Topic.FUSION_RESULT, bad)
    bus.subscribe(Topic.FUSION_RESULT, lambda p: good_hits.append(p))
    bus.subscribe(Topic.SYS_ERROR, lambda p: errors.append(p))

    bus.publish(Topic.FUSION_RESULT, "payload-1")
    assert bus.join(timeout=2.0)
    # SYS_ERROR 是由分发线程二次发布的，需再等一轮分发完成
    assert bus.join(timeout=2.0)

    assert good_hits == ["payload-1"], "正常订阅者不应被异常订阅者牵连"
    assert len(errors) == 1
    assert errors[0]["source_topic"] == "FUSION_RESULT"


def test_sys_error_handler_exception_does_not_recurse(bus):
    """SYS_ERROR 订阅者自身抛异常时，不得二次转投造成递归风暴。"""
    calls = {"n": 0}
    lock = threading.Lock()

    def faulty_sys_handler(_p):
        with lock:
            calls["n"] += 1
        raise RuntimeError("sys handler failed")

    bus.subscribe(Topic.SYS_ERROR, faulty_sys_handler)
    bus.publish(Topic.SYS_ERROR, {"boom": True})
    assert bus.join(timeout=2.0)
    # 给潜在的（错误的）递归留出窗口
    time.sleep(0.2)
    assert bus.join(timeout=2.0)
    assert calls["n"] == 1, "SYS_ERROR 处理异常被二次转投，发生递归！"


def test_unsubscribe(bus):
    """退订后不应再收到事件。"""
    received = []
    cb = lambda p: received.append(p)  # noqa: E731
    bus.subscribe(Topic.CV_UPDATE, cb)
    bus.publish(Topic.CV_UPDATE, 1)
    assert bus.join(timeout=2.0)

    bus.unsubscribe(Topic.CV_UPDATE, cb)
    bus.publish(Topic.CV_UPDATE, 2)
    assert bus.join(timeout=2.0)

    assert received == [1], "退订后不应再收到新事件"


def test_publish_is_non_blocking(bus):
    """publish 不应被慢订阅者阻塞：入队即返回。"""
    release = threading.Event()

    def slow(_p):
        release.wait(timeout=5.0)  # 阻塞订阅者

    bus.subscribe(Topic.CV_UPDATE, slow)

    t0 = time.monotonic()
    bus.publish(Topic.CV_UPDATE, "x")
    elapsed = time.monotonic() - t0

    assert elapsed < 0.1, f"publish 被阻塞了 {elapsed:.3f}s，违反非阻塞语义"
    release.set()  # 放行慢订阅者，便于 fixture 干净关闭
