"""Run BLE coroutines on a persistent background event loop.

Avoids creating/closing a fresh asyncio loop for each BLE call, which can
trip CoreBluetooth callback races on macOS when the machine sleeps/wakes.
"""

from __future__ import annotations

import asyncio
import atexit
import threading
from typing import Awaitable, TypeVar

T = TypeVar("T")


class _BleLoopRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()

        # Best-effort shutdown of pending tasks to avoid noisy warnings.
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        if pending:
            for task in pending:
                task.cancel()
            try:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
        loop.close()

    def _ensure_started(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None and self._thread is not None and self._thread.is_alive():
            return self._loop

        with self._lock:
            if self._loop is not None and self._thread is not None and self._thread.is_alive():
                return self._loop

            self._ready.clear()
            thread = threading.Thread(
                target=self._thread_main,
                name="razecli-ble-loop",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            self._ready.wait(timeout=2.0)

            loop = self._loop
            if loop is None:
                raise RuntimeError("Failed to start BLE event loop thread")
            return loop

    def run(self, coro: Awaitable[T]) -> T:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("BLE sync wrapper cannot run inside an active asyncio event loop")

        loop = self._ensure_started()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result()

    def shutdown(self) -> None:
        loop = self._loop
        thread = self._thread
        if loop is None or thread is None:
            return

        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass
        try:
            thread.join(timeout=1.0)
        except Exception:
            pass

        self._loop = None
        self._thread = None


_RUNNER = _BleLoopRunner()
atexit.register(_RUNNER.shutdown)


def run_ble_sync(coro: Awaitable[T]) -> T:
    return _RUNNER.run(coro)

