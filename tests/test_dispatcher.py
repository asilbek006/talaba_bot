"""Exercise real aiogram routing/FSM with an offline Telegram transport."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, Update, User

import bot as app
import config


class TelegramStub(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.counter = 100

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = method.__api_method__
        if name in ("answerCallbackQuery", "deleteMessage"):
            return True
        self.counter += 1
        document = None
        if name == "sendDocument":
            document = {
                "file_id": "offline-document",
                "file_unique_id": "offline-unique",
                "file_name": "result.docx",
            }
        return Message(
            message_id=getattr(method, "message_id", None) or self.counter,
            date=datetime.now(timezone.utc),
            chat=Chat(id=777, type="private"),
            from_user=User(id=123456, is_bot=True, first_name="Bot"),
            text=getattr(method, "text", None),
            caption=getattr(method, "caption", None),
            document=document,
            reply_markup=getattr(method, "reply_markup", None),
        )


def test_registration_generation_and_back_through_dispatcher(tmp_path, monkeypatch):
    users = {}

    async def get_user(uid):
        return users.get(uid)

    async def save(uid, name, username, lang):
        users[uid] = {"id": uid, "name": name, "lang": lang}
        return True

    monkeypatch.setattr(config, "FILES_DIR", tmp_path)
    monkeypatch.setattr(app.db, "get_user", get_user)
    monkeypatch.setattr(app.db, "save_user", save)
    monkeypatch.setattr(app.db, "touch_user", AsyncMock())
    monkeypatch.setattr(app.db, "admin_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(app.db, "reserve_job", AsyncMock(return_value={"id": 1}))
    monkeypatch.setattr(app.db, "complete_job", AsyncMock(return_value=1))
    monkeypatch.setattr(app.db, "refund_job", AsyncMock(return_value=False))
    monkeypatch.setattr(
        app.gen,
        "gen_test",
        lambda *args: [
            {"q": f"Savol {i}?", "options": ["a", "b", "c", "d"], "answer": 0} for i in range(5)
        ],
    )

    async def scenario():
        session = TelegramStub()
        telegram = Bot("123456:offline-test-token", session=session)
        storage = MemoryStorage()
        dp = app.create_dispatcher(storage)
        update_id = 0

        async def text(value):
            nonlocal update_id
            update_id += 1
            msg = {
                "message_id": update_id,
                "date": int(datetime.now(timezone.utc).timestamp()),
                "chat": {"id": 777, "type": "private"},
                "from": {"id": 777, "is_bot": False, "first_name": "Student"},
                "text": value,
            }
            await dp.feed_update(
                telegram, Update.model_validate({"update_id": update_id, "message": msg})
            )

        async def callback(value, document=False):
            nonlocal update_id
            update_id += 1
            msg = {
                "message_id": 100,
                "date": int(datetime.now(timezone.utc).timestamp()),
                "chat": {"id": 777, "type": "private"},
                "from": {"id": 123456, "is_bot": True, "first_name": "Bot"},
            }
            if document:
                msg["document"] = {
                    "file_id": "offline-document",
                    "file_unique_id": "offline-unique",
                    "file_name": "result.docx",
                }
            else:
                msg["text"] = "menu"
            await dp.feed_update(
                telegram,
                Update.model_validate(
                    {
                        "update_id": update_id,
                        "callback_query": {
                            "id": str(update_id),
                            "from": {"id": 777, "is_bot": False, "first_name": "Student"},
                            "chat_instance": "test",
                            "message": msg,
                            "data": value,
                        },
                    }
                ),
            )

        await text("/start")
        await callback("l:uz")
        await text("Student")
        await callback("m:test")
        await text("Matematika")
        assert session.calls[-1].text == app.t("uz", "ask_count_qu")
        await text("5")
        sent = [call for call in session.calls if call.__api_method__ == "sendDocument"]
        assert len(sent) == 1
        from docx import Document

        assert any("Savol 0?" in p.text for p in Document(sent[0].document.path).paragraphs)
        buttons = [b.callback_data for row in sent[0].reply_markup.inline_keyboard for b in row]
        assert {"topdf:1", "again:1"} <= set(buttons)
        before = len([call for call in session.calls if call.__api_method__ == "sendMessage"])
        await callback("back", document=True)
        after = len([call for call in session.calls if call.__api_method__ == "sendMessage"])
        assert after == before + 1
        await storage.close()
        await telegram.session.close()

    asyncio.run(scenario())
