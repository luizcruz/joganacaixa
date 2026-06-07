"""Operation lifecycle manager with pause/resume/cancel support.

Each long-running store or recover call is tracked as an Operation.
Threads (uploads/downloads) check the operation's pause and cancel
events periodically so the UI can halt or stop them.

SSE listeners receive JSON-serialisable dicts via asyncio queues that
are bridged from worker threads using the stored event-loop reference.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from enum import Enum
from typing import Any


class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    NO_CONNECTION = "no_connection"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL = {Status.COMPLETED, Status.FAILED, Status.CANCELLED}


class Operation:
    """Single tracked operation (store or recover)."""

    def __init__(self, op_id: str, op_type: str, label: str = "") -> None:
        self.id = op_id
        self.type = op_type          # "store" | "recover"
        self.label = label           # filename or package_id
        self.status = Status.PENDING
        self.progress = 0            # 0–100
        self.transferred = 0         # bytes
        self.total = 0               # bytes (0 = unknown)
        self.error: str | None = None
        self.result: dict | None = None   # set on completion
        self.created_at = time.time()
        self.updated_at = time.time()

        # Control events (thread-safe)
        self._pause = threading.Event()
        self._pause.set()            # starts unpaused
        self._cancel = threading.Event()

        # SSE subscribers: list of (asyncio.AbstractEventLoop, asyncio.Queue)
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._subs_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Control API (called from HTTP handlers)
    # ------------------------------------------------------------------

    def pause(self) -> None:
        if self.status not in _TERMINAL:
            self._pause.clear()
            self._set_status(Status.PAUSED)

    def resume(self) -> None:
        if self.status in (Status.PAUSED, Status.NO_CONNECTION):
            self._set_status(Status.RUNNING)
            self._pause.set()

    def cancel(self) -> None:
        if self.status not in _TERMINAL:
            self._cancel.set()
            self._pause.set()   # unblock any wait
            self._set_status(Status.CANCELLED)

    # ------------------------------------------------------------------
    # Worker API (called from upload/download threads)
    # ------------------------------------------------------------------

    def check(self) -> bool:
        """Block while paused; return False if cancelled."""
        self._pause.wait()
        return not self._cancel.is_set()

    def mark_running(self) -> None:
        self._set_status(Status.RUNNING)

    def mark_no_connection(self) -> None:
        """Called by worker on network failure; auto-retried externally."""
        if self.status not in _TERMINAL | {Status.PAUSED}:
            self._pause.clear()
            self._set_status(Status.NO_CONNECTION)

    def mark_completed(self, result: dict | None = None) -> None:
        self.result = result
        self._set_status(Status.COMPLETED, progress=100)

    def mark_failed(self, error: str) -> None:
        self.error = error
        self._set_status(Status.FAILED)

    def update_progress(self, transferred: int, total: int = 0) -> None:
        self.transferred = transferred
        if total:
            self.total = total
        if self.total > 0:
            self.progress = min(99, int(100 * self.transferred / self.total))
        self.updated_at = time.time()
        self._broadcast("progress")

    # ------------------------------------------------------------------
    # SSE subscription
    # ------------------------------------------------------------------

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self._subs_lock:
            self._subs.append((loop, q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._subs_lock:
            self._subs = [(l, sq) for l, sq in self._subs if sq is not q]

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "status": self.status.value,
            "progress": self.progress,
            "transferred": self.transferred,
            "total": self.total,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _set_status(self, status: Status, progress: int | None = None) -> None:
        self.status = status
        if progress is not None:
            self.progress = progress
        self.updated_at = time.time()
        self._broadcast("status")

    def _broadcast(self, event: str) -> None:
        payload = self.to_dict()
        payload["event"] = event
        dead: list[tuple] = []
        with self._subs_lock:
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(q.put_nowait, payload)
            except Exception:
                dead.append((loop, q))
        if dead:
            with self._subs_lock:
                self._subs = [(l, sq) for l, sq in self._subs if (l, sq) not in dead]


class OperationRegistry:
    """In-memory store of all active and recent operations."""

    _MAX_COMPLETED_AGE = 3600  # seconds to keep completed ops

    def __init__(self) -> None:
        self._ops: dict[str, Operation] = {}
        self._lock = threading.Lock()

    def create(self, op_type: str, label: str = "") -> Operation:
        op = Operation(uuid.uuid4().hex[:8], op_type, label)
        with self._lock:
            self._ops[op.id] = op
            self._gc()
        return op

    def get(self, op_id: str) -> Operation | None:
        return self._ops.get(op_id)

    def list_all(self) -> list[Operation]:
        with self._lock:
            return list(self._ops.values())

    def _gc(self) -> None:
        now = time.time()
        to_del = [
            k for k, v in self._ops.items()
            if v.status in _TERMINAL and now - v.updated_at > self._MAX_COMPLETED_AGE
        ]
        for k in to_del:
            del self._ops[k]


# Module-level singleton
registry = OperationRegistry()


# ---------------------------------------------------------------------------
# Connection monitor
# ---------------------------------------------------------------------------

def _connection_monitor(interval: float = 15.0) -> None:
    """Background daemon: periodically resumes NO_CONNECTION operations."""
    import socket

    def _online() -> bool:
        try:
            socket.setdefaulttimeout(3)
            socket.socket().connect(("8.8.8.8", 53))
            return True
        except OSError:
            return False

    while True:
        time.sleep(interval)
        for op in registry.list_all():
            if op.status is Status.NO_CONNECTION:
                if _online():
                    op.resume()


_monitor_thread = threading.Thread(target=_connection_monitor, daemon=True, name="conn-monitor")
_monitor_thread.start()
