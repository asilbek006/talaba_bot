import json
from typing import Mapping

from aiogram.exceptions import DataNotDictLikeError
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

import db
from config import log

_log_warned = False


def _load(raw) -> dict:
    global _log_warned
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except Exception:
            if not _log_warned:
                _log_warned = True
                log.warning("fsm_record.data JSON emas, bo'sh holatga tushirildi")
            return {}
    return dict(raw or {})


class PostgresStorage(BaseStorage):
    """Holat va data PostgreSQL fsm_record jadvalida saqlanadi.
    Bot restart bo'lsa ham yo'qolmaydi, ko'p ishchi jarayon o'qisa ham xavfsiz."""

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        s = state.state if isinstance(state, State) else state
        await db.POOL.execute(
            """INSERT INTO fsm_record (bot_id, chat_id, user_id, thread_id, data, state)
               VALUES ($1, $2, $3, $4, '{}'::jsonb, $5)
               ON CONFLICT (bot_id, chat_id, user_id, thread_id)
               DO UPDATE SET state = EXCLUDED.state""",
            key.bot_id, key.chat_id, key.user_id, key.thread_id or 0, s)

    async def get_state(self, key: StorageKey) -> str | None:
        row = await db.POOL.fetchrow(
            "SELECT state FROM fsm_record WHERE bot_id=$1 AND chat_id=$2 "
            "AND user_id=$3 AND thread_id=$4",
            key.bot_id, key.chat_id, key.user_id, key.thread_id or 0)
        return row["state"] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, object]) -> None:
        if not isinstance(data, dict):
            msg = f"Data must be a dict or dict-like object, got {type(data).__name__}"
            raise DataNotDictLikeError(msg)
        await db.POOL.execute(
            """INSERT INTO fsm_record (bot_id, chat_id, user_id, thread_id, data, state)
               VALUES ($1, $2, $3, $4, $5::jsonb, NULL)
               ON CONFLICT (bot_id, chat_id, user_id, thread_id)
               DO UPDATE SET data = EXCLUDED.data""",
            key.bot_id, key.chat_id, key.user_id, key.thread_id or 0,
            json.dumps(data, ensure_ascii=False))

    async def get_data(self, key: StorageKey) -> dict[str, object]:
        row = await db.POOL.fetchrow(
            "SELECT data FROM fsm_record WHERE bot_id=$1 AND chat_id=$2 "
            "AND user_id=$3 AND thread_id=$4",
            key.bot_id, key.chat_id, key.user_id, key.thread_id or 0)
        return _load(row["data"]) if row else {}

    async def update_data(self, key: StorageKey, data: Mapping[str, object]) -> dict[str, object]:
        row = await db.POOL.fetchrow(
            """INSERT INTO fsm_record (bot_id, chat_id, user_id, thread_id, data, state)
               VALUES ($1, $2, $3, $4, $5::jsonb, NULL)
               ON CONFLICT (bot_id, chat_id, user_id, thread_id)
               DO UPDATE SET data = COALESCE(fsm_record.data, '{}'::jsonb) || EXCLUDED.data
               RETURNING data""",
            key.bot_id, key.chat_id, key.user_id, key.thread_id or 0,
            json.dumps(dict(data), ensure_ascii=False))
        return _load(row["data"]) if row else {}

    async def close(self) -> None:
        pass