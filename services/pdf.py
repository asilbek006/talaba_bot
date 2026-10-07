from pypdf import PdfReader, PdfWriter

from pathlib import Path


def merge_pdfs(files, out: Path):
    writer = PdfWriter()
    for f in files:
        reader = PdfReader(str(f))
        for page in reader.pages:
            writer.add_page(page)
    with open(out, "wb") as fb:
        writer.write(fb)
    return out


def split_pdf(src: Path, ranges: list[tuple[int, int]]) -> list[Path]:
    reader = PdfReader(str(src))
    total = len(reader.pages)
    outs = []
    for idx, (a, b) in enumerate(ranges, 1):
        writer = PdfWriter()
        for page_no in range(a - 1, min(b, total)):
            writer.add_page(reader.pages[page_no])
        p = src.parent / f"split_{idx}_{a}-{b}.pdf"
        with open(p, "wb") as fb:
            writer.write(fb)
        outs.append(p)
    return outs


def parse_ranges(text: str, total: int) -> list[tuple[int, int]]:
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