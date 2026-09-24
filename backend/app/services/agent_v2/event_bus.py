"""Agent v2 事件总线（PRD 评审 D1）：同一 run 的多连接事件分发与断线补发。

单 worker 部署的进程内实现：run_id → 订阅者队列列表。发布者（图执行线程）
publish；消费者（SSE /stream）subscribe_with_replay 先补发缓冲再收增量。
跨进程/多 worker 部署时替换为 Redis Pub/Sub，接口不变。
消费者长期不取时环形缓冲丢最旧——DB 状态始终是权威，逐字 delta 不承诺补发。
"""

import threading
from collections import deque

_MAX_BUFFER = 500

_lock = threading.Lock()
_subscribers: dict[int, list[deque]] = {}


def subscribe_with_replay(run_id: int) -> deque:
    """注册订阅者；若已有其他订阅者，把其缓冲复制一份（重连补发既有状态事件）。"""
    with _lock:
        existing = _subscribers.get(run_id, [])
        buf = deque(maxlen=_MAX_BUFFER)
        if existing:
            buf.extend(existing[0])
        _subscribers.setdefault(run_id, []).append(buf)
    return buf


def unsubscribe(run_id: int, buf: deque) -> None:
    with _lock:
        subs = _subscribers.get(run_id, [])
        if buf in subs:
            subs.remove(buf)
        if run_id in _subscribers and not _subscribers[run_id]:
            del _subscribers[run_id]


def publish(run_id: int, event: dict) -> None:
    """向该 run 的全部订阅者投递事件（无订阅者时静默——DB 状态是权威）。"""
    with _lock:
        subs = list(_subscribers.get(run_id, []))
    for buf in subs:
        buf.append(event)


def subscriber_count(run_id: int) -> int:
    with _lock:
        return len(_subscribers.get(run_id, []))
