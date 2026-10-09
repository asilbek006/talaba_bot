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
    data = {
        "title": "Orol dengizi",
        "sections": [{"heading": "Kirish", "paragraphs": ["Orol dengizi ekologiyasi ..."]}],
    }
    assert gen._topic_ok("Orol dengizi ekologik muammosi", data) is True


def test_topic_ok_missing():
    data = {
        "title": "Kompyuter",
        "sections": [{"heading": "Kirish", "paragraphs": ["Kompyuter tarixi ..."]}],
    }
    assert gen._topic_ok("Orol dengizi", data) is False


def test_topic_ok_short_words():
    assert gen._topic_ok("AI", {"title": "X", "sections": []}) is True


def test_ppt_from_text_trim_slides(monkeypatch):
    fake = {
        "title": "T",
        "slides": [
            {"title": "s1", "bullets": ["a"], "notes": ""},
            {"title": "s2", "bullets": ["a"], "notes": ""},
            {"title": "s3", "bullets": ["a"], "notes": ""},
            {"title": "s4", "bullets": ["a"], "notes": ""},
            {"title": "s5", "bullets": ["a"], "notes": ""},
        ],
    }
    monkeypatch.setattr(gen, "_ask", lambda prompt: fake)
    out = gen.gen_ppt_from_text("uz", "matn", 3)
    assert len(out["slides"]) == 3


def test_ppt_from_text_prompt_defined():
    assert "title" in gen.PPTX_FROM_TEXT_PROMPT
    assert "{n}" in gen.PPTX_FROM_TEXT_PROMPT
    assert "{text}" in gen.PPTX_FROM_TEXT_PROMPT


def test_xlsx_exact_rows_when_overproduced(monkeypatch):
    """Foydalanuvchi 200 so'rasa, Gemini 255 yuborsa — aynan 200 gacha kesiladi."""
    fake = {"title": "T", "headers": ["a", "b"], "rows": [[f"r{i}", i] for i in range(255)]}
    monkeypatch.setattr(gen, "_ask_validated", lambda prompt, ok: fake)
    out = gen.gen_xlsx("uz", "Mavzu", 200)
    assert len(out["rows"]) == 200


def test_xlsx_retry_on_mismatch(monkeypatch):
    calls = []

    def fake(prompt, ok):
        calls.append(prompt)
        n = len(calls)
        rows = [[f"r{i}", i] for i in range(10)] if n == 1 else [[f"r{i}", i] for i in range(5)]
        return {"title": "T", "headers": ["a", "b"], "rows": rows}

    monkeypatch.setattr(gen, "_ask_validated", fake)
    out = gen.gen_xlsx("uz", "Mavzu", 5)
    assert len(calls) == 2
    assert len(out["rows"]) == 5


def test_referat_retries_when_short(monkeypatch):
    words = ["kalima"] * 400
    calls = []

    def fake_ask(prompt, ok):
        calls.append(prompt)
        return {
            "title": "T",
            "sections": [
                {"heading": "Kirish", "paragraphs": words},
                {"heading": "Xulosa", "paragraphs": words},
            ],
        }

    monkeypatch.setattr(gen, "_ask_validated", fake_ask)
    # 30 sahifa -> kamida 0.8 * 29*300 ~ 6960 so'z kerak, bu yerda 800 ta — qoltadan qayta so'raladi
    with pytest.raises(RuntimeError, match="hajmi"):
        gen.gen_referat("uz", "Mavzu", 30)
    assert len(calls) == 2


def test_word_count_helper():
    data = {"sections": [{"paragraphs": ["bir ikki uch", "to'rt besh"]}, {"paragraphs": ["olti"]}]}
    assert gen._word_count(data) == 6


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


def test_pptx_prompts_have_diversity_rules():
    assert "XILMA-XILLIK" in gen.PPTX_PROMPT
    assert "XILMA-XILLIK" in gen.PPTX_FROM_TEXT_PROMPT


def test_pptx_prompts_ask_for_image_hint():
    for p in (gen.PPTX_PROMPT, gen.PPTX_FROM_TEXT_PROMPT):
        assert "image_hint" in p
        assert "inglizcha" in p


@pytest.mark.parametrize(
    "function,args",
    [
        (gen.gen_translate, ("en", ["one", "two", "three"])),
        (gen.gen_rewrite, (["one", "two", "three"],)),
    ],
)
def test_incomplete_transform_fails_instead_of_silently_dropping_text(monkeypatch, function, args):
    monkeypatch.setattr(gen, "_ask", lambda prompt: {"paragraphs": ["only one"]})
    with pytest.raises(RuntimeError):
        function(*args)


def test_transform_preserves_every_paragraph_across_batches(monkeypatch):
    import re

    sizes = []

    def fake(prompt):
        sizes.append(len(prompt))
        parts = re.findall(r"^\d+\. (.*)$", prompt, re.M)
        return {"paragraphs": [part.upper() for part in parts]}

    monkeypatch.setattr(gen, "_ask", fake)
    paragraphs = ["a" * 5000, "b" * 5000, "c" * 9000]
    out = gen.gen_rewrite(paragraphs)
    assert len(out) == 3
    assert [p.replace(" ", "") for p in out] == [p.upper() for p in paragraphs]
    assert len(sizes) >= 3 and max(sizes) < 9000


def test_too_few_slides_are_rejected(monkeypatch):
    monkeypatch.setattr(
        gen,
        "_ask",
        lambda prompt: {"title": "Topic", "slides": [{"title": "Topic", "bullets": ["content"]}]},
    )
    with pytest.raises(RuntimeError):
        gen.gen_pptx("en", "Topic", 10)
    with pytest.raises(RuntimeError):
        gen.gen_ppt_from_text("en", "source", 10)


def test_invalid_answer_index_is_rejected(monkeypatch):
    monkeypatch.setattr(
        gen, "_ask", lambda prompt: [{"q": "Q", "options": ["a", "b", "c", "d"], "answer": 9}] * 5
    )
    with pytest.raises(RuntimeError):
        gen.gen_test("en", "Topic", 5)


def test_wrong_row_shape_is_rejected(monkeypatch):
    monkeypatch.setattr(gen, "_ask", lambda prompt: {"headers": ["A", "B"], "rows": [["one"]] * 5})
    with pytest.raises(RuntimeError):
        gen.gen_xlsx("en", "Topic", 5)


def test_large_source_is_not_silently_truncated():
    with pytest.raises(ValueError):
        gen.gen_ppt_from_text("en", "x" * 40001, 5)


def test_permanent_api_error_is_not_retried(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    class BadKey(Exception):
        code = 403

    generate = Mock(side_effect=BadKey("private credential detail"))
    monkeypatch.setattr(
        gen, "client", SimpleNamespace(models=SimpleNamespace(generate_content=generate))
    )
    with pytest.raises(RuntimeError) as err:
        gen._ask("hello")
    assert generate.call_count == 1
    assert "private credential" not in str(err.value)
