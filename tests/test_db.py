import asyncio
import time
import uuid

import asyncpg
import pytest

import config
import db

_loop = None


def run(coro):
    return _loop.run_until_complete(coro)


@pytest.fixture(scope="module", autouse=True)
def pg():
    global _loop
    if not config.DATABASE_URL:
        pytest.skip("DATABASE_URL .env'da yo'q")
    _loop = asyncio.new_event_loop()
    schema = "test_" + uuid.uuid4().hex

    async def prepare():
        connection = await asyncpg.connect(config.DATABASE_URL)
        try:
            await connection.execute(f"CREATE SCHEMA {schema}")
        finally:
            await connection.close()
        await db.init(schema=schema)

    try:
        run(prepare())
    except Exception as e:
        _loop.close()
        pytest.fail(f"Test PostgreSQL unavailable: {e}")
    try:
        yield db.POOL
    finally:
        run(db.POOL.close())

        async def cleanup_schema():
            connection = await asyncpg.connect(config.DATABASE_URL)
            try:
                await connection.execute(f"DROP SCHEMA {schema} CASCADE")
            finally:
                await connection.close()

        run(cleanup_schema())
        _loop.close()


def test_upsert_and_get_user(pg):
    uid = 9_000_000_001 + int(time.time()) % 1000
    run(db.upsert_user(uid, "Test", "testuser", "ru"))
    u = run(db.get_user(uid))
    assert u is not None
    assert u["name"] == "Test"
    assert u["lang"] == "ru"
    run(cleanup_users([uid]))


def test_bump_docs(pg):
    uid = 9_000_000_010 + int(time.time()) % 1000
    run(db.upsert_user(uid, "D", "", "uz"))
    run(db.bump_docs(uid))
    run(db.bump_docs(uid))
    u = run(db.get_user(uid))
    assert u["docs"] == 2
    run(cleanup_users([uid]))


def test_file_crud(pg):
    uid = 9_000_000_020 + int(time.time()) % 1000
    run(db.upsert_user(uid, "F", "", "uz"))
    run(db.add_file(uid, "ref", "Referat", "/tmp/fake.docx", 1234))
    run(db.add_file(uid, "ppt", "Slayd", "/tmp/fake.pptx", 999))
    assert run(db.count_files(uid)) == 2
    files = run(db.list_files(uid, 1, 0))
    assert len(files) == 1
    assert files[0]["kind"] == "ppt"
    f = run(db.get_file(files[0]["id"], uid))
    assert f is not None and f["title"] == "Slayd"
    run(cleanup_users([uid], files=True))


def test_admin_ids_basic(pg):
    ids = run(db.admin_ids())
    assert len(ids) == len(set(ids))
    assert all(isinstance(i, int) for i in ids)


def test_daily_status_and_track(pg):
    uid = 9_000_000_030 + int(time.time()) % 1000
    run(db.upsert_user(uid, "L", "", "uz"))
    premium, used = run(db.daily_status(uid))
    assert premium is False and used == 0
    run(db.track_used(uid))
    run(db.track_used(uid))
    premium, used = run(db.daily_status(uid))
    assert used == 2
    run(cleanup_users([uid]))


def test_grant_premium(pg):
    uid = 9_000_000_040 + int(time.time()) % 1000
    run(db.upsert_user(uid, "P", "", "uz"))
    run(db.grant_premium(uid, 7))
    premium, _ = run(db.daily_status(uid))
    assert premium is True
    now = int(time.time())
    u = run(db.get_user(uid))
    assert u["premium_until"] >= now + 6 * 86400
    run(cleanup_users([uid]))


def test_package_quota(pg):
    uid = 9_000_000_045 + int(time.time()) % 1000
    run(db.upsert_user(uid, "Q", "", "uz"))
    pro, wl, sl, ql = run(db.package_status(uid))
    assert pro is False and wl == 0 and sl == 0 and ql == 0
    run(db.grant_package(uid, 30, 3, 2, 4))
    pro, wl, sl, ql = run(db.package_status(uid))
    assert pro is True and wl == 3 and sl == 2 and ql == 4
    run(db.consume_package(uid, "word"))
    run(db.consume_package(uid, "word"))
    _, wl2, _, _ = run(db.package_status(uid))
    assert wl2 == 1
    run(db.consume_package(uid, "slide"))
    _, _, sl2, _ = run(db.package_status(uid))
    assert sl2 == 1
    run(db.consume_package(uid, "quiz"))
    # A renewal adds credits in all categories without expiration.
    run(db.grant_package(uid, 5, 1, 1, 1))
    _, wl3, sl3, ql3 = run(db.package_status(uid))
    assert wl3 == 2 and sl3 == 2 and ql3 == 4
    run(cleanup_users([uid]))


def test_stats_kinds_labels(pg):
    uid = 9_000_000_050 + int(time.time()) % 1000
    run(db.upsert_user(uid, "S", "", "uz"))
    run(db.add_file(uid, "ref", "R", "/tmp/fake2.docx", 1))
    kinds = run(db.stats_kinds())
    assert any(k["kind"] == "ref" for k in kinds if k["n"] >= 1)
    s = run(db.stats())
    assert "premium" in s and s["total"] >= 1
    run(db.POOL.execute("DELETE FROM user_files WHERE path = '/tmp/fake2.docx'"))
    run(cleanup_users([uid]))


def test_expiring_and_expired(pg):
    now = int(time.time())
    soon_uid = 9_000_000_060 + now % 1000
    done_uid = 9_000_000_061 + now % 1000
    run(db.upsert_user(soon_uid, "E", "", "uz"))
    run(db.upsert_user(done_uid, "D2", "", "ru"))
    run(db.POOL.execute("UPDATE users SET premium_until=$1 WHERE id=$2", now + 5 * 3600, soon_uid))
    run(db.POOL.execute("UPDATE users SET premium_until=$1 WHERE id=$2", now - 3600, done_uid))
    soon = run(db.expiring_premiums(86400))
    assert any(u["id"] == soon_uid for u in soon)
    expired = run(db.expired_premiums())
    assert any(u["id"] == done_uid for u in expired)
    run(cleanup_users([soon_uid, done_uid]))


async def cleanup_users(ids, files=False):
    await db.POOL.execute("DELETE FROM users WHERE id = ANY($1::bigint[])", ids)
    if files:
        await db.POOL.execute("DELETE FROM user_files WHERE path LIKE '/tmp/fake%'")


def test_payment_confirmation_is_atomic(pg):
    async def scenario():
        uid = 9_100_000_001
        await db.upsert_user(uid, "Paid", "", "uz")
        payment = await db.create_payment(uid, "proof", "unique-proof", "photo")
        same = await db.create_payment(uid, "new-file-id", "unique-proof", "photo")
        assert same["id"] == payment["id"]
        outcomes = await asyncio.gather(
            *(db.decide_payment(payment["id"], admin, True) for admin in range(10))
        )
        assert sum(result is not None for result in outcomes) == 1
        user = await db.get_user(uid)
        assert user["premium_until"] == 0
        assert user["quiz_left"] == config.PRO_QUIZ
        assert user["word_left"] == config.PRO_WORD
        assert await db.decide_payment(payment["id"], 99, False) is None
        assert await db.pending_payments() == []

    run(scenario())


def test_payment_snapshot_survives_price_change(pg, monkeypatch):
    async def scenario():
        uid = 9_100_000_002
        await db.upsert_user(uid, "Snapshot", "", "uz")
        payment = await db.create_payment(uid, "proof2", "unique-proof2", "photo")
        old_word = payment["word"]
        monkeypatch.setattr(config, "PRO_WORD", old_word + 100)
        await db.decide_payment(payment["id"], 1, True)
        assert (await db.get_user(uid))["word_left"] == old_word

    run(scenario())


def test_quota_parallel_reservation_and_refund(pg):
    async def scenario():
        uid = 9_100_000_003
        await db.upsert_user(uid, "Parallel", "", "uz")
        jobs = await asyncio.gather(
            *(db.reserve_job(uid, "word", {"mode": "ref"}) for _ in range(12))
        )
        accepted = [job for job in jobs if job and "id" in job]
        assert len(accepted) == 1
        assert (await db.get_user(uid))["daily_used"] == 1
        outcomes = await asyncio.gather(*(db.refund_job(accepted[0]["id"]) for _ in range(10)))
        assert outcomes.count(True) == 1
        assert (await db.get_user(uid))["daily_used"] == 0

    run(scenario())


def test_completed_job_is_not_refunded_and_checks_owner(pg):
    async def scenario():
        uid = 9_100_000_004
        await db.upsert_user(uid, "Complete", "", "uz")
        job = await db.reserve_job(uid, "word", {"mode": "ref", "topic": "Test", "count": 3})
        fid = await db.complete_job(job["id"], uid, "ref", "Title", "/tmp/audit.docx", 12)
        assert await db.refund_job(job["id"]) is False
        assert (await db.get_user(uid))["daily_used"] == 1
        assert (await db.get_user(uid))["docs"] == 1
        assert await db.get_file(fid, uid + 100) is None
        assert await db.get_job(job["id"], uid + 100) is None
        assert (await db.get_job(job["id"], uid))["payload"]["topic"] == "Test"

    run(scenario())


def test_pro_uses_free_quota_after_package_exhaustion(pg):
    async def scenario():
        uid = 9_100_000_005
        await db.upsert_user(uid, "Fallback", "", "uz")
        await db.grant_package(uid, 30, 1, 0)
        paid = await db.reserve_job(uid, "word", {})
        assert paid["source"] == "word"
        await db.complete_job(paid["id"], uid, "ref", "", "/tmp/fallback.docx", 1)
        free = await db.reserve_job(uid, "word", {})
        assert free["source"] == "free"
        await db.refund_job(free["id"])
        assert (await db.get_user(uid))["daily_used"] == 0

    run(scenario())


def test_quota_exhaustion_and_midnight_refund(pg):
    async def scenario():
        uid = 9_100_000_006
        await db.upsert_user(uid, "Daily", "", "uz")
        await db.POOL.execute(
            "UPDATE users SET daily_used=$1,daily_reset=$2 WHERE id=$3",
            config.DAILY_LIMIT,
            db.day_start(),
            uid,
        )
        assert await db.reserve_job(uid, "word", {}) is None
        await db.POOL.execute("UPDATE users SET daily_reset=0 WHERE id=$1", uid)
        job = await db.reserve_job(uid, "word", {})
        assert (await db.get_user(uid))["daily_used"] == 1
        await db.POOL.execute(
            "UPDATE users SET daily_reset=$1,daily_used=2 WHERE id=$2", db.day_start() + 86400, uid
        )
        await db.refund_job(job["id"])
        assert (await db.get_user(uid))["daily_used"] == 2

    run(scenario())


def test_restart_recovery_refunds_once(pg):
    async def scenario():
        uid = 9_100_000_007
        await db.upsert_user(uid, "Restart", "", "uz")
        await db.reserve_job(uid, "slide", {})
        assert uid in await db.recover_interrupted_jobs()
        assert uid not in await db.recover_interrupted_jobs()
        assert (await db.get_user(uid))["daily_used"] == 0

    run(scenario())


def test_notifications_do_not_repeat_across_restarts(pg):
    async def scenario():
        uid = 9_100_000_008
        await db.upsert_user(uid, "Notice", "", "uz")
        period = int(time.time()) + 3600
        await db.POOL.execute("UPDATE users SET premium_until=$1 WHERE id=$2", period, uid)
        assert any(u["id"] == uid for u in await db.expiring_premiums(86400))
        await db.mark_premium_notified(uid, period, True)
        assert not any(u["id"] == uid for u in await db.expiring_premiums(86400))
        await db.POOL.execute("UPDATE users SET premium_until=$1 WHERE id=$2", period + 1, uid)
        assert any(u["id"] == uid for u in await db.expiring_premiums(86400))

    run(scenario())


def test_fsm_serializable_merge_and_restart(pg):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey

    from services.fsm_pg import PostgresStorage

    async def scenario():
        key = StorageKey(bot_id=1, chat_id=999, user_id=999)
        state = FSMContext(storage=PostgresStorage(), key=key)
        await state.set_state("merge")
        await state.update_data(merge_files=["/tmp/one.pdf"])
        await asyncio.gather(state.update_data(lang="uz"), state.update_data(name="Test"))
        restored = FSMContext(storage=PostgresStorage(), key=key)
        assert await restored.get_state() == "merge"
        assert await restored.get_data() == {
            "merge_files": ["/tmp/one.pdf"],
            "lang": "uz",
            "name": "Test",
        }

    run(scenario())


def test_initial_migration_keeps_docs_and_never_reduces_them(pg):
    uid = 9_100_000_009
    run(db.upsert_user(uid, "Legacy", "", "uz", docs=12))
    assert run(db.get_user(uid))["docs"] == 12
    run(db.upsert_user(uid, "Legacy", "", "uz", docs=3))
    assert run(db.get_user(uid))["docs"] == 12


def test_expired_files_are_hidden_and_pruned(pg):
    async def scenario():
        uid = 9_100_000_010
        await db.upsert_user(uid, "Expiry", "", "uz")
        fid = await db.add_file(uid, "ref", "", "/tmp/old.docx", 1)
        await db.POOL.execute("UPDATE user_files SET created_at=1 WHERE id=$1", fid)
        assert await db.get_file(fid, uid) is None
        assert await db.list_files(uid) == []
        assert await db.count_files(uid) == 0
        await db.prune_expired_records()
        assert await db.POOL.fetchval("SELECT 1 FROM user_files WHERE id=$1", fid) is None

    run(scenario())


def test_admin_secret_is_one_time_and_support_mapping_is_scoped(pg):
    async def scenario():
        outcomes = await asyncio.gather(
            *(db.claim_admin_secret(uid, "test-digest") for uid in (91, 92))
        )
        assert outcomes.count(True) == 1
        await db.remember_support_reply(10, 20, 30)
        assert await db.support_recipient(10, 20) == 30
        assert await db.support_recipient(11, 20) is None

    run(scenario())


def test_tashkent_midnight_boundary():
    from datetime import datetime, timezone

    before = datetime(2026, 10, 8, 18, 59, tzinfo=timezone.utc).timestamp()
    after = before + 120
    assert db.day_start(after) - db.day_start(before) == 86400


def test_real_job_flow_refunds_failure_and_keeps_delivered_file(pg, tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    import bot
    from tests.test_workflows import State, message

    async def scenario():
        uid = 9_100_000_011
        await db.upsert_user(uid, "Workflow", "", "uz")
        monkeypatch.setattr(config, "FILES_DIR", tmp_path)
        state = State({"lang": "uz"})
        msg = message(uid=uid)

        async def failed():
            raise RuntimeError("Provider failed")

        await bot.run_document_job(msg, state, uid, "word", "ref", "Report", failed, "done_ref")
        assert (await db.get_user(uid))["daily_used"] == 0
        assert (await db.get_user(uid))["docs"] == 0

        async def successful():
            path = bot.new_file(uid, "ref", ".docx")
            path.write_bytes(b"completed document")
            return path, 0

        monkeypatch.setattr(
            bot, "send_file", AsyncMock(side_effect=RuntimeError("Telegram delivery failed"))
        )
        await bot.run_document_job(msg, state, uid, "word", "ref", "Report", successful, "done_ref")
        assert (await db.get_user(uid))["daily_used"] == 1
        assert (await db.get_user(uid))["docs"] == 1
        files = await db.list_files(uid)
        assert len(files) == 1
        assert Path(files[0]["path"]).read_bytes() == b"completed document"

    from pathlib import Path

    run(scenario())


def test_non_expiring_quiz_credit_and_repeated_refund(pg):
    async def scenario():
        uid = 9_200_000_001
        await db.upsert_user(uid, "Quiz credit", "", "uz")
        await db.grant_package(uid, 1, 0, 0, 1)
        await db.POOL.execute("UPDATE users SET premium_until=1 WHERE id=$1", uid)
        assert await db.package_status(uid) == (True, 0, 0, 1)
        job = await db.reserve_job(uid, "quiz", {})
        assert job["source"] == "quiz"
        assert await db.package_status(uid) == (False, 0, 0, 0)
        results = await asyncio.gather(*(db.refund_job(job["id"]) for _ in range(10)))
        assert results.count(True) == 1
        assert await db.package_status(uid) == (True, 0, 0, 1)
        assert not any(u["id"] == uid for u in await db.expired_premiums())

    run(scenario())


def test_interactive_quiz_reservation_survives_restart_and_charges_once(pg, monkeypatch):
    from unittest.mock import AsyncMock

    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey

    import bot
    from services.fsm_pg import PostgresStorage
    from tests.test_workflows import message

    async def scenario():
        uid = 9_200_000_002
        await db.upsert_user(uid, "Last quiz", "", "uz")
        await db.grant_package(uid, 0, 0, 0, 1)
        storage = PostgresStorage()
        key = StorageKey(bot_id=1, chat_id=777, user_id=uid)
        state = FSMContext(storage=storage, key=key)
        await state.set_state(bot.St.quiz_time)
        await state.update_data(
            lang="uz",
            quiz_n=2,
            quiz_bank=[{"q": "Question?", "options": ["A", "B"], "answer": 0}] * 2,
        )
        msg = message(uid=uid, text="1")
        sent = message(uid=999)
        msg.answer = AsyncMock(return_value=sent)
        monkeypatch.setattr(bot, "schedule_quiz", lambda *args: None)
        await bot.on_quiz_time(msg, state)
        assert await db.package_status(uid) == (False, 0, 0, 0)
        assert uid not in await db.recover_interrupted_jobs()
        restored = FSMContext(storage=PostgresStorage(), key=key)
        await bot.finish_quiz(sent, restored)
        await bot.finish_quiz(sent, restored)
        quiz = (await restored.get_data())["quiz"]
        assert quiz["done"] and quiz["pro"]
        assert (await db.get_user(uid))["docs"] == 1
        assert await db.refund_job(quiz["job_id"]) is False
        sent.answer.assert_awaited_once()  # Last paid quiz still receives Pro analysis.
        assert await db.count_files(uid) == 0

    run(scenario())


def test_orphaned_interactive_quiz_is_refunded_on_restart(pg):
    async def scenario():
        uid = 9_200_000_003
        await db.upsert_user(uid, "Orphan", "", "uz")
        await db.grant_package(uid, 0, 0, 0, 1)
        await db.reserve_job(uid, "quiz", {"interactive": True})
        assert uid in await db.recover_interrupted_jobs()
        assert await db.package_status(uid) == (True, 0, 0, 1)

    run(scenario())


def test_quiz_payment_snapshot_and_topup_add_all_categories(pg, monkeypatch):
    async def scenario():
        uid = 9_200_000_004
        await db.upsert_user(uid, "Topup", "", "uz")
        await db.grant_package(uid, 0, 1, 2, 3)
        payment = await db.create_payment(uid, "quiz-proof", "quiz-unique", "photo")
        monkeypatch.setattr(config, "PRO_QUIZ", 100)
        await db.decide_payment(payment["id"], 1, True)
        assert await db.package_status(uid) == (
            True,
            1 + payment["word"],
            2 + payment["slide"],
            3 + payment["quiz"],
        )
        assert (await db.get_user(uid))["premium_until"] == 0

    run(scenario())
