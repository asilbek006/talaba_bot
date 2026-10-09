"""Run blocking work without releasing its concurrency slot before it finishes."""

import asyncio


async def run_blocking(limit: asyncio.Semaphore, fn, *args):
    async with limit:
        return await run_sync(fn, *args)


async def run_sync(fn, *args):
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Cancelling to_thread cannot stop its thread. Retain the caller's slot.
        try:
            await task
        except Exception:
            pass
        raise
