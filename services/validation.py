from pathlib import Path
from zipfile import BadZipFile, ZipFile

import config


def validate_docx(path: Path):
    if path.stat().st_size > config.MAX_DOWNLOAD:
        raise ValueError("Word fayli juda katta")
    try:
        with ZipFile(path) as archive:
            items = archive.infolist()
            if (
                len(items) > 2000
                or sum(item.file_size for item in items) > 60 * 1024 * 1024
                or "word/document.xml" not in archive.namelist()
            ):
                raise ValueError("Word faylining ichki hajmi yoki shakli noto'g'ri")
    except BadZipFile as exc:
        raise ValueError("Word fayli buzilgan") from exc
