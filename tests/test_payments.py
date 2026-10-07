import config
import services.payments as payments


def test_is_ready_returns_bool():
    assert payments.is_ready() in (True, False)


def test_card_info_shape():
    info = payments.card_info()
    assert set(info) == {"card", "holder", "amount"}
    assert isinstance(info["card"], str) and isinstance(info["amount"], str)


def test_card_never_embedded_in_code():
    """Haqiqiy karta raqami kod/README da bo'lmasligi kerak — faqat .env dan keladi.
    (0000.../1111... ko'rinishidagi dummy misollar ruxsat etilgan.)"""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent
    for f in (root / "bot.py", root / "config.py",
              root / "services" / "payments.py", root / "README.md"):
        for m in re.finditer(r"\b\d{16}\b", f.read_text()):
            n = m.group(0)
            if n == n[0] * 16:
                continue  # dummy placeholder
            raise AssertionError(f"Karta raqami {f.name} ichiga tushib qolgan: {n}")


def test_env_ignored_in_git():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    gi = (root / ".gitignore").read_text()
    assert ".env" in gi, ".env .gitignore'da yo'q — sir tashqariga chiqishi mumkin!"


def test_pro_package_config_positive():
    assert config.PRO_WORD >= 0
    assert config.PRO_SLIDE >= 0
    assert config.PREMIUM_DAYS >= 1


class _FakeCall:
    def __init__(self, data):
        self.from_user = type("U", (), {"id": 1})
        self.data = data
        self.message = type("M", (), {
            "edit_text": _async_noop,
            "edit_caption": _async_noop,
            "caption": "",
        })
        self.answered = False

    async def answer(self, *a, **k):
        self.answered = True


class _FakeState:
    def __init__(self):
        self.data = {"lang": "uz"}
        self.state = None

    async def get_data(self):
        return self.data

    async def set_state(self, s):
        self.state = s


async def _async_noop(*a, **k):
    return None


def test_cb_premium_buy_renders_when_ready(monkeypatch):
    """pay_manual t() chaqiruvida barcha placeholder (w/s) berilishi kerak."""
    import asyncio
    import bot
    monkeypatch.setattr(bot.payments, "is_ready", lambda: True)
    call, state = _FakeCall("premium_buy"), _FakeState()
    asyncio.run(bot.cb_premium_buy(call, state))
    assert state.state is not None  # pay_photo holatiga o'tdi (KeyError bo'lmasa)
    assert call.answered


def test_cb_premium_buy_graceful_when_not_configured(monkeypatch):
    import asyncio
    import bot
    monkeypatch.setattr(bot.payments, "is_ready", lambda: False)
    monkeypatch.setattr(bot.db, "admin_ids", _async_admin_ids)
    call, state = _FakeCall("premium_buy"), _FakeState()
    asyncio.run(bot.cb_premium_buy(call, state))
    assert call.answered


async def _async_admin_ids():
    return {1}


def test_pay_decision_approve_renders_pro_activated(monkeypatch):
    import asyncio
    import bot

    async def fake_admin_ids():
        return {1}
    sent = []

    async def fake_send(uid, text, *a, **k):
        sent.append(text)
    async def fake_get_user(uid):
        return {"lang": "uz"}
    monkeypatch.setattr(bot.db, "admin_ids", fake_admin_ids)
    monkeypatch.setattr(bot.db, "get_user", fake_get_user)
    monkeypatch.setattr(bot.db, "grant_package", _async_noop)
    monkeypatch.setattr(bot, "notify_admin", _async_noop)
    monkeypatch.setattr(bot, "user_name", _async_noop)
    fake_bot = type("B", (), {})
    fake_bot.send_message = fake_send
    call = _FakeCall("pay:ok:777")
    asyncio.run(bot._pay_decision(call, fake_bot, ok=True))
    assert any("Pro" in s for s in sent)  # pro_activated qaytarildi, KeyError yo'q