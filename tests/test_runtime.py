import asyncio
import threading
import time

from services.runtime import run_blocking


def test_cancellation_does_not_release_a_running_thread_slot():
    async def scenario():
        gate = asyncio.Semaphore(1)
        entered, leave = threading.Event(), threading.Event()

        def work():
            entered.set()
            leave.wait(timeout=2)

        task = asyncio.create_task(run_blocking(gate, work))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.sleep(0.01)
        assert gate.locked()
        leave.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert not gate.locked()

    asyncio.run(scenario())


def test_blocking_work_keeps_event_loop_responsive():
    async def scenario():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            for _ in range(10):
                await asyncio.sleep(0.005)
                ticks += 1

        await asyncio.gather(run_blocking(asyncio.Semaphore(1), time.sleep, 0.08), heartbeat())
        assert ticks == 10

    asyncio.run(scenario())


def test_event_isolation_serializes_updates_and_releases_unused_locks():
    from aiogram.fsm.storage.base import StorageKey

    from services.fsm_pg import EventIsolation

    async def scenario():
        isolation = EventIsolation()
        key = StorageKey(bot_id=1, chat_id=2, user_id=3)
        count = 0

        async def update():
            nonlocal count
            async with isolation.lock(key):
                old = count
                await asyncio.sleep(0)
                count = old + 1

        await asyncio.gather(*(update() for _ in range(50)))
        assert count == 50
        assert isolation.locks == {}

    asyncio.run(scenario())


def test_shutdown_tracking_includes_handler_until_cancellation_cleanup():
    from services.middleware import TaskTrackingMiddleware, active_handlers

    async def scenario():
        entered = asyncio.Event()
        cleaned = asyncio.Event()

        async def handler(event, data):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        task = asyncio.create_task(TaskTrackingMiddleware()(handler, None, {}))
        await entered.wait()
        assert task in active_handlers
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert cleaned.is_set() and task not in active_handlers

    asyncio.run(scenario())
