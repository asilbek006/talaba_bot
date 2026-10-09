import asyncio
import time
from collections import OrderedDict

from aiogram import BaseMiddleware

import db
from i18n import DEFAULT_LANG, t

active_handlers = set()


class TaskTrackingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        task = asyncio.current_task()
        active_handlers.add(task)
        try:
            return await handler(event, data)
        finally:
            active_handlers.discard(task)


class ActivityMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if event.from_user:
            await db.touch_user(event.from_user.id)
        return await handler(event, data)


class RateLimitMiddleware(BaseMiddleware):
    """Bound per-user update bursts before they queue behind the FSM lock."""

    def __init__(self, busy=None):
        self.buckets = OrderedDict()
        self.busy = busy or (lambda uid: False)

    async def __call__(self, handler, event, data):
        item = event.message or event.callback_query
        user = getattr(item, "from_user", None)
        if not user:
            return await handler(event, data)
        now = time.monotonic()
        tokens, last = self.buckets.pop(user.id, (20.0, now))
        tokens = min(20.0, tokens + (now - last) * 2)
        self.buckets[user.id] = (max(0, tokens - 1), now)
        while len(self.buckets) > 10000:
            self.buckets.popitem(last=False)
        if tokens < 1:
            if event.callback_query:
                await event.callback_query.answer()
            return None
        if self.busy(user.id):
            account = await db.get_user(user.id)
            text = t((account or {}).get("lang", DEFAULT_LANG), "busy")
            await item.answer(text)
            return None
        return await handler(event, data)
