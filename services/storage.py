"""User-owned file paths and bounded retention, shared by all bot workflows."""

import os
import shutil
import time
import uuid
from pathlib import Path

import config


def user_dir(uid: int) -> Path:
    if uid <= 0:
        raise ValueError("Invalid user ID")
    directory = config.FILES_DIR / str(uid)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise ValueError("Invalid storage directory")
    return directory


def new_file(uid: int, prefix: str, suffix: str) -> Path:
    if not prefix.replace("_", "").isalnum() or not suffix.lstrip(".").isalnum():
        raise ValueError("Invalid file name")
    return user_dir(uid) / f"{prefix}_{uuid.uuid4().hex}{suffix}"


def owned_path(uid: int, path: Path) -> bool:
    root = config.FILES_DIR.resolve() / str(uid)
    return not path.is_symlink() and path.resolve().is_relative_to(root)


def persist(uid: int, path: Path) -> Path:
    """Copy temporary/legacy output to durable user storage before indexing it."""
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(path)
    if owned_path(uid, path):
        os.chmod(path, 0o600)
        return path
    target = new_file(uid, "file", path.suffix)
    staging = target.with_suffix(target.suffix + ".part")
    try:
        shutil.copyfile(path, staging)
        os.chmod(staging, 0o600)
        staging.replace(target)
    finally:
        staging.unlink(missing_ok=True)
    return target


def cleanup_expired() -> int:
    """Includes abandoned uploads; never follows a symlink outside FILES_DIR."""
    cutoff = time.time() - config.FILE_RETENTION_DAYS * 86400
    deleted = 0
    for directory, subdirs, files in os.walk(config.FILES_DIR, followlinks=False):
        subdirs[:] = [s for s in subdirs if not (Path(directory) / s).is_symlink()]
        for name in files:
            path = Path(directory) / name
            try:
                if not path.is_symlink() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    deleted += 1
            except FileNotFoundError:
                pass
    return deleted
