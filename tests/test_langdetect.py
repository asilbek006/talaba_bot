import pytest

from services import generator
from services.langdetect import detect_lang


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Axborot xavfsizligi haqida", "uz"),
        ("Безопасность информации", "ru"),
        ("Computer network security", "en"),
    ],
)
def test_upstream_language_detection(text, language):
    assert detect_lang(text) == language


def test_generation_keeps_topic_language_detection(monkeypatch):
    calls = []

    def answer(prompt, valid):
        calls.append(prompt)
        return {"headers": ["A", "B"], "rows": [[1, 2]]}

    monkeypatch.setattr(generator, "_ask_validated", answer)
    generator.gen_xlsx("uz", "Безопасность информации", 1)
    assert "русский" in calls[0]
