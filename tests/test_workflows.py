import asyncio
import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import SimpleEventIsolation

import bot
import config
from i18n import t


class State:
    def __init__(self, data=None, state=None):
        self.data = data or {}
        self.state = state
        self.key = StorageKey(bot_id=1, chat_id=777, user_id=777)

    async def get_data(self):
        return copy.deepcopy(self.data)

    async def update_data(self, **kw):
        json.dumps(kw)  # Match the real PostgreSQL serialization contract.
        self.data.update(kw)

    async def set_state(self, state):
        self.state = getattr(state, "state", state)

    async def get_state(self):
        return self.state

    async def clear(self):
        self.data = {}
        self.state = None


def message(uid=777, text="5"):
    return NS(
        from_user=NS(id=uid, username="audit"),
        chat=NS(id=777),
        message_id=42,
        text=text,
        document=None,
        photo=None,
        media_group_id=None,
        answer=AsyncMock(return_value=NS(delete=AsyncMock())),
        answer_document=AsyncMock(),
        edit_text=AsyncMock(),
        edit_caption=AsyncMock(),
        delete=AsyncMock(),
        reply=AsyncMock(),
        caption="",
        reply_to_message=None,
    )


def call(data, msg=None, uid=777):
    return NS(from_user=NS(id=uid), data=data, message=msg or message(uid=999), answer=AsyncMock())


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FILES_DIR", tmp_path)
    monkeypatch.setattr(bot, "_event_isolation", SimpleEventIsolation())
    monkeypatch.setattr(bot.db, "touch_user", AsyncMock())
    monkeypatch.setattr(bot.db, "get_user", AsyncMock(return_value={"lang": "uz", "name": "Audit"}))


def quiz_state(deadline=None, done=False):
    return State(
        {
            "lang": "uz",
            "quiz": {
                "id": "session",
                "uid": 777,
                "msg_id": 42,
                "qs": [{"q": "Question?", "options": ["one", "two"], "answer": 0}],
                "i": 0,
                "score": 0,
                "n": 1,
                "done": done,
                "deadline": deadline or time.time() + 60,
            },
        },
        bot.St.quiz_active.state,
    )


def test_quiz_count_moves_to_time_step():
    state = State({"lang": "uz", "quiz_bank": [{}] * 5})
    asyncio.run(bot.on_quiz_count(message(), state))
    assert state.data["quiz_n"] == 5
    assert state.state == bot.St.quiz_time.state


def test_invalid_quiz_count_keeps_user_in_step():
    state = State({"lang": "uz", "quiz_bank": [{}] * 5}, bot.St.quiz_count.state)
    msg = message(text="0")
    asyncio.run(bot.on_quiz_count(msg, state))
    assert "quiz_n" not in state.data
    assert state.state == bot.St.quiz_count.state


def test_test_generation_asks_human_readable_count():
    msg = message(text="Matematika")
    state = State({"lang": "uz", "mode": "test"})
    asyncio.run(bot.on_topic(msg, state))
    assert msg.answer.await_args.args[0] == t("uz", "ask_count_qu")


def test_tools_are_reachable_in_every_language():
    for lang in ("uz", "ru", "en"):
        buttons = {
            button.callback_data for row in bot.kb_menu(lang).inline_keyboard for button in row
        }
        assert {"m:tools", "lang", "m:premium"} <= buttons


def test_back_from_document_sends_menu():
    msg = message()
    msg.document = NS(file_name="result.docx")
    state = State({"lang": "uz"})
    asyncio.run(bot.cb_back(call("back", msg), state))
    msg.edit_text.assert_not_awaited()
    msg.answer.assert_awaited_once()
    assert msg.answer.await_args.kwargs["reply_markup"] == bot.kb_menu("uz")


def test_merge_uploads_are_serializable_and_unique(monkeypatch):
    async def scenario():
        msg = message()
        msg.document = NS(file_name="test.pdf", file_size=5)

        async def download(document, destination):
            Path(destination).write_bytes(b"PDF placeholder")

        state = State({"lang": "uz", "merge_files": []})
        for _ in range(2):
            await bot.on_merge_file(msg, state, NS(download=download))
        paths = state.data["merge_files"]
        assert len(set(paths)) == 2
        assert all(isinstance(p, str) and Path(p).is_file() for p in paths)

    asyncio.run(scenario())


@pytest.mark.parametrize("text", ["✅ Tayyor", "✅ Готово", "✅ Done", "ok"])
def test_localized_merge_done_is_accepted(monkeypatch, text):
    merge = AsyncMock()
    monkeypatch.setattr(bot, "do_merge", merge)
    asyncio.run(bot.on_merge_done(message(text=text), State({"lang": "uz"})))
    merge.assert_awaited_once()


def test_quiz_pro_analysis_checks_student_id(monkeypatch):
    status = AsyncMock(return_value=(True, 1, 1))
    monkeypatch.setattr(bot.db, "package_status", status)
    state = quiz_state()
    state.data["quiz"]["score"] = 1
    msg = message(uid=999)
    asyncio.run(bot.finish_quiz(msg, state))
    status.assert_awaited_once_with(777)
    msg.answer.assert_awaited_once()
    assert state.data["quiz"]["done"]


def test_late_quiz_answer_does_not_receive_points(monkeypatch):
    finish = AsyncMock()
    monkeypatch.setattr(bot, "finish_quiz", finish)
    state = quiz_state(time.time() - 10)
    asyncio.run(bot.on_quiz_answer(call("q:session:0:0"), state))
    assert state.data["quiz"]["score"] == 0
    finish.assert_awaited_once()


@pytest.mark.parametrize("data", ["q:old:0:0", "q:session:0:999", "q:session:garbage:0", "q:0:0"])
def test_stale_and_malformed_quiz_callbacks_are_ignored(data):
    state = quiz_state()
    asyncio.run(bot.on_quiz_answer(call(data), state))
    assert state.data["quiz"]["i"] == 0


def test_other_users_cannot_answer_quiz():
    state = quiz_state()
    asyncio.run(bot.on_quiz_answer(call("q:session:0:0", uid=888), state))
    assert state.data["quiz"]["i"] == 0


def test_old_quiz_timer_cannot_finish_new_quiz(monkeypatch):
    finish = AsyncMock()
    monkeypatch.setattr(bot, "finish_quiz", finish)
    state = quiz_state(time.time() - 1)
    asyncio.run(bot.quiz_timeout_task(0, message(), state, "old"))
    finish.assert_not_awaited()


def test_quiz_timer_does_not_expire_early(monkeypatch):
    finish = AsyncMock()
    monkeypatch.setattr(bot, "finish_quiz", finish)
    asyncio.run(bot.quiz_timeout_task(0, message(), quiz_state(), "session"))
    finish.assert_not_awaited()


def test_valid_quiz_timer_finishes_current_session(monkeypatch):
    finish = AsyncMock()
    monkeypatch.setattr(bot, "finish_quiz", finish)
    asyncio.run(bot.quiz_timeout_task(0, message(), quiz_state(time.time() - 1), "session"))
    finish.assert_awaited_once()


def test_payment_replay_never_sends_another_activation(monkeypatch):
    monkeypatch.setattr(bot.db, "admin_ids", AsyncMock(return_value=[777]))
    decide = AsyncMock(return_value=None)
    monkeypatch.setattr(bot.db, "decide_payment", decide)
    api = NS(send_message=AsyncMock())
    callback = call("pay:ok:v2:42")
    asyncio.run(bot._pay_decision(callback, api, True))
    api.send_message.assert_not_awaited()
    assert "allaqachon" in callback.answer.await_args.args[0]


def test_nonadmin_cannot_decide_payment(monkeypatch):
    monkeypatch.setattr(bot.db, "admin_ids", AsyncMock(return_value=[999]))
    decide = AsyncMock()
    monkeypatch.setattr(bot.db, "decide_payment", decide)
    asyncio.run(bot._pay_decision(call("pay:ok:v2:42"), NS(), True))
    decide.assert_not_awaited()


def test_legacy_payment_buttons_cannot_grant(monkeypatch):
    monkeypatch.setattr(bot.db, "admin_ids", AsyncMock(return_value=[777]))
    decide = AsyncMock()
    monkeypatch.setattr(bot.db, "decide_payment", decide)
    asyncio.run(bot._pay_decision(call("pay:ok:777"), NS(), True))
    decide.assert_not_awaited()


def test_pdf_callback_uses_owned_file_record(monkeypatch):
    lookup = AsyncMock(return_value=None)
    convert = AsyncMock()
    monkeypatch.setattr(bot.db, "get_file", lookup)
    monkeypatch.setattr(bot.conv, "docx_to_pdf", convert)
    asyncio.run(bot.cb_topdf(call("topdf:123"), State({"lang": "uz"})))
    lookup.assert_awaited_once_with(123, 777)
    convert.assert_not_awaited()


def test_regenerate_uses_original_job_payload(monkeypatch):
    payload = {"lang": "uz", "mode": "ref", "topic": "Original topic", "count": 5}
    monkeypatch.setattr(bot.db, "get_job", AsyncMock(return_value={"payload": payload}))
    generate = AsyncMock()
    monkeypatch.setattr(bot, "generate", generate)
    state = State({"lang": "en", "mode": "xls", "topic": "New topic", "count": 10})
    asyncio.run(bot.cb_again(call("again:7"), state, NS()))
    assert state.data == payload
    generate.assert_awaited_once()


def test_queue_overflow_does_not_call_provider(monkeypatch):
    monkeypatch.setattr(
        bot, "_gemini_queue", config.GEMINI_MAX_CONCURRENT + config.GEMINI_QUEUE_LIMIT
    )
    called = []

    async def scenario():
        with pytest.raises(RuntimeError):
            await bot.call_ai(message(), State({"lang": "uz"}), lambda: called.append(True))

    asyncio.run(scenario())
    assert called == []


def test_queue_counter_recovers_on_notification_failure(monkeypatch):
    async def scenario():
        lock = asyncio.Semaphore(1)
        await lock.acquire()
        monkeypatch.setattr(bot, "_gemini_lock", lock)
        monkeypatch.setattr(bot, "_gemini_queue", 0)
        msg = message()
        msg.answer.side_effect = RuntimeError("Telegram unavailable")
        with pytest.raises(RuntimeError):
            await bot.call_ai(msg, State({"lang": "uz"}), lambda: None)
        assert bot._gemini_queue == 0 and lock.locked()
        lock.release()

    asyncio.run(scenario())


def test_support_reply_never_parses_user_supplied_ids(monkeypatch):
    msg = message(text="Admin answer")
    msg.reply_to_message = NS(message_id=123, text="ID: 888")
    monkeypatch.setattr(bot.db, "admin_ids", AsyncMock(return_value=[777]))
    monkeypatch.setattr(bot.db, "support_recipient", AsyncMock(return_value=None))
    api = NS(send_message=AsyncMock())
    asyncio.run(bot.any_text(msg, State({"lang": "uz"}), api))
    api.send_message.assert_not_awaited()


def test_album_handler_returns_without_blocking_collection(monkeypatch):
    async def scenario():
        entered = asyncio.Event()

        async def process(key, state, api):
            entered.set()

        monkeypatch.setattr(bot, "process_album", process)
        monkeypatch.setattr(bot, "_album_messages", {})
        monkeypatch.setattr(bot, "_album_tasks", {})
        msg = message()
        msg.media_group_id = "album"
        msg.photo = [NS(file_id="p")]
        await bot.any_photo(msg, State({"lang": "uz"}), NS())
        await entered.wait()
        assert len(bot._album_messages[(777, "album")]) == 1
        await asyncio.gather(*bot._album_tasks.values())

    asyncio.run(scenario())


def test_legacy_archive_migrates_to_durable_user_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path / "old_install")
    monkeypatch.setattr(config, "FILES_DIR", tmp_path / "new_storage")
    legacy = config.BASE_DIR / "files" / "shared_name.docx"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy document")
    monkeypatch.setattr(bot.db, "file_owner_count", AsyncMock(return_value=1))
    relocate = AsyncMock()
    monkeypatch.setattr(bot.db, "relocate_file", relocate)
    record = {"id": 12, "path": str(legacy)}
    migrated = asyncio.run(bot.archive_path(777, record))
    assert migrated.read_bytes() == b"legacy document"
    assert bot.file_store.owned_path(777, migrated)
    relocate.assert_awaited_once_with(12, 777, str(migrated))


def test_legacy_shared_or_external_archive_is_never_delivered(tmp_path, monkeypatch):
    path = tmp_path / "shared.docx"
    path.write_bytes(b"ambiguous owner")
    monkeypatch.setattr(bot.db, "file_owner_count", AsyncMock(return_value=2))
    relocate = AsyncMock()
    monkeypatch.setattr(bot.db, "relocate_file", relocate)
    with pytest.raises(FileNotFoundError):
        asyncio.run(bot.archive_path(777, {"id": 1, "path": str(path)}))
    monkeypatch.setattr(config, "FILES_DIR", tmp_path / "other_root")
    monkeypatch.setattr(bot.db, "file_owner_count", AsyncMock(return_value=1))
    with pytest.raises(FileNotFoundError):
        asyncio.run(bot.archive_path(777, {"id": 1, "path": str(path)}))
    relocate.assert_not_awaited()


def test_oversized_result_is_not_charged_or_indexed(tmp_path, monkeypatch):
    path = tmp_path / "large.pdf"
    path.write_bytes(b"0123456789")
    monkeypatch.setattr(config, "MAX_UPLOAD", 5)
    complete = AsyncMock()
    monkeypatch.setattr(bot.db, "complete_job", complete)
    with pytest.raises(ValueError):
        asyncio.run(bot.keep(777, "pdf", "Result", path, job_id=1))
    complete.assert_not_awaited()


def test_approved_payment_notifies_customer_even_if_admin_ui_fails(monkeypatch):
    monkeypatch.setattr(bot.db, "admin_ids", AsyncMock(return_value=[777]))
    payment = {"id": 42, "user_id": 888, "days": 30, "word": 5, "slide": 5}
    monkeypatch.setattr(bot.db, "decide_payment", AsyncMock(return_value=payment))
    monkeypatch.setattr(bot, "notify_admin", AsyncMock())
    callback = call("pay:ok:v2:42")
    callback.answer.side_effect = RuntimeError("callback expired")
    api = NS(send_message=AsyncMock())
    asyncio.run(bot._pay_decision(callback, api, True))
    assert api.send_message.await_args.args[0] == 888


def test_admin_secret_accepts_unicode_without_type_error(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_SECRET", "yangi-maxfiy-🔐")
    monkeypatch.setattr(bot, "_admin_attempts", {})
    monkeypatch.setattr(bot.db, "admin_ids", AsyncMock(return_value=[]))
    claim = AsyncMock(return_value=True)
    monkeypatch.setattr(bot.db, "claim_admin_secret", claim)
    monkeypatch.setattr(bot, "notify_admin", AsyncMock())
    asyncio.run(bot.cmd_admin(message(text="/admin yangi-maxfiy-🔐"), NS()))
    claim.assert_awaited_once()
