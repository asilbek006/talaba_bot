from pathlib import Path

from pypdf import PdfReader, PdfWriter

from services.pdf import merge_pdfs, parse_ranges, split_pdf


def make_pdf(path: Path, pages: int = 3) -> Path:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    with open(path, "wb") as f:
        w.write(f)
    return path


def test_parse_ranges_basic():
    assert parse_ranges("1-3, 5, 8-10", 10) == [(1, 3), (5, 5), (8, 10)]


def test_parse_ranges_en_dash():
    assert parse_ranges("1–3, 5", 10) == [(1, 3), (5, 5)]


def test_parse_ranges_out_of_bounds():
    assert parse_ranges("2-99", 10) == [(2, 10)]


def test_parse_ranges_invalid():
    assert parse_ranges("abc", 10) == []


def test_parse_ranges_zero():
    assert parse_ranges("0-1, 0", 10) == []


def test_parse_ranges_reversed():
    assert parse_ranges("5-3", 10) == [(3, 5)]


def test_merge_pdfs(tmp_path):
    a = make_pdf(tmp_path / "a.pdf", 2)
    b = make_pdf(tmp_path / "b.pdf", 3)
    out = merge_pdfs([a, b], tmp_path / "m.pdf")
    assert len(PdfReader(str(out)).pages) == 5


def test_split_pdf(tmp_path):
    src = make_pdf(tmp_path / "s.pdf", 5)
    outs = split_pdf(src, [(1, 2), (4, 5)])
    assert len(outs) == 2
    assert len(PdfReader(str(outs[0])).pages) == 2
    assert len(PdfReader(str(outs[1])).pages) == 2


def test_doc_pdf_conversion(tmp_path):
    from docx import Document
    from services.doc_pdf import docx_to_pdf, pdf_to_docx

    doc = Document()
    doc.add_paragraph("Test TalabaBot Conversion")
    docx_p = tmp_path / "test.docx"
    doc.save(docx_p)

    pdf_p = tmp_path / "test.pdf"
    res_pdf = docx_to_pdf(docx_p, pdf_p)
    assert res_pdf.exists()
    assert res_pdf.stat().st_size > 0

    back_docx = tmp_path / "back.docx"
    res_docx = pdf_to_docx(res_pdf, back_docx)
    assert res_docx.exists()
    assert res_docx.stat().st_size > 0


def test_image_to_pdf_conversion(tmp_path):
    from PIL import Image
    from services.doc_pdf import image_to_pdf

    img = Image.new("RGB", (200, 200), color="blue")
    img_p = tmp_path / "sample.jpg"
    img.save(img_p)

    out_pdf = tmp_path / "sample.pdf"
    res_pdf = image_to_pdf(img_p, out_pdf)
    assert res_pdf.exists()
    assert res_pdf.stat().st_size > 0
    assert len(PdfReader(str(res_pdf)).pages) == 1