import json
import os
import time
from pathlib import Path

import asyncpg

from config import BASE_DIR, DATABASE_URL, log

POOL: asyncpg.Pool | None = None


async def init() -> asyncpg.Pool:
    global POOL
    if not DATABASE_URL:
        raise SystemExit(
            "DATABASE_URL topilmadi — .env faylida PostgreSQL ulanishini kiriting "
            "(masalan: postgresql://user:parol@localhost:5432/talaba_bot)")
    POOL = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=20)
    await _migrate()
    return POOL


async def _migrate() -> None:
    async with POOL.acquire() as con:
        await con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         bigint PRIMARY KEY,
                name       text NOT NULL DEFAULT '',
                username   text DEFAULT '',
                lang       text NOT NULL DEFAULT 'uz',
                docs       int  NOT NULL DEFAULT 0,
                first_seen bigint NOT NULL DEFAULT 0,
                last_seen  bigint NOT NULL DEFAULT 0
            )
        """)
        await con.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id bigint PRIMARY KEY
            )
        """)
        await con.execute("""
            CREATE TABLE IF NOT EXISTS fsm_record (
                bot_id bigint NOT NULL,
                chat_id bigint NOT NULL,
                user_id bigint NOT NULL,
                thread_id bigint NOT NULL DEFAULT 0,
                data jsonb NOT NULL DEFAULT '{}'::jsonb,
                state text,
                PRIMARY KEY (bot_id, chat_id, user_id, thread_id)
            )
        """)
        await con.execute("""
            CREATE TABLE IF NOT EXISTS user_files (
                id serial PRIMARY KEY,
                user_id bigint NOT NULL,
                kind text NOT NULL,
                title text DEFAULT '',
                path text NOT NULL,
                size int NOT NULL DEFAULT 0,
                created_at bigint NOT NULL DEFAULT 0
            )
        """)
        await con.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_files ON user_files (user_id, id DESC)""")
        for col, ddl in [
            ("premium_until", "ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_until bigint NOT NULL DEFAULT 0"),
            ("daily_used", "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_used int NOT NULL DEFAULT 0"),
            ("daily_reset", "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_reset bigint NOT NULL DEFAULT 0"),
            ("word_left", "ALTER TABLE users ADD COLUMN IF NOT EXISTS word_left int NOT NULL DEFAULT 0"),
            ("slide_left", "ALTER TABLE users ADD COLUMN IF NOT EXISTS slide_left int NOT NULL DEFAULT 0"),
        ]:
            await con.execute(ddl)
    jf = BASE_DIR / "users.json"
    af = BASE_DIR / "admin.json"
    try:
        if jf.exists():
            users = json.loads(jf.read_text())
            for str_uid, u in users.items():
                await upsert_user(
                    int(str_uid),
                    u.get("name", ""),
                    u.get("username", ""),
                    u.get("lang") or "uz",
                    u.get("first_seen") or int(time.time()),
                    docs=u.get("docs", 0),
                )
    except Exception as e:
        log.exception("users.json migratsiyasi xatosi: %s", e)
    try:
        if af.exists():
            for uid in json.loads(af.read_text()):
                await add_admin(int(uid))
    except Exception as e:
        log.exception("admin.json migratsiyasi xatosi: %s", e)


async def get_user(uid: int) -> dict | None:
    row = await POOL.fetchrow(
        "SELECT * FROM users WHERE id=$1", uid)
    return dict(row) if row else None


async def upsert_user(uid: int, name: str, username: str, lang: str,
                      first_seen: int | None = None, docs: int | None = None) -> bool:
    now = int(time.time())
    set_docs = ", docs = $7" if docs is not None else ""
    row = await POOL.fetchrow(
        f"""INSERT INTO users (id, name, username, lang, first_seen, last_seen)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO UPDATE SET
                name=EXCLUDED.name, username=EXCLUDED.username,
                lang=EXCLUDED.lang, last_seen=EXCLUDED.last_seen{set_docs}
            RETURNING (xmax = 0) AS inserted""",
        uid, name, username or "", lang, first_seen or now, now,
        docs) if docs is not None else await POOL.fetchrow(
        """INSERT INTO users (id, name, username, lang, first_seen, last_seen)
           VALUES ($1, $2, $3, $4, $5, $6)
           ON CONFLICT (id) DO UPDATE SET
               name=EXCLUDED.name, username=EXCLUDED.username,
               lang=EXCLUDED.lang, last_seen=EXCLUDED.last_seen
           RETURNING (xmax = 0) AS inserted""",
        uid, name, username or "", lang, first_seen or now, now,
    )
    return bool(row and row["inserted"])


async def save_user(uid: int, name: str, username: str, lang: str) -> bool:
    return await upsert_user(uid, name, username, lang)


async def bump_docs(uid: int) -> None:
    await POOL.execute(
        "UPDATE users SET docs = docs + 1 WHERE id=$1", uid)


async def admin_ids() -> list[int]:
    rows = await POOL.fetch("SELECT id FROM admins")
    ids = {int(r["id"]) for r in rows}
    for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(","):
        if x.isdigit():
            ids.add(int(x))
    return sorted(ids)


async def add_admin(uid: int) -> None:
    await POOL.execute(
        "INSERT INTO admins (id) VALUES ($1) ON CONFLICT DO NOTHING", uid)


async def stats() -> dict:
    now = int(time.time())
    total = await POOL.fetchval("SELECT COUNT(*) FROM users")
    day = await POOL.fetchval(
        "SELECT COUNT(*) FROM users WHERE last_seen > $1", now - 86400)
    docs = await POOL.fetchval("SELECT COALESCE(SUM(docs), 0) FROM users")
    premium = await POOL.fetchval(
        "SELECT COUNT(*) FROM users WHERE premium_until > $1", now)
    return {"total": total, "day": day, "docs": docs, "premium": premium}


async def stats_kinds() -> list[dict]:
    rows = await POOL.fetch(
        "SELECT kind, COUNT(*) AS n FROM user_files GROUP BY kind ORDER BY n DESC")
    return [dict(r) for r in rows]


async def daily_status(uid: int) -> tuple[bool, int]:
    """(premium, bugungi ishlatilgan AI hujjatlar) — kun almashganda sifrlashni tiklaydi."""
    u = await get_user(uid)
    if not u:
        return False, 0
    now = int(time.time())
    today = now - (now % 86400)
    premium = int(u.get("premium_until") or 0) > now
    if int(u.get("daily_reset") or 0) < today:
        await POOL.execute(
            "UPDATE users SET daily_used=0, daily_reset=$1 WHERE id=$2", today, uid)
        return premium, 0
    return premium, int(u.get("daily_used") or 0)


async def track_used(uid: int) -> None:
    await POOL.execute(
        "UPDATE users SET daily_used = daily_used + 1 WHERE id=$1", uid)


async def grant_premium(uid: int, days: int) -> None:
    now = int(time.time())
    await POOL.execute(
        "UPDATE users SET premium_until = GREATEST(premium_until, $1) + $2 WHERE id=$3",
        now, int(days) * 86400, uid)


async def grant_package(uid: int, days: int, word: int, slide: int) -> None:
    """Pro paket: davr + Word/slayd kvotalari (kamaytirmaydi)."""
    now = int(time.time())
    await POOL.execute(
        "UPDATE users SET premium_until = GREATEST(premium_until, $1) + $2, "
        "word_left = GREATEST(word_left, $3), slide_left = GREATEST(slide_left, $4) "
        "WHERE id=$5",
        now, int(days) * 86400, int(word), int(slide), uid)


async def package_status(uid: int) -> tuple[bool, int, int]:
    """(pro, word_left, slide_left) — pro = premium_until hozirgi vaqtdan keyin."""
    u = await get_user(uid)
    if not u:
        return False, 0, 0
    now = int(time.time())
    pro = int(u.get("premium_until") or 0) > now
    return pro, int(u.get("word_left") or 0), int(u.get("slide_left") or 0)


async def consume_package(uid: int, kind: str) -> None:
    col = "word_left" if kind == "word" else "slide_left"
    await POOL.execute(
        f"UPDATE users SET {col} = GREATEST({col} - 1, 0) WHERE id=$1", uid)


async def expiring_premiums(within: int) -> list[dict]:
    now = int(time.time())
    rows = await POOL.fetch(
        "SELECT id, lang, premium_until FROM users "
        "WHERE premium_until > $1 AND premium_until <= $2",
        now, now + int(within))
    return [dict(r) for r in rows]


async def expired_premiums() -> list[dict]:
    now = int(time.time())
    rows = await POOL.fetch(
        "SELECT id, lang FROM users WHERE premium_until > 0 AND premium_until < $1",
        now)
    return [dict(r) for r in rows]


async def all_users() -> list[dict]:
    rows = await POOL.fetch("SELECT * FROM users ORDER BY last_seen DESC")
    return [dict(r) for r in rows]


async def add_file(uid: int, kind: str, title: str, path: str, size: int) -> None:
    await POOL.execute(
        "INSERT INTO user_files (user_id, kind, title, path, size, created_at) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        uid, kind, title or "", path, size or 0, int(time.time()))


async def list_files(uid: int, limit: int = 8, offset: int = 0) -> list[dict]:
    rows = await POOL.fetch(
        "SELECT * FROM user_files WHERE user_id=$1 ORDER BY id DESC LIMIT $2 OFFSET $3",
        uid, limit, offset)
    return [dict(r) for r in rows]


async def count_files(uid: int) -> int:
    return int(await POOL.fetchval("SELECT COUNT(*) FROM user_files WHERE user_id=$1", uid))


async def get_file(fid: int, uid: int) -> dict | None:
    row = await POOL.fetchrow(
        "SELECT * FROM user_files WHERE id=$1 AND user_id=$2", fid, uid)
    return dict(row) if row else None