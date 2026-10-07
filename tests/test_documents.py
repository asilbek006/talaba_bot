from docx import Document
from pptx import Presentation
from openpyxl import load_workbook

from services import documents as docs


def test_build_referat(tmp_path):
    data = {
        "title": "Elektron tijorat",
        "sections": [
            {"heading": "Kirish",
             "paragraphs": ["Birinchi paragraf.", "Ikkinchi paragraf."]},
            {"heading": "Asosiy qism",
             "paragraphs": ["Uchinchi paragraf."]},
        ],
    }
    out = docs.build_referat(data, "uz", tmp_path / "ref.docx")
    assert out.exists()
    doc = Document(out)
    texts = [p.text for p in doc.paragraphs]
    assert any("Elektron tijorat" in x for x in texts)
    assert any("Birinchi paragraf" in x for x in texts)
    assert any("Kirish" in x for x in texts)


def test_build_referat_ru(tmp_path):
    data = {"title": "Тема", "sections": [
        {"heading": "Введение", "paragraphs": ["Текст."]}]}
    out = docs.build_referat(data, "ru", tmp_path / "ref_ru.docx")
    assert out.exists()
    texts = [p.text for p in Document(out).paragraphs]
    assert any("РЕФЕРАТ" in x for x in texts)


def test_build_pptx_slide_count(tmp_path):
    data = {
        "title": "AI",
        "subtitle": "Ta'limda",
        "slides": [
            {"title": "Kirish", "bullets": ["b1", "b2", "b3"], "notes": "n1"},
            {"title": "Xulosa", "bullets": ["b4", "b5"], "notes": "n2"},
        ],
    }
    out = docs.build_pptx(data, "uz", tmp_path / "p.pptx")
    assert out.exists()
    prs = Presentation(out)
    assert len(prs.slides) == 2


def test_build_pptx_notes(tmp_path):
    data = {"title": "T", "slides": [
        {"title": "S", "bullets": ["b"], "notes": "hop notes"}]}
    out = docs.build_pptx(data, "uz", tmp_path / "p2.pptx")
    assert out.exists()


def test_build_pptx_all_bullets_render(tmp_path):
    data = {"title": "T", "subtitle": "s", "slides": [
        {"title": "Muqova", "bullets": [], "notes": ""},
        {"title": "S2", "bullets": ["birinchi", "ikkinchi", "uchinchi"], "notes": "n"},
    ]}
    out = docs.build_pptx(data, "uz", tmp_path / "p3.pptx")
    prs = Presentation(out)
    slide = prs.slides[1]
    joined = "\n".join(
        s.text_frame.text for s in slide.shapes if s.has_text_frame)
    for w in ("birinchi", "ikkinchi", "uchinchi"):
        assert w in joined, f"bullet '{w}' render bo'lmadi"


def test_build_xlsx_rows_and_headers(tmp_path):
    data = {
        "title": "Aholi",
        "headers": ["Viloyat", "Aholi"],
        "rows": [["Toshkent", 2900000], ["Samarqand", 3900000]],
        "note": "taxminiy",
    }
    out = docs.build_xlsx(data, "uz", tmp_path / "t.xlsx")
    assert out.exists()
    wb = load_workbook(out)
    ws = wb.active
    assert ws.cell(row=2, column=1).value == "Viloyat"
    assert ws.cell(row=3, column=1).value == "Toshkent"


def test_build_xlsx_empty_rows(tmp_path):
    data = {"title": "Bo'sh", "headers": ["№", "Ma'lumot"], "rows": [], "note": ""}
    out = docs.build_xlsx(data, "uz", tmp_path / "t2.xlsx")
    assert out.exists()


def test_build_test_docx(tmp_path):
    qs = [
        {"q": "Savol 1?", "options": ["A v", "B v", "C v", "D v"], "answer": 1},
        {"q": "Savol 2?", "options": ["A v", "B v", "C v", "D v"], "answer": 3},
    ]
    out = docs.build_test_docx(qs, "uz", tmp_path / "test.docx")
    assert out.exists()
    texts = [p.text for p in Document(out).paragraphs]
    assert any("TEST SAVOLLARI" in x for x in texts)
    assert any("Toʻgʻri javob: B" in x for x in texts)
    assert any("Toʻgʻri javob: D" in x for x in texts)


def test_build_test_docx_bad_answer(tmp_path):
    qs = [{"q": "Q?", "options": ["A", "B", "C", "D"], "answer": 99}]
    out = docs.build_test_docx(qs, "uz", tmp_path / "t.docx")
    texts = [p.text for p in Document(out).paragraphs]
    assert any("Toʻgʻri javob: ?" in x for x in texts)


def test_build_text_docx(tmp_path):
    paras = ["Oddiy paragraf.", "## Sarlavha", "Yana matn"]
    out = docs.build_text_docx(paras, "Sarlavha", tmp_path / "txt.docx")
    assert out.exists()