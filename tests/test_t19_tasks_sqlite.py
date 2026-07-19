"""T19 — TASKS 元数据迁移到 SQLite，验证跨进程/跨线程可见性。

覆盖 L1 生产 bug：gunicorn 4 worker 下 TASKS 内存字典跨进程不可见。
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time

import pytest

from termify.taskstore import (
    CACHE,
    TaskStore,
    cache_clear_all,
    cache_clear_task,
    cache_get,
    cache_key,
    cache_put,
    reset_store_for_tests,
)


@pytest.fixture(autouse=True)
def _isolate_taskstore(monkeypatch):
    """每个测试使用独立的 SQLite 数据库，避免相互污染。

    同时重置模块级 cache 和 store singleton，让每个测试都在干净的
    基础上运行。
    """
    cache_clear_all()
    reset_store_for_tests()
    yield
    cache_clear_all()
    reset_store_for_tests()


@pytest.fixture
def store(tmp_path):
    """创建指向临时数据库的 TaskStore 并初始化 schema。"""
    db_path = str(tmp_path / "test_tasks.db")
    s = TaskStore(db_path, ttl_seconds=3600)
    s.init_db()
    return s


# ---------------------------------------------------------------------------
# 测试 1：元数据持久化到 SQLite，新连接可读到
# ---------------------------------------------------------------------------


def test_tasks_metadata_persists_in_sqlite(store):
    """写入任务后，新开一个 sqlite3 连接查询，能查到的数据。"""
    task_id = "test-task-001"
    store.put(
        task_id,
        filepath="/tmp/test.png",
        original_size={"width": 1920, "height": 1080},
        target_size={"width": 80, "height": 24},
        frames_count=42,
        interval=0.1,
    )

    # 用全新的 sqlite3 连接（绕过 TaskStore）验证数据已落盘
    conn = sqlite3.connect(store.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    conn.close()

    assert row is not None, "新连接应能查到已写入的任务行"
    assert row["task_id"] == task_id
    assert row["filepath"] == "/tmp/test.png"
    assert row["original_size_w"] == 1920
    assert row["original_size_h"] == 1080
    assert row["target_size_w"] == 80
    assert row["target_size_h"] == 24
    assert row["frames_count"] == 42
    assert row["interval"] == pytest.approx(0.1)

    # 也应可通过 store.get() 读到
    task = store.get(task_id)
    assert task is not None
    assert task["filepath"] == "/tmp/test.png"


# ---------------------------------------------------------------------------
# 测试 2：跨线程并发安全（4 线程同时读写不同 task_id）
# ---------------------------------------------------------------------------


def test_tasks_cross_thread_safety(store):
    """4 个线程同时写/读不同 task_id，验证无竞态。"""
    errors: list[str] = []
    ready = threading.Barrier(4, timeout=10)

    def worker(worker_id: int) -> None:
        try:
            ready.wait()
            for i in range(5):
                tid = f"thread-{worker_id}-{i}"
                store.put(
                    tid,
                    filepath=f"/tmp/thread{worker_id}_{i}.png",
                    original_size=(100, 100),
                    target_size=(80, 24),
                    frames_count=i + 1,
                    interval=0.05,
                )
                # 立即读回验证
                task = store.get(tid)
                if task is None:
                    errors.append(f"worker {worker_id}: put {tid} 后 get 返回 None")
                elif task["frames_count"] != i + 1:
                    errors.append(
                        f"worker {worker_id}: 期望 frames_count={i+1}, "
                        f"实际={task['frames_count']}"
                    )
        except Exception as exc:
            errors.append(f"worker {worker_id}: {exc}")

    threads = [
        threading.Thread(target=worker, args=(i,)) for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, "\n".join(errors)

    # 所有 4×5=20 条任务都应可查询
    for w in range(4):
        for i in range(5):
            assert store.exists(f"thread-{w}-{i}"), f"thread-{w}-{i} 应存在"


# ---------------------------------------------------------------------------
# 测试 3：TTL 过期清理
# ---------------------------------------------------------------------------


def test_tasks_ttl_cleanup(store):
    """创建 task，验证过期后 sweep_expired 会删除。"""
    now = time.time()
    # 插入一个 TTL 还有 3600s 的有效任务
    store.put(
        "alive",
        filepath="/tmp/alive.png",
        original_size=(100, 100),
        target_size=(80, 24),
        ttl_seconds=3600,
        now=now,
    )
    # 插入一个 TTL 仅 1s 的任务，并让 now 已经过去 2s
    store.put(
        "expired",
        filepath="/tmp/expired.png",
        original_size=(100, 100),
        target_size=(80, 24),
        ttl_seconds=1,
        now=now - 2,  # 2 秒前创建，TTL=1 → 已过期
    )

    # 清理前两者都应存在
    assert store.exists("alive")
    assert store.exists("expired")

    removed = store.sweep_expired(now=now)
    assert removed >= 1, "至少 expired 任务应被清理"
    assert store.exists("alive"), "未过期任务不应被清理"
    assert not store.exists("expired"), "已过期任务应被删除"


# ---------------------------------------------------------------------------
# 测试 4：未知 task_id 返回 404
# ---------------------------------------------------------------------------


def test_tasks_get_or_404_returns_404_for_unknown():
    """未知 task_id → _task_get_or_404 返回 (None, (json_resp, 404))。

    这里通过直接测 store.get() 返回 None 并在应用层封装的逻辑来验证。
    不 import Flask app 避免触发 rate-limit 初始化。
    """
    # 直接测 store 层: 未知 id 返回 None
    import tempfile
    db_path = os.path.join(tempfile.mkdtemp(), "test404.db")
    s = TaskStore(db_path, ttl_seconds=3600)
    s.init_db()

    assert s.get("nonexistent-task-id") is None
    assert not s.exists("nonexistent-task-id")
    assert not s.exists("")

    # 验证已知 id 存在时 get 正常返回
    s.put("known", filepath="/tmp/x.png", original_size=(10, 10),
          target_size=(5, 5), frames_count=1, interval=0.1)
    assert s.get("known") is not None
    assert s.exists("known")


# ---------------------------------------------------------------------------
# 测试 5：cache 独立于元数据（模拟跨 worker 场景）
# ---------------------------------------------------------------------------


def test_tasks_cache_independent_per_worker(store):
    """元数据通过 SQLite 共享，但 cache 是本进程的独立 dict。

    模拟场景：worker A 写入元数据 + cache，worker B（另一连接）能读
    元数据但读不到 cache。这验证了 cache miss 不会导致错误。
    """
    task_id = "cross-worker-test"
    charset = "ascii"
    w, h = 80, 24
    key = cache_key(task_id, charset, w, h)

    # --- Worker A: 写入元数据 + 填充 cache ---
    store.put(
        task_id,
        filepath="/tmp/shared.png",
        original_size=(1920, 1080),
        target_size=(w, h),
        frames_count=10,
        interval=0.05,
    )
    dummy_seq = object()  # 模拟 FrameSequence
    cache_put(task_id, key, dummy_seq)
    assert cache_get(task_id, key) is dummy_seq

    # --- Worker B 模拟：清空 cache（模拟另一进程） ---
    # 另一进程的 CACHE dict 是空的，这里手动清空来模拟
    cache_clear_all()
    assert cache_get(task_id, key) is None, (
        "cache 清空后应 miss（模拟 worker B 冷启动）"
    )

    # 但元数据仍可通过 SQLite 读到
    task = store.get(task_id)
    assert task is not None, "元数据应跨 '进程' 可见"
    assert task["filepath"] == "/tmp/shared.png"
    assert task["frames_count"] == 10

    # cache miss 后重新计算并填入 → 新一轮 cache hit
    cache_put(task_id, key, "recomputed")
    assert cache_get(task_id, key) == "recomputed"
