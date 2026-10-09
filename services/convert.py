import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter

import config
from services.runtime import run_blocking

log = logging.getLogger("talababot")
TMP = config.FILES_DIR / ".tmp"
TMP.mkdir(parents=True, exist_ok=True, mode=0o700)
_lock = asyncio.Semaphore(config.CONVERT_MAX_CONCURRENT)


def _cleanup(directory: Path):
    shutil.rmtree(directory, ignore_errors=True)


def cleanup_result(path: Path):
    path = Path(path)
    if path.parent.parent.resolve() == TMP.resolve() and not path.parent.is_symlink():
        _cleanup(path.parent)


def cleanup_temp():
    cutoff = time.time() - 86400
    for path in TMP.iterdir():
        if path.is_dir() and not path.is_symlink() and path.stat().st_mtime < cutoff:
            _cleanup(path)


def _run_soffice(src: Path, out_dir: Path) -> Path:
    with tempfile.TemporaryDirectory(prefix="lo_profile_") as profile:
        result = subprocess.run(
            [
                "soffice",
                "--headless",
                "--norestore",
                f"-env:UserInstallation={Path(profile).as_uri()}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(src.resolve()),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    pdf = out_dir / (src.stem + ".pdf")
    if result.returncode or not pdf.is_file() or not pdf.stat().st_size:
        raise RuntimeError("LibreOffice konvertatsiyasi bajarilmadi")
    return pdf


def docx_to_pdf_sync(src: Path) -> Path:
    if src.suffix.lower() == ".docx":
        from services.validation import validate_docx

        validate_docx(src)
    out_dir = TMP / f"docx_{uuid.uuid4().hex}"
    out_dir.mkdir(mode=0o700)
    try:
        return _run_soffice(src, out_dir)
    except BaseException:
        _cleanup(out_dir)
        raise


def pdf_to_docx_sync(src: Path) -> Path:
    from services.pdf import page_count

    page_count(src)
    out_dir = TMP / f"pdf_{uuid.uuid4().hex}"
    out_dir.mkdir(mode=0o700)
    out = out_dir / (src.stem + ".docx")
    try:
        with tempfile.TemporaryDirectory(prefix="lo_p2d_") as profile:
            try:
                result = subprocess.run(
                    [
                        "soffice",
                        "--headless",
                        "--norestore",
                        f"-env:UserInstallation={Path(profile).as_uri()}",
                        "--infilter=writer_pdf_import",
                        "--convert-to",
                        "docx",
                        str(src.resolve()),
                        "--outdir",
                        str(out_dir),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode == 0 and out.exists() and out.stat().st_size > 500:
                    return out
            except (OSError, subprocess.TimeoutExpired):
                log.info("LibreOffice PDF import unavailable; using isolated converter")
        # One bounded subprocess per conversion, instead of 4 children per request.
        out.unlink(missing_ok=True)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "services.convert_worker",
                str(src.resolve()),
                str(out.resolve()),
            ],
            cwd=config.BASE_DIR,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode or not out.exists() or out.stat().st_size < 500:
            raise RuntimeError("PDF ni Word formatiga aylantirib bo'lmadi")
        return out
    except BaseException:
        _cleanup(out_dir)
        raise


def images_to_pdf_sync(image_paths: list[Path] | Path, out_path: Path | None = None) -> Path:
    paths = (
        [Path(image_paths)]
        if isinstance(image_paths, (str, Path))
        else [Path(p) for p in image_paths]
    )
    if not 1 <= len(paths) <= 10:
        raise ValueError("1–10 ta rasm yuboring")
    temporary = out_path is None
    if temporary:
        directory = TMP / f"img_{uuid.uuid4().hex}"
        directory.mkdir(mode=0o700)
        out_path = directory / (paths[0].stem + ".pdf")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        writer = PdfWriter()
        for path in paths:
            if path.stat().st_size > config.MAX_DOWNLOAD:
                raise ValueError("Rasm hajmi juda katta")
            with Image.open(path) as original:
                if original.width * original.height > 12_000_000 or max(original.size) > 8000:
                    raise ValueError("Rasm o'lchami juda katta")
                image = ImageOps.exif_transpose(original)
                rgba = image.convert("RGBA")
                rgb = Image.new("RGB", rgba.size, "white")
                try:
                    rgb.paste(rgba, mask=rgba.getchannel("A"))
                    rgb.thumbnail((2400, 3200), Image.Resampling.LANCZOS)
                    buffer = BytesIO()
                    rgb.save(buffer, "PDF", resolution=150.0)
                    writer.append(PdfReader(buffer))
                finally:
                    rgb.close()
                    rgba.close()
                    if image is not original:
                        image.close()
        writer.write(str(out_path))
        writer.close()
        return out_path
    except BaseException:
        out_path.unlink(missing_ok=True)
        if temporary:
            _cleanup(out_path.parent)
        raise


async def docx_to_pdf(src: Path) -> Path:
    return await run_blocking(_lock, docx_to_pdf_sync, src)


async def pdf_to_docx(src: Path) -> Path:
    return await run_blocking(_lock, pdf_to_docx_sync, src)


async def images_to_pdf(paths: list[Path], out_path: Path | None = None) -> Path:
    return await run_blocking(_lock, images_to_pdf_sync, paths, out_path)


async def image_to_pdf(image_path: Path) -> Path:
    return await images_to_pdf([image_path])
