import asyncio
import time

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
    try:
        run(db.init())
    except Exception as e:
        pytest.skip(f"PostgreSQL mavjud emas: {e}")
    yield db.POOL
    run(db.POOL.close())


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
    _, _, _, ql2 = run(db.package_status(uid))
    assert ql2 == 3
    # takror xarid kamaytirmaydi (GREATEST)
    run(db.grant_package(uid, 5, 1, 1, 1))
    _, wl3, sl3, ql3 = run(db.package_status(uid))
    assert wl3 == 1 and sl3 == 1 and ql3 == 3
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
    run(db.POOL.execute(
        "UPDATE users SET premium_until=$1 WHERE id=$2", now + 5 * 3600, soon_uid))
    run(db.POOL.execute(
        "UPDATE users SET premium_until=$1 WHERE id=$2", now - 3600, done_uid))
    soon = run(db.expiring_premiums(86400))
    assert any(u["id"] == soon_uid for u in soon)
    expired = run(db.expired_premiums())
    assert any(u["id"] == done_uid for u in expired)
    run(cleanup_users([soon_uid, done_uid]))


async def cleanup_users(ids, files=False):
    await db.POOL.execute("DELETE FROM users WHERE id = ANY($1::bigint[])", ids)
    if files:
        await db.POOL.execute("DELETE FROM user_files WHERE path LIKE '/tmp/fake%'")