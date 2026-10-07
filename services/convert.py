import asyncio
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from pdf2docx import Converter

TMP = Path("/tmp/talaba_bot_convert")
TMP.mkdir(parents=True, exist_ok=True)

_lock = asyncio.Semaphore(2)


def _cleanup(d: Path):
    shutil.rmtree(d, ignore_errors=True)


def _run_soffice(src: Path, out_dir: Path) -> Path:
    profile = Path(tempfile.mkdtemp(prefix="lo_profile_"))
    try:
        r = subprocess.run(
            ["soffice", "--headless", "--norestore",
             f"-env:UserInstallation=file://{profile}",
             "--convert-to", "pdf", "--outdir", str(out_dir), str(src)],
            capture_output=True, text=True, timeout=180,
        )
        pdf = out_dir / (src.stem + ".pdf")
        if not pdf.exists():
            raise RuntimeError((r.stderr or r.stdout or "libreoffice xatosi")[-300:])
    finally:
        _cleanup(profile)
    return pdf


def docx_to_pdf_sync(src: Path) -> Path:
    out_dir = TMP / f"docx_{uuid.uuid4().hex[:10]}"
    out_dir.mkdir(parents=True)
    try:
        return _run_soffice(src, out_dir)
    except Exception:
        _cleanup(out_dir)
        raise


def pdf_to_docx_sync(src: Path) -> Path:
    out_dir = TMP / f"pdf_{uuid.uuid4().hex[:10]}"
    out_dir.mkdir(parents=True)
    out = out_dir / (src.stem + ".docx")
    cv = Converter(str(src))
    try:
        cv.convert(str(out), start=0, end=None)
    finally:
        cv.close()
    if not out.exists():
        _cleanup(out_dir)
        raise RuntimeError("PDF oʻqilmadi")
    return out


async def docx_to_pdf(src: Path) -> Path:
    async with _lock:
        return await asyncio.to_thread(docx_to_pdf_sync, src)


async def pdf_to_docx(src: Path) -> Path:
    async with _lock:
        return await asyncio.to_thread(pdf_to_docx_sync, src)