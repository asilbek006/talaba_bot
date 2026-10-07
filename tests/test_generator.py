import pytest

import services.generator as gen


def test_extract_json_fenced():
    text = '```json\n{"a": 1}\n```'
    assert gen._extract_json(text) == {"a": 1}


def test_extract_json_raw():
    assert gen._extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_embedded():
    text = 'Boshlang\'ich matn\n{"a": 1, "b": [1, 2]}\n\nKelayotgan matn'
    assert gen._extract_json(text) == {"a": 1, "b": [1, 2]}


def test_topic_ok_valid():
    data = {"title": "Orol dengizi", "sections": [
        {"heading": "Kirish", "paragraphs": ["Orol dengizi ekologiyasi ..."]}]}
    assert gen._topic_ok("Orol dengizi ekologik muammosi", data) is True


def test_topic_ok_missing():
    data = {"title": "Kompyuter", "sections": [
        {"heading": "Kirish", "paragraphs": ["Kompyuter tarixi ..."]}]}
    assert gen._topic_ok("Orol dengizi", data) is False


def test_topic_ok_short_words():
    assert gen._topic_ok("AI", {"title": "X", "sections": []}) is True


def test_ppt_from_text_trim_slides(monkeypatch):
    fake = {"title": "T", "slides": [
        {"title": "s1", "bullets": ["a"], "notes": ""},
        {"title": "s2", "bullets": ["a"], "notes": ""},
        {"title": "s3", "bullets": ["a"], "notes": ""},
        {"title": "s4", "bullets": ["a"], "notes": ""},
        {"title": "s5", "bullets": ["a"], "notes": ""},
    ]}
    monkeypatch.setattr(gen, "_ask", lambda prompt: fake)
    out = gen.gen_ppt_from_text("uz", "matn", 3)
    assert len(out["slides"]) == 3


def test_ppt_from_text_prompt_defined():
    assert "title" in gen.PPTX_FROM_TEXT_PROMPT
    assert "{n}" in gen.PPTX_FROM_TEXT_PROMPT
    assert "{text}" in gen.PPTX_FROM_TEXT_PROMPT


def test_ask_validated_valid(monkeypatch):
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return {"slides": [{"title": "s"}]}
    monkeypatch.setattr(gen, "_ask", fake)
    out = gen._ask_validated("p", gen._ppt_ok)
    assert out["slides"]
    assert len(calls) == 1


def test_ask_validated_invalid_then_valid(monkeypatch):
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return {"bad": True} if len(calls) == 1 else {"slides": [{"title": "s"}]}
    monkeypatch.setattr(gen, "_ask", fake)
    out = gen._ask_validated("p", gen._ppt_ok)
    assert len(calls) == 2
    assert out["slides"]


def test_ask_validated_gives_up(monkeypatch):
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return {"bad": True}
    monkeypatch.setattr(gen, "_ask", fake)
    with pytest.raises(RuntimeError):
        gen._ask_validated("p", gen._ppt_ok)
    assert len(calls) == 2