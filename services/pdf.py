import uuid
from pathlib import Path

from pypdf import PdfReader, PdfWriter

import config


def page_count(src: Path) -> int:
    total = len(PdfReader(str(src)).pages)
    if not 1 <= total <= config.MAX_PDF_PAGES:
        raise ValueError(f"PDF 1–{config.MAX_PDF_PAGES} sahifa bo'lishi kerak")
    return total


def merge_pdfs(files, out: Path):
    if not 2 <= len(files) <= 10:
        raise ValueError("2–10 ta PDF kerak")
    writer = PdfWriter()
    total = 0
    for f in files:
        reader = PdfReader(str(f))
        total += len(reader.pages)
        if total > config.MAX_PDF_PAGES:
            raise ValueError("PDF sahifalari soni juda ko'p")
        for page in reader.pages:
            writer.add_page(page)
    with open(out, "wb") as fb:
        writer.write(fb)
    return out


def split_pdf(src: Path, ranges: list[tuple[int, int]]) -> list[Path]:
    page_count(src)
    if not 1 <= len(ranges) <= 20:
        raise ValueError("1–20 ta diapazon kiriting")
    reader = PdfReader(str(src))
    total = len(reader.pages)
    outs = []
    for idx, (a, b) in enumerate(ranges, 1):
        writer = PdfWriter()
        for page_no in range(a - 1, min(b, total)):
            writer.add_page(reader.pages[page_no])
        p = src.parent / f"split_{uuid.uuid4().hex}_{idx}_{a}-{b}.pdf"
        with open(p, "wb") as fb:
            writer.write(fb)
        outs.append(p)
    return outs


def parse_ranges(text: str, total: int) -> list[tuple[int, int]]:
    if total < 1 or len(text.split(",")) > 20:
        return []
    out = []
    parts = text.replace("–", "-").split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a_raw, b_raw = part.split("-", 1)
                a, b = int(a_raw), int(b_raw)
            else:
                a = b = int(part)
        except (ValueError, TypeError):
            continue
        if a < 1 or b < 1:
            continue
        a, b = min(a, total), min(b, total)
        lo, hi = min(a, b), max(a, b)
        out.append((lo, hi))
    return out
