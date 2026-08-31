"""Tests for the advisory lock that keeps two processes off the amp at once."""

import multiprocessing as mp
import time

import pytest

from lt25_mcp.transport import AmpLock, TransportError


def _hold(path, started, release):
    lock = AmpLock(path)
    lock.acquire()
    started.set()
    release.wait(timeout=10)
    lock.release()


class TestAmpLock:
    def test_acquire_and_release(self, tmp_path):
        lock = AmpLock(tmp_path / "amp.lock")
        lock.acquire()
        lock.release()

    def test_release_is_idempotent(self, tmp_path):
        lock = AmpLock(tmp_path / "amp.lock")
        lock.acquire()
        lock.release()
        lock.release()

    def test_the_same_lock_can_be_retaken(self, tmp_path):
        lock = AmpLock(tmp_path / "amp.lock")
        for _ in range(3):
            lock.acquire()
            lock.release()

    def test_a_second_process_is_refused(self, tmp_path):
        path = tmp_path / "amp.lock"
        ctx = mp.get_context("spawn")
        started, release = ctx.Event(), ctx.Event()
        holder = ctx.Process(target=_hold, args=(str(path), started, release))
        holder.start()
        try:
            assert started.wait(timeout=10), "holder never acquired"
            with pytest.raises(TransportError, match="another program"):
                AmpLock(path).acquire()
        finally:
            release.set()
            holder.join(timeout=10)

    def test_the_lock_frees_when_the_holder_exits(self, tmp_path):
        path = tmp_path / "amp.lock"
        ctx = mp.get_context("spawn")
        started, release = ctx.Event(), ctx.Event()
        holder = ctx.Process(target=_hold, args=(str(path), started, release))
        holder.start()
        assert started.wait(timeout=10)
        release.set()
        holder.join(timeout=10)

        lock = AmpLock(path)
        lock.acquire()   # must succeed now
        lock.release()

    def test_context_manager_releases(self, tmp_path):
        path = tmp_path / "amp.lock"
        with AmpLock(path):
            pass
        lock = AmpLock(path)
        lock.acquire()
        lock.release()

    def test_error_names_the_holding_process(self, tmp_path):
        path = tmp_path / "amp.lock"
        ctx = mp.get_context("spawn")
        started, release = ctx.Event(), ctx.Event()
        holder = ctx.Process(target=_hold, args=(str(path), started, release))
        holder.start()
        try:
            assert started.wait(timeout=10)
            with pytest.raises(TransportError) as exc:
                AmpLock(path).acquire()
            assert str(holder.pid) in str(exc.value)
        finally:
            release.set()
            holder.join(timeout=10)
