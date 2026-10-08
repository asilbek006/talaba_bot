from __future__ import annotations

import shutil
from pathlib import Path

import services.convert as conv


def docx_to_pdf(docx_path: Path | str, pdf_path: Path | str) -> Path:
    """Word (.docx yoki .doc) faylni PDF formatiga o'tkazish."""
    docx_path = Path(docx_path)
    pdf_path = Path(pdf_path)
    if not docx_path.exists():
        raise FileNotFoundError(docx_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    generated = conv.docx_to_pdf_sync(docx_path)
    if pdf_path.resolve() != generated.resolve():
        if pdf_path.exists():
            try:
                pdf_path.unlink()
            except Exception:
                pass
        shutil.move(str(generated), str(pdf_path))
    return pdf_path


def pdf_to_docx(pdf_path: Path | str, docx_path: Path | str) -> Path:
    """PDF faylni Word (.docx) formatiga o'tkazish."""
    pdf_path = Path(pdf_path)
    docx_path = Path(docx_path)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)
    docx_path.parent.mkdir(parents=True, exist_ok=True)
    generated = conv.pdf_to_docx_sync(pdf_path)
    if docx_path.resolve() != generated.resolve():
        if docx_path.exists():
            try:
                docx_path.unlink()
            except Exception:
                pass
        shutil.move(str(generated), str(docx_path))
    return docx_path
