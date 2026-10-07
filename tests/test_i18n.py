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
            assert theirs == placeholders, f"{key}: {lang} placeholder farqi {theirs} vs {placeholders}"