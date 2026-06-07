"""Tests for the operation lifecycle manager."""
import threading
import time

import pytest

from joganacaixa.operations import Operation, OperationRegistry, Status


@pytest.fixture()
def reg():
    return OperationRegistry()


def test_create_operation(reg):
    op = reg.create("store", label="test.tar")
    assert op.id
    assert op.type == "store"
    assert op.label == "test.tar"
    assert op.status is Status.PENDING


def test_pause_resume(reg):
    op = reg.create("store")
    op.mark_running()
    assert op.status is Status.RUNNING

    op.pause()
    assert op.status is Status.PAUSED
    assert not op._pause.is_set()

    op.resume()
    assert op.status is Status.RUNNING
    assert op._pause.is_set()


def test_cancel_unblocks_pause(reg):
    op = reg.create("store")
    op.pause()

    # cancel should unblock _pause so workers don't hang
    op.cancel()
    assert op._pause.is_set()
    assert op._cancel.is_set()
    assert op.status is Status.CANCELLED


def test_check_returns_false_when_cancelled(reg):
    op = reg.create("store")
    op.mark_running()
    op.cancel()
    assert op.check() is False


def test_check_blocks_while_paused(reg):
    op = reg.create("store")
    op.mark_running()
    op.pause()

    results = []

    def worker():
        # check() should block until resumed
        results.append(op.check())

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    time.sleep(0.05)
    assert not results   # still blocked
    op.resume()
    t.join(timeout=1)
    assert results == [True]


def test_no_connection_sets_pause(reg):
    op = reg.create("recover")
    op.mark_running()
    op.mark_no_connection()
    assert op.status is Status.NO_CONNECTION
    assert not op._pause.is_set()


def test_update_progress(reg):
    op = reg.create("store")
    op.update_progress(500_000, 1_000_000)
    assert op.progress == 50
    assert op.transferred == 500_000
    assert op.total == 1_000_000


def test_mark_completed(reg):
    op = reg.create("store")
    op.mark_completed(result={"package_id": "abc"})
    assert op.status is Status.COMPLETED
    assert op.progress == 100
    assert op.result["package_id"] == "abc"


def test_mark_failed(reg):
    op = reg.create("recover")
    op.mark_failed("connection timeout")
    assert op.status is Status.FAILED
    assert op.error == "connection timeout"


def test_to_dict_keys(reg):
    op = reg.create("store", label="file.tar")
    d = op.to_dict()
    for key in ("id", "type", "label", "status", "progress", "transferred", "total", "error"):
        assert key in d


def test_registry_get(reg):
    op = reg.create("store")
    assert reg.get(op.id) is op
    assert reg.get("nonexistent") is None


def test_registry_list(reg):
    a = reg.create("store")
    b = reg.create("recover")
    ops = reg.list_all()
    ids = {o.id for o in ops}
    assert a.id in ids
    assert b.id in ids


def test_terminal_status_ignores_pause_resume(reg):
    op = reg.create("store")
    op.mark_completed()
    # Should not change status after terminal
    op.pause()
    assert op.status is Status.COMPLETED
    op.cancel()
    assert op.status is Status.COMPLETED
