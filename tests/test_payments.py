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