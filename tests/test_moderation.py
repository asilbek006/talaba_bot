from pathlib import Path
import pytest
import services.moderation as mod
import services.badwords as badwords


def test_insult_detection():
    # Uzbek insults
    assert mod.is_insult("dalbayob") is True
    assert mod.is_insult("jallab") is True
    assert mod.is_insult("ahmoqsan") is True
    assert mod.is_insult("qotoqbosh") is True
    assert mod.is_insult("qoʻtoq") is True  # with okina
    assert mod.is_insult("itvachcha") is True

    # Cyrillic insults
    assert mod.is_insult("далбаёб") is True
    assert mod.is_insult("сука") is True
    assert mod.is_insult("ахмоқ") is True

    # Foreign insults
    assert mod.is_insult("fuck you") is True
    assert mod.is_insult("bitch") is True
    assert mod.is_insult("asshole") is True

    # Clean text
    assert mod.is_insult("Assalomu alaykum, referat kerak") is False
    assert mod.is_insult("Iqtisodiyot nazariyasi") is False
    assert mod.is_insult("Fizika va astronomiya") is False


def test_personal_query_detection():
    # Asilbek Davlatov queries
    assert mod.is_personal_query("Asilbek Davlatov haqida") is True
    assert mod.is_personal_query("Davlatov Asilbek kim?") is True
    assert mod.is_personal_query("Davlatov haqida ma'lumot bering") is True
    assert mod.is_personal_query("Asilbek kim") is True
    assert mod.is_personal_query("Asilbekning tarjimai holi") is True
    assert mod.is_personal_query("Асилбек Давлатов хакида") is True

    # Normal queries shouldn't trigger personal query
    assert mod.is_personal_query("Alisher Navoiy haqida referat") is False
    assert mod.is_personal_query("Amir Temur hayoti") is False
    assert mod.is_personal_query("Oʻzbekiston tarixi") is False
    # Just the first name without any triggers (e.g. user entering their name as Asilbek)
    assert mod.is_personal_query("Asilbek") is False


def test_check_content():
    # Bad word -> haqorat
    ok, reason = mod.check_content("sen dalbayobmisan")
    assert ok is False
    assert reason == "haqorat"

    # Personal query -> personal
    ok, reason = mod.check_content("Asilbek Davlatov haqida prezentatsiya")
    assert ok is False
    assert reason == "personal"

    # Clean text -> True
    ok, reason = mod.check_content("Sun'iy intellektning tibbiyotdagi o'rni")
    assert ok is True
    assert reason is None

    # Empty text -> True
    assert mod.check_content("") == (True, None)
    assert mod.check_content(None) == (True, None)


def test_badwords_module_compatibility():
    assert badwords.has_badword("dalbayob") is True
    assert badwords.has_badword("assalomu alaykum") is False
    words = badwords.get_badwords("ahmoq va dalbayob")
    assert len(words) >= 1
