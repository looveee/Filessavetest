# -*- coding: utf-8 -*-
"""
event_bus.py
================================================================================
FPS 多模态态势感知系统 —— 底层事件总线 (Event Bus)

定位
----
一个极其轻量、线程安全、基于发布/订阅 (Pub-Sub) 模式的进程内事件总线，
作为各模态模块 (CV / Audio / Fusion) 之间唯一的解耦通信通道。

设计要点
--------
1. **非阻塞发布 (Non-blocking publish)**：`publish()` 仅将事件压入内部队列即返回，
   不会因订阅者处理缓慢而拖慢高频生产者（如 30FPS 的视觉抓取线程）。
2. **异步分发 (Async dispatch)**：独立的 dispatcher 守护线程消费队列，
   通过 `ThreadPoolExecutor` 并发地把事件投递给各订阅者，
   单个慢订阅者不会阻塞其他订阅者。
3. **线程安全 (Thread-safe)**：订阅表读写以 `RLock` 保护；分发时对订阅者列表
   做快照拷贝，避免迭代期间被并发修改。
4. **故障隔离 (Fault isolation)**：任一订阅者抛出的异常都会被捕获并记录，
   且自动转投 `SYS_ERROR` 主题（对 `SYS_ERROR` 自身的异常不再二次转投，防止递归风暴）。
================================================================================
"""

from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

_LOGGER: logging.Logger = logging.getLogger("event_bus")


# ===========================================================================
#  一、主题枚举 (Topic)
# ===========================================================================
class Topic(Enum):
    """事件总线的标准主题。使用 auto() 保证取值唯一，订阅/发布以枚举成员为键。"""

    CV_UPDATE = auto()      # 视觉坐标更新（CVEngine 产出）
    AUDIO_TICK = auto()     # 音频切片产生（AudioEngine 产出）
    FUSION_RESULT = auto()  # 多模态融合推导结果（FusionEngine 产出）
    SYS_ERROR = auto()      # 系统异常 / 故障上报


# 订阅者回调签名：接收任意 payload，返回值被忽略。
Callback = Callable[[Any], None]


@dataclass(frozen=True)
class _Event:
    """内部事件信封：在队列中流转的最小单元。"""
    topic: Topic
    payload: Any


# ===========================================================================
#  二、事件总线单例 (EventBus)
# ===========================================================================
class EventBus:
    """
    线程安全的单例发布/订阅事件总线。

    用法::

        bus = EventBus()
        bus.subscribe(Topic.CV_UPDATE, on_cv_update)
        bus.publish(Topic.CV_UPDATE, payload)   # 立即返回，异步分发
        ...
        bus.shutdown()                          # 进程退出前优雅关闭
    """

    _instance: Optional["EventBus"] = None
    _singleton_lock: threading.Lock = threading.Lock()

    def __new__(cls, *args: Any, **kwargs: Any) -> "EventBus":
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, max_workers: int = 8, queue_maxsize: int = 0) -> None:
        # 幂等初始化：单例只装配一次。
        if getattr(self, "_initialized", False):
            return

        # 订阅表：topic -> 回调列表。以 RLock 保护读写。
        self._subscribers: Dict[Topic, List[Callback]] = {t: [] for t in Topic}
        self._sub_lock: threading.RLock = threading.RLock()

        # 事件队列与分发线程池。
        self._queue: "queue.Queue[Optional[_Event]]" = queue.Queue(maxsize=queue_maxsize)
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="ebus-worker"
        )
        self._running: bool = True
        self._dispatcher: threading.Thread = threading.Thread(
            target=self._dispatch_loop, name="ebus-dispatcher", daemon=True
        )
        self._initialized: bool = True
        self._dispatcher.start()
        _LOGGER.info("EventBus 已启动 (max_workers=%d)。", max_workers)

    # -----------------------------------------------------------------
    # 2.1 订阅 / 退订
    # -----------------------------------------------------------------
    def subscribe(self, topic: Topic, callback: Callback) -> None:
        """
        订阅指定主题。同一回调对同一主题重复订阅将被忽略（幂等）。

        :param topic: 目标主题枚举。
        :param callback: 形如 ``fn(payload) -> None`` 的可调用对象。
        """
        if not isinstance(topic, Topic):
            raise TypeError(f"topic 必须为 Topic 枚举，实际为 {type(topic).__name__}")
        if not callable(callback):
            raise TypeError("callback 必须为可调用对象")

        with self._sub_lock:
            subs = self._subscribers[topic]
            if callback not in subs:
                subs.append(callback)
                _LOGGER.debug("新增订阅 topic=%s，当前订阅者数=%d", topic.name, len(subs))

    def unsubscribe(self, topic: Topic, callback: Callback) -> None:
        """退订指定主题的某个回调（不存在时静默忽略）。"""
        with self._sub_lock:
            subs = self._subscribers.get(topic, [])
            if callback in subs:
                subs.remove(callback)
                _LOGGER.debug("移除订阅 topic=%s，剩余订阅者数=%d", topic.name, len(subs))

    # -----------------------------------------------------------------
    # 2.2 发布（非阻塞）
    # -----------------------------------------------------------------
    def publish(self, topic: Topic, payload: Any) -> None:
        """
        发布事件。仅将事件入队后立即返回，绝不等待订阅者处理。

        :param topic: 目标主题枚举。
        :param payload: 任意事件负载。
        """
        if not isinstance(topic, Topic):
            raise TypeError(f"topic 必须为 Topic 枚举，实际为 {type(topic).__name__}")
        if not self._running:
            _LOGGER.warning("EventBus 已关闭，丢弃发布到 %s 的事件。", topic.name)
            return
        # put 不阻塞（默认无界队列）；即便有界队列满，也以异常形式快速失败而非静默丢弃。
        self._queue.put(_Event(topic=topic, payload=payload))

    # -----------------------------------------------------------------
    # 2.3 分发循环（dispatcher 线程）
    # -----------------------------------------------------------------
    def _dispatch_loop(self) -> None:
        """后台守护线程：持续消费队列并把事件并发投递给订阅者。"""
        while True:
            event = self._queue.get()
            try:
                if event is None:
                    # 收到哨兵，退出循环（shutdown 触发）。
                    break
                self._deliver(event)
            finally:
                self._queue.task_done()

    def _deliver(self, event: _Event) -> None:
        """把单个事件并发投递给该主题的全部订阅者，并等待本批完成。"""
        with self._sub_lock:
            # 快照拷贝，避免迭代期间订阅表被并发修改。
            subscribers = list(self._subscribers.get(event.topic, ()))

        if not subscribers:
            return

        futures: List[Future] = [
            self._executor.submit(self._invoke, cb, event) for cb in subscribers
        ]
        # 等待本事件的所有订阅者执行完毕，使 join() 语义精确（无在途遗留）。
        for fut in futures:
            fut.result()  # _invoke 内部已吞掉异常，这里不会抛出

    def _invoke(self, callback: Callback, event: _Event) -> None:
        """在工作线程中安全调用单个订阅者，隔离并上报其异常。"""
        try:
            callback(event.payload)
        except Exception as exc:  # noqa: BLE001  故障隔离：单个订阅者异常不得污染总线
            _LOGGER.exception("订阅者处理 topic=%s 时抛出异常: %s", event.topic.name, exc)
            # 防递归：SYS_ERROR 自身的处理异常不再二次转投。
            if event.topic is not Topic.SYS_ERROR:
                self.publish(
                    Topic.SYS_ERROR,
                    {"source_topic": event.topic.name, "error": repr(exc)},
                )

    # -----------------------------------------------------------------
    # 2.4 同步与关闭
    # -----------------------------------------------------------------
    def join(self, timeout: Optional[float] = None) -> bool:
        """
        阻塞直到当前已入队的事件全部分发完成（主要用于测试同步）。

        :return: 队列清空返回 True；若指定 timeout 超时仍未清空返回 False。
        """
        if timeout is None:
            self._queue.join()
            return True
        # queue.join 不支持 timeout，这里基于未完成计数做有界轮询等待。
        import time

        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks > 0:  # type: ignore[attr-defined]
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)
        return True

    def shutdown(self, wait: bool = True) -> None:
        """优雅关闭总线：停止接收新事件，排空队列并回收线程资源（幂等）。"""
        with self._singleton_lock:
            if not self._running:
                return
            self._running = False

        if wait:
            self._queue.join()      # 等待在途事件分发完成
        self._queue.put(None)       # 投递哨兵唤醒 dispatcher 退出
        self._dispatcher.join(timeout=5.0)
        self._executor.shutdown(wait=wait)
        _LOGGER.info("EventBus 已关闭。")

    # -----------------------------------------------------------------
    # 2.5 测试辅助：重置单例（仅供测试夹具使用）
    # -----------------------------------------------------------------
    @classmethod
    def _reset_singleton(cls) -> None:
        """销毁当前单例（不负责 shutdown，调用方需自行处理旧实例）。"""
        with cls._singleton_lock:
            cls._instance = None
