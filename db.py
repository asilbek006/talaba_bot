import json
import os
import time
from datetime import datetime

import asyncpg

import config
from config import BASE_DIR, DATABASE_URL, log

POOL: asyncpg.Pool | None = None


async def init(*, schema: str | None = None) -> asyncpg.Pool:
    global POOL
    if not DATABASE_URL:
        raise SystemExit(
            "DATABASE_URL topilmadi — .env faylida PostgreSQL ulanishini kiriting "
            "(masalan: postgresql://user:parol@localhost:5432/talaba_bot)"
        )
    POOL = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=20,
        command_timeout=30,
        server_settings={"search_path": schema} if schema else None,
    )
    try:
        await _migrate()
    except BaseException:
        await POOL.close()
        POOL = None
        raise
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
        for _col, ddl in [
            (
                "premium_until",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_until bigint NOT NULL DEFAULT 0",
            ),
            (
                "daily_used",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_used int NOT NULL DEFAULT 0",
            ),
            (
                "daily_reset",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_reset bigint NOT NULL DEFAULT 0",
            ),
            (
                "word_left",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS word_left int NOT NULL DEFAULT 0",
            ),
            (
                "slide_left",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS slide_left int NOT NULL DEFAULT 0",
            ),
            (
                "soon_notified_until",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS soon_notified_until bigint NOT NULL DEFAULT 0",
            ),
            (
                "expired_notified_until",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS expired_notified_until bigint NOT NULL DEFAULT 0",
            ),
        ]:
            await con.execute(ddl)
        await con.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS payments (
                id bigserial PRIMARY KEY, user_id bigint NOT NULL REFERENCES users(id),
                proof_id text NOT NULL, proof_unique_id text NOT NULL,
                proof_kind text NOT NULL, amount text NOT NULL,
                days int NOT NULL, word int NOT NULL, slide int NOT NULL,
                status text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected')),
                created_at bigint NOT NULL, decided_at bigint, decided_by bigint,
                UNIQUE (user_id, proof_unique_id)
            );
            CREATE TABLE IF NOT EXISTS ai_jobs (
                id bigserial PRIMARY KEY, user_id bigint NOT NULL REFERENCES users(id),
                kind text NOT NULL CHECK (kind IN ('word', 'slide', 'quiz')),
                source text NOT NULL CHECK (source IN ('free', 'word', 'slide', 'quiz')),
                period bigint NOT NULL, payload jsonb NOT NULL DEFAULT '{}',
                status text NOT NULL DEFAULT 'reserved'
                    CHECK (status IN ('reserved', 'completed', 'failed')),
                created_at bigint NOT NULL, finished_at bigint
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_job
                ON ai_jobs (user_id) WHERE status='reserved';
            CREATE TABLE IF NOT EXISTS support_replies (
                chat_id bigint NOT NULL, message_id bigint NOT NULL,
                user_id bigint NOT NULL, created_at bigint NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            );
            CREATE TABLE IF NOT EXISTS admin_secrets_used (digest text PRIMARY KEY);
            CREATE INDEX IF NOT EXISTS idx_files_expiry ON user_files (created_at);
            CREATE INDEX IF NOT EXISTS idx_payments_pending ON payments (id) WHERE status='pending';
        """)
    # Keep existing balances and receipt snapshots while adding the upstream quiz plan.
    await POOL.execute("""
        ALTER TABLE users ADD COLUMN IF NOT EXISTS quiz_left int NOT NULL DEFAULT 0;
        ALTER TABLE payments ADD COLUMN IF NOT EXISTS quiz int NOT NULL DEFAULT 0;
        ALTER TABLE ai_jobs DROP CONSTRAINT IF EXISTS ai_jobs_kind_check;
        ALTER TABLE ai_jobs ADD CONSTRAINT ai_jobs_kind_check CHECK (kind IN ('word','slide','quiz'));
        ALTER TABLE ai_jobs DROP CONSTRAINT IF EXISTS ai_jobs_source_check;
        ALTER TABLE ai_jobs ADD CONSTRAINT ai_jobs_source_check CHECK (source IN ('free','word','slide','quiz'));
    """)
    jf = BASE_DIR / "users.json"
    af = BASE_DIR / "admin.json"
    try:
        if jf.exists() and not await POOL.fetchval(
            "SELECT 1 FROM schema_migrations WHERE name='users_json'"
        ):
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
            await POOL.execute(
                "INSERT INTO schema_migrations(name) VALUES ('users_json') ON CONFLICT DO NOTHING"
            )
    except Exception as e:
        log.exception("users.json migratsiyasi xatosi: %s", e)
    try:
        if af.exists() and not await POOL.fetchval(
            "SELECT 1 FROM schema_migrations WHERE name='admins_json'"
        ):
            for uid in json.loads(af.read_text()):
                await add_admin(int(uid))
            await POOL.execute(
                "INSERT INTO schema_migrations(name) VALUES ('admins_json') ON CONFLICT DO NOTHING"
            )
    except Exception as e:
        log.exception("admin.json migratsiyasi xatosi: %s", e)


async def get_user(uid: int) -> dict | None:
    row = await POOL.fetchrow("SELECT * FROM users WHERE id=$1", uid)
    return dict(row) if row else None


async def upsert_user(
    uid: int,
    name: str,
    username: str,
    lang: str,
    first_seen: int | None = None,
    docs: int | None = None,
) -> bool:
    now = int(time.time())
    row = await POOL.fetchrow(
        """INSERT INTO users (id, name, username, lang, first_seen, last_seen, docs)
            VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7::int, 0))
            ON CONFLICT (id) DO UPDATE SET
                name=EXCLUDED.name, username=EXCLUDED.username,
                lang=EXCLUDED.lang, last_seen=EXCLUDED.last_seen,
                docs=GREATEST(users.docs, EXCLUDED.docs)
            RETURNING (xmax = 0) AS inserted""",
        uid,
        name,
        username or "",
        lang,
        first_seen or now,
        now,
        docs,
    )
    return bool(row and row["inserted"])


async def save_user(uid: int, name: str, username: str, lang: str) -> bool:
    return await upsert_user(uid, name, username, lang)


async def bump_docs(uid: int) -> None:
    await POOL.execute("UPDATE users SET docs = docs + 1 WHERE id=$1", uid)


async def admin_ids() -> list[int]:
    rows = await POOL.fetch("SELECT id FROM admins")
    ids = {int(r["id"]) for r in rows}
    for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(","):
        if x.isdigit():
            ids.add(int(x))
    return sorted(ids)


async def add_admin(uid: int) -> None:
    await POOL.execute("INSERT INTO admins (id) VALUES ($1) ON CONFLICT DO NOTHING", uid)


async def stats() -> dict:
    now = int(time.time())
    return dict(
        await POOL.fetchrow(
            "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE last_seen > $1) AS day, "
            "COALESCE(SUM(docs), 0) AS docs, COUNT(*) FILTER (WHERE premium_until > $2 OR word_left > 0 OR slide_left > 0 OR quiz_left > 0) AS premium "
            "FROM users",
            now - 86400,
            now,
        )
    )


async def stats_kinds() -> list[dict]:
    rows = await POOL.fetch(
        "SELECT kind, COUNT(*) AS n FROM user_files GROUP BY kind ORDER BY n DESC"
    )
    return [dict(r) for r in rows]


async def daily_status(uid: int) -> tuple[bool, int]:
    """(premium, bugungi ishlatilgan AI hujjatlar) — kun almashganda sifrlashni tiklaydi."""
    u = await get_user(uid)
    if not u:
        return False, 0
    now = int(time.time())
    today = day_start(now)
    premium = int(u.get("premium_until") or 0) > now or any(
        u.get(k, 0) > 0 for k in ("word_left", "slide_left", "quiz_left")
    )
    if int(u.get("daily_reset") or 0) < today:
        await POOL.execute("UPDATE users SET daily_used=0, daily_reset=$1 WHERE id=$2", today, uid)
        return premium, 0
    return premium, int(u.get("daily_used") or 0)


async def track_used(uid: int) -> None:
    await POOL.execute("UPDATE users SET daily_used = daily_used + 1 WHERE id=$1", uid)


async def grant_premium(uid: int, days: int) -> None:
    now = int(time.time())
    await POOL.execute(
        "UPDATE users SET premium_until = GREATEST(premium_until, $1) + $2 WHERE id=$3",
        now,
        int(days) * 86400,
        uid,
    )


async def grant_package(
    uid: int, days: int = 0, word: int = 5, slide: int = 5, quiz: int = 5
) -> None:
    """Add non-expiring credits. days is retained for legacy caller compatibility."""
    await POOL.execute(
        "UPDATE users SET premium_until=0, word_left=word_left+$2, "
        "slide_left=slide_left+$3, quiz_left=quiz_left+$4 WHERE id=$1",
        uid,
        int(word),
        int(slide),
        int(quiz),
    )


async def package_status(uid: int) -> tuple[bool, int, int, int]:
    user = await get_user(uid)
    balances = tuple(
        int((user or {}).get(k) or 0) for k in ("word_left", "slide_left", "quiz_left")
    )
    return any(n > 0 for n in balances), *balances


async def consume_package(uid: int, kind: str) -> None:
    if kind not in ("word", "slide", "quiz"):
        raise ValueError("Invalid quota kind")
    col = kind + "_left"
    await POOL.execute(f"UPDATE users SET {col}=GREATEST({col}-1,0) WHERE id=$1", uid)


async def expiring_premiums(within: int) -> list[dict]:
    now = int(time.time())
    rows = await POOL.fetch(
        "SELECT id, lang, premium_until FROM users "
        "WHERE premium_until > $1 AND premium_until <= $2 AND soon_notified_until < premium_until AND word_left=0 AND slide_left=0 AND quiz_left=0",
        now,
        now + int(within),
    )
    return [dict(r) for r in rows]


async def expired_premiums() -> list[dict]:
    now = int(time.time())
    rows = await POOL.fetch(
        "SELECT id, lang, premium_until FROM users WHERE premium_until > 0 AND premium_until <= $1 "
        "AND expired_notified_until < premium_until AND word_left=0 AND slide_left=0 AND quiz_left=0",
        now,
    )
    return [dict(r) for r in rows]


async def all_users() -> list[dict]:
    rows = await POOL.fetch("SELECT * FROM users ORDER BY last_seen DESC")
    return [dict(r) for r in rows]


async def recent_users(limit: int = 15) -> list[dict]:
    rows = await POOL.fetch(
        "SELECT id, name, username, lang, docs, first_seen, last_seen "
        "FROM users ORDER BY last_seen DESC LIMIT $1",
        limit,
    )
    return [dict(r) for r in rows]


async def add_file(uid: int, kind: str, title: str, path: str, size: int) -> int:
    return await POOL.fetchval(
        "INSERT INTO user_files (user_id, kind, title, path, size, created_at) "
        "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
        uid,
        kind,
        title or "",
        path,
        size or 0,
        int(time.time()),
    )


async def list_files(uid: int, limit: int = 8, offset: int = 0) -> list[dict]:
    rows = await POOL.fetch(
        "SELECT * FROM user_files WHERE user_id=$1 AND created_at > $4 ORDER BY id DESC LIMIT $2 OFFSET $3",
        uid,
        limit,
        offset,
        retention_cutoff(),
    )
    return [dict(r) for r in rows]


async def count_files(uid: int) -> int:
    return int(
        await POOL.fetchval(
            "SELECT COUNT(*) FROM user_files WHERE user_id=$1 AND created_at > $2",
            uid,
            retention_cutoff(),
        )
    )


async def get_file(fid: int, uid: int) -> dict | None:
    row = await POOL.fetchrow(
        "SELECT * FROM user_files WHERE id=$1 AND user_id=$2 AND created_at > $3",
        fid,
        uid,
        retention_cutoff(),
    )
    return dict(row) if row else None


async def file_owner_count(path: str) -> int:
    return await POOL.fetchval("SELECT COUNT(DISTINCT user_id) FROM user_files WHERE path=$1", path)


async def relocate_file(fid: int, uid: int, path: str) -> None:
    await POOL.execute("UPDATE user_files SET path=$3 WHERE id=$1 AND user_id=$2", fid, uid, path)


def day_start(now: float | None = None) -> int:
    local = datetime.fromtimestamp(now if now is not None else time.time(), config.APP_TIMEZONE)
    return int(local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def retention_cutoff() -> int:
    return int(time.time()) - config.FILE_RETENTION_DAYS * 86400


async def touch_user(uid: int) -> None:
    now = int(time.time())
    await POOL.execute(
        "UPDATE users SET last_seen=$1 WHERE id=$2 AND last_seen < $3", now, uid, now - 60
    )


async def claim_admin_secret(uid: int, digest: str) -> bool:
    async with POOL.acquire() as con, con.transaction():
        inserted = await con.fetchval(
            "INSERT INTO admin_secrets_used(digest) VALUES ($1) ON CONFLICT DO NOTHING RETURNING digest",
            digest,
        )
        if not inserted:
            return False
        await con.execute("INSERT INTO admins(id) VALUES ($1) ON CONFLICT DO NOTHING", uid)
        return True


async def create_payment(uid: int, proof_id: str, unique_id: str, kind: str) -> dict:
    row = await POOL.fetchrow(
        """INSERT INTO payments(user_id, proof_id, proof_unique_id, proof_kind, amount, days, word, slide, created_at, quiz)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
           ON CONFLICT (user_id, proof_unique_id) DO UPDATE SET proof_id=EXCLUDED.proof_id
           RETURNING *""",
        uid,
        proof_id,
        unique_id,
        kind,
        config.PAYMENT_AMOUNT,
        0,
        config.PRO_WORD,
        config.PRO_SLIDE,
        int(time.time()),
        config.PRO_QUIZ,
    )
    return dict(row)


async def pending_payments(limit: int = 20) -> list[dict]:
    return [
        dict(r)
        for r in await POOL.fetch(
            "SELECT * FROM payments WHERE status='pending' ORDER BY id LIMIT $1", limit
        )
    ]


async def decide_payment(pid: int, admin_id: int, approve: bool) -> dict | None:
    """Only one admin can transition a receipt; its grant is in the same transaction."""
    now = int(time.time())
    async with POOL.acquire() as con, con.transaction():
        row = await con.fetchrow(
            "UPDATE payments SET status=$2, decided_at=$3, decided_by=$4 "
            "WHERE id=$1 AND status='pending' RETURNING *",
            pid,
            "approved" if approve else "rejected",
            now,
            admin_id,
        )
        if not row:
            return None
        if approve:
            await con.execute(
                "UPDATE users SET premium_until=0, word_left=word_left+$2, "
                "slide_left=slide_left+$3, quiz_left=quiz_left+$4 WHERE id=$1",
                row["user_id"],
                row["word"],
                row["slide"],
                row["quiz"],
            )
        return dict(row)


async def reserve_job(uid: int, kind: str, payload: dict) -> dict | None:
    """Reserve one credit atomically before AI work, with a durable refund record."""
    if kind not in ("word", "slide", "quiz"):
        raise ValueError("Invalid quota kind")
    now, today = int(time.time()), day_start()
    async with POOL.acquire() as con, con.transaction():
        user = await con.fetchrow("SELECT * FROM users WHERE id=$1 FOR UPDATE", uid)
        if not user:
            return None
        if await con.fetchval("SELECT 1 FROM ai_jobs WHERE user_id=$1 AND status='reserved'", uid):
            return {"busy": True}
        column = kind + "_left"
        if user[column] > 0:
            source, period = kind, 0
            await con.execute(f"UPDATE users SET {column}={column}-1 WHERE id=$1", uid)
        else:
            used = user["daily_used"] if user["daily_reset"] >= today else 0
            if used >= config.DAILY_LIMIT:
                return None
            source, period = "free", today
            await con.execute(
                "UPDATE users SET daily_used=$1,daily_reset=$2 WHERE id=$3", used + 1, today, uid
            )
        return dict(
            await con.fetchrow(
                "INSERT INTO ai_jobs(user_id,kind,source,period,payload,created_at) "
                "VALUES($1,$2,$3,$4,$5::jsonb,$6) RETURNING *",
                uid,
                kind,
                source,
                period,
                json.dumps(payload, ensure_ascii=False),
                now,
            )
        )


async def refund_job(job_id: int) -> bool:
    async with POOL.acquire() as con, con.transaction():
        # Follow the same users -> jobs lock order as reserve_job.
        uid = await con.fetchval("SELECT user_id FROM ai_jobs WHERE id=$1", job_id)
        if uid is None:
            return False
        await con.fetchrow("SELECT id FROM users WHERE id=$1 FOR UPDATE", uid)
        job = await con.fetchrow(
            "UPDATE ai_jobs SET status='failed',finished_at=$2 WHERE id=$1 AND status='reserved' RETURNING *",
            job_id,
            int(time.time()),
        )
        if not job:
            return False
        if job["source"] == "free":
            await con.execute(
                "UPDATE users SET daily_used=GREATEST(daily_used-1,0) WHERE id=$1 AND daily_reset=$2",
                uid,
                job["period"],
            )
        else:
            column = job["source"] + "_left"
            await con.execute(
                f"UPDATE users SET {column}={column}+1 WHERE id=$1",
                uid,
            )
        return True


async def complete_job(job_id: int, uid: int, kind: str, title: str, path: str, size: int) -> int:
    async with POOL.acquire() as con, con.transaction():
        await con.fetchrow("SELECT id FROM users WHERE id=$1 FOR UPDATE", uid)
        job = await con.fetchval(
            "UPDATE ai_jobs SET status='completed',finished_at=$3 WHERE id=$1 AND user_id=$2 AND status='reserved' RETURNING id",
            job_id,
            uid,
            int(time.time()),
        )
        if not job:
            raise RuntimeError("AI job is no longer active")
        fid = await con.fetchval(
            "INSERT INTO user_files(user_id,kind,title,path,size,created_at) VALUES($1,$2,$3,$4,$5,$6) RETURNING id",
            uid,
            kind,
            title,
            path,
            size,
            int(time.time()),
        )
        await con.execute(
            "UPDATE users SET docs=docs+1,last_seen=$2 WHERE id=$1", uid, int(time.time())
        )
        return fid


async def get_job(job_id: int, uid: int) -> dict | None:
    row = await POOL.fetchrow(
        "SELECT * FROM ai_jobs WHERE id=$1 AND user_id=$2 AND status='completed'", job_id, uid
    )
    if not row:
        return None
    result = dict(row)
    result["payload"] = (
        json.loads(result["payload"]) if isinstance(result["payload"], str) else result["payload"]
    )
    return result


async def recover_interrupted_jobs() -> list[int]:
    # Startup only: this bot runs one polling process. No active worker is displaced.
    rows = await POOL.fetch("""
        SELECT j.id,j.user_id FROM ai_jobs j WHERE j.status='reserved'
        AND NOT (j.kind='quiz' AND COALESCE(j.payload->>'interactive','false')='true' AND EXISTS (
            SELECT 1 FROM fsm_record f WHERE f.user_id=j.user_id
            AND f.data->'quiz'->>'job_id'=j.id::text
            AND f.data->'quiz'->>'done'='false'
        ))
    """)
    recovered = []
    for row in rows:
        if await refund_job(row["id"]):
            recovered.append(row["user_id"])
    return recovered


async def mark_premium_notified(uid: int, period: int, soon: bool) -> None:
    column = "soon_notified_until" if soon else "expired_notified_until"
    await POOL.execute(
        f"UPDATE users SET {column}=$2 WHERE id=$1 AND premium_until=$2", uid, period
    )


async def remember_support_reply(chat_id: int, message_id: int, uid: int) -> None:
    await POOL.execute(
        "INSERT INTO support_replies(chat_id,message_id,user_id,created_at) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",
        chat_id,
        message_id,
        uid,
        int(time.time()),
    )


async def support_recipient(chat_id: int, message_id: int) -> int | None:
    return await POOL.fetchval(
        "SELECT user_id FROM support_replies WHERE chat_id=$1 AND message_id=$2",
        chat_id,
        message_id,
    )


async def prune_expired_records() -> None:
    cutoff = retention_cutoff()
    async with POOL.acquire() as con, con.transaction():
        await con.execute("DELETE FROM user_files WHERE created_at <= $1", cutoff)
        await con.execute("DELETE FROM support_replies WHERE created_at <= $1", cutoff)
        await con.execute(
            "DELETE FROM ai_jobs WHERE status != 'reserved' AND created_at <= $1", cutoff
        )


async def complete_quiz(job_id: int, uid: int) -> bool:
    """Charge a reserved interactive quiz once, without creating a file record."""
    async with POOL.acquire() as con, con.transaction():
        await con.fetchrow("SELECT id FROM users WHERE id=$1 FOR UPDATE", uid)
        changed = await con.fetchval(
            "UPDATE ai_jobs SET status='completed',finished_at=$3 "
            "WHERE id=$1 AND user_id=$2 AND kind='quiz' AND status='reserved' RETURNING id",
            job_id,
            uid,
            int(time.time()),
        )
        if changed:
            await con.execute(
                "UPDATE users SET docs=docs+1,last_seen=$2 WHERE id=$1", uid, int(time.time())
            )
        return bool(changed)
