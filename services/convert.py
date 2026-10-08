import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from pdf2docx import Converter
from PIL import Image

log = logging.getLogger("talababot")

TMP = Path("/tmp/talaba_bot_convert")
TMP.mkdir(parents=True, exist_ok=True)

_lock = asyncio.Semaphore(4)


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
    """PDF ni Word (.docx) ga tezkor aylantirish.
    Avval tezyurar LibreOffice orqali (5-10 baravar tez),
    agar muammo bo'lsa pdf2docx orqali ko'p yadroli (multiprocessing) konvertatsiya qiladi.
    """
    out_dir = TMP / f"pdf_{uuid.uuid4().hex[:10]}"
    out_dir.mkdir(parents=True)
    out = out_dir / (src.stem + ".docx")

    # 1. Tezkor LibreOffice orqali sinab ko'rish
    try:
        profile = Path(tempfile.mkdtemp(prefix="lo_p2d_"))
        try:
            r = subprocess.run(
                ["soffice", "--headless", "--norestore",
                 f"-env:UserInstallation=file://{profile}",
                 "--infilter=writer_pdf_import",
                 "--convert-to", "docx", str(src), "--outdir", str(out_dir)],
                capture_output=True, text=True, timeout=60,
            )
            if out.exists() and out.stat().st_size > 500:
                return out
        finally:
            _cleanup(profile)
    except Exception as e:
        log.warning("LibreOffice PDF->DOCX xatosi: %s, pdf2docx ga o'tilmoqda", e)

    # 2. Agar LibreOffice ishlamasa yoki bo'sh chiqsa, pdf2docx multi-core
    try:
        cv = Converter(str(src))
        try:
            cores = min(os.cpu_count() or 4, 4)
            cv.convert(str(out), start=0, end=None, multi_processing=True, cpu_count=cores)
        finally:
            cv.close()
    except Exception:
        # Fallback oddiy rejim
        cv = Converter(str(src))
        try:
            cv.convert(str(out), start=0, end=None)
        finally:
            cv.close()

    if not out.exists():
        _cleanup(out_dir)
        raise RuntimeError("PDF oʻqilmadi")
    return out


def images_to_pdf_sync(image_paths: list[Path] | Path, out_path: Path | None = None) -> Path:
    """Rasm(lar)ni PDF faylga aylantirish."""
    if isinstance(image_paths, (str, Path)):
        image_paths = [Path(image_paths)]
    else:
        image_paths = [Path(p) for p in image_paths]
    if not image_paths:
        raise ValueError("Kamida bitta rasm kerak")

    if out_path is None:
        out_dir = TMP / f"img_{uuid.uuid4().hex[:10]}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / (image_paths[0].stem + ".pdf")
    else:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

    imgs = []
    for p in image_paths:
        im = Image.open(p)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGB")
        elif im.mode != "RGB":
            im = im.convert("RGB")
        imgs.append(im)

    first = imgs[0]
    rest = imgs[1:]
    first.save(out_path, "PDF", resolution=100.0, save_all=True, append_images=rest)
    return out_path


async def docx_to_pdf(src: Path) -> Path:
    async with _lock:
        return await asyncio.to_thread(docx_to_pdf_sync, src)


async def pdf_to_docx(src: Path) -> Path:
    async with _lock:
        return await asyncio.to_thread(pdf_to_docx_sync, src)


async def image_to_pdf(image_path: Path) -> Path:
    async with _lock:
        return await asyncio.to_thread(images_to_pdf_sync, image_path)