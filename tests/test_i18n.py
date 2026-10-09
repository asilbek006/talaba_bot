import re
from pathlib import Path

import i18n

ROOT = Path(__file__).resolve().parent.parent


def test_all_langs_have_same_keys():
    sets = [set(i18n.LANGS[ln]) for ln in i18n.LANGS]
    assert sets[0] == sets[1] == sets[2], "Til so'zlari to'plami farq qiladi"


def test_used_keys_exist_in_all_langs():
    src = ""
    for f in (ROOT / "bot.py", ROOT / "services" / "generator.py"):
        src += f.read_text()
    used = set(re.findall(r'\bt\([^,]+,\s*"([a-z_0-9]+)"', src))
    assert used, "t() kalitlari topilmadi"
    for key in used:
        for lang in i18n.LANGS:
            assert key in i18n.LANGS[lang], f"{key} {lang}da yo'q"


def test_format_placeholders_match():
    src = open(ROOT / "bot.py").read() + open(ROOT / "services" / "generator.py").read()
    for key in set(re.findall(r'\bt\([^,]+,\s*"([a-z_0-9]+)"', src)):
        tmpl = i18n.LANGS["uz"].get(key, "")
        placeholders = set(re.findall(r"\{(\w+)\}", tmpl))
        for lang in i18n.LANGS:
            theirs = set(re.findall(r"\{(\w+)\}", i18n.LANGS[lang].get(key, "")))
            assert theirs == placeholders, (
                f"{key}: {lang} placeholder farqi {theirs} vs {placeholders}"
            )


def test_t_escapes_dynamic_html():
    """t() qo'shish orqali uzatilgan qiymatlar HTML parse'dan buzilmasligi kerak."""
    for lang in i18n.LANGS:
        out = i18n.t(
            lang,
            "pay_manual",
            card="6262<&>5585",
            holder="A & B",
            amount="50 000",
            days=30,
            w=5,
            s=3,
        )
        assert "<code>6262&lt;&amp;&gt;5585</code>" in out
        assert "A &amp; B" in out


def test_bad_range_key():
    for lang in i18n.LANGS:
        out = i18n.t(lang, "bad_range", lo=3, hi=30)
        assert "3" in out and "30" in out


def test_payment_amount_is_configurable_in_every_language():
    for lang in i18n.LANGS:
        rendered = i18n.t(
            lang, "pay_manual", card="0000", holder="Audit", amount="99999", days=30, w=5, s=5
        )
        assert "99999" in rendered
        assert "15 000" not in rendered
