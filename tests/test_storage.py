import os
from concurrent.futures import ThreadPoolExecutor

import config
from services import storage


def test_paths_are_unique_and_user_owned(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FILES_DIR", tmp_path)
    with ThreadPoolExecutor(max_workers=8) as executor:
        paths = list(
            executor.map(
                lambda i: storage.new_file(111 if i % 2 else 222, "tr", ".docx"), range(1000)
            )
        )
    assert len(set(paths)) == 1000
    for path in paths:
        assert storage.owned_path(int(path.parent.name), path)
        assert not storage.owned_path(333, path)


def test_temporary_file_is_copied_to_durable_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FILES_DIR", tmp_path / "files")
    source = tmp_path / "temporary.docx"
    source.write_bytes(b"user document")
    path = storage.persist(111, source)
    source.unlink()
    assert path.read_bytes() == b"user document"
    assert storage.owned_path(111, path)
    assert storage.persist(111, path) == path


def test_retention_removes_inactive_uploads_without_following_symlinks(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FILES_DIR", tmp_path / "files")
    old = storage.new_file(111, "upload", ".docx")
    old.write_bytes(b"expired")
    os.utime(old, (1, 1))
    fresh = storage.new_file(111, "upload", ".docx")
    fresh.write_bytes(b"fresh")
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "secret"
    protected.write_bytes(b"do not remove")
    os.utime(protected, (1, 1))
    (config.FILES_DIR / "linked").symlink_to(outside, target_is_directory=True)
    assert storage.cleanup_expired() == 1
    assert fresh.exists() and protected.exists() and not old.exists()


def test_split_results_do_not_overwrite_previous_files(tmp_path):
    from pypdf import PdfWriter

    from services.pdf import split_pdf

    source = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(source)
    first = split_pdf(source, [(1, 1)])[0]
    second = split_pdf(source, [(1, 1)])[0]
    assert first != second and first.exists() and second.exists()
