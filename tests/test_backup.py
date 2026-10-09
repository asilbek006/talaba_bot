import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from deploy import backup


def configure(tmp_path, monkeypatch):
    files = tmp_path / "files"
    files.mkdir()
    (files / "user.docx").write_bytes(b"kept")
    (files / ".tmp").mkdir()
    (files / ".tmp" / "partial.docx").write_bytes(b"partial")
    output = tmp_path / "backups"
    monkeypatch.setattr(sys, "argv", ["backup.py", "--files", str(files), "--output", str(output)])
    monkeypatch.setenv("DATABASE_URL", "postgresql://owner:s%40cret@localhost/test?sslmode=require")
    return output


def test_backup_omits_temp_and_keeps_password_out_of_command(tmp_path, monkeypatch):
    output = configure(tmp_path, monkeypatch)
    calls = []

    def dump(argv, **kwargs):
        calls.append((argv, kwargs))
        Path(argv[-1]).write_bytes(b"database dump")

    monkeypatch.setattr(backup.subprocess, "run", dump)
    backup.main()
    (destination,) = output.iterdir()
    with tarfile.open(destination / "files.tar.gz") as archive:
        assert "files/user.docx" in archive.getnames()
        assert not any("/.tmp" in name for name in archive.getnames())
    assert calls[0][1]["env"]["PGPASSWORD"] == "s@cret"
    assert calls[0][1]["env"]["PGSSLMODE"] == "require"
    assert not any("s@cret" in arg or "s%40cret" in arg for arg in calls[0][0])
    assert os.stat(destination / "database.dump").st_mode & 0o777 == 0o600


def test_failed_backup_removes_only_its_partial_directory(tmp_path, monkeypatch):
    output = configure(tmp_path, monkeypatch)
    previous = output / "older"
    previous.mkdir(parents=True)

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "pg_dump")

    monkeypatch.setattr(backup.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        backup.main()
    assert list(output.iterdir()) == [previous]
