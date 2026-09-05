"""Explicit legacy import: snapshot SQLite including WAL, never alter the source."""

import os
import shutil
import sqlite3
import tempfile
from contextlib import ExitStack, closing
from pathlib import Path

from core.paths import data_dir, upload_dir

TABLES = {
    ".runs.db": {"runs", "run_results", "companies"},
    ".cache.db": {"http_cache", "search_cache", "mx_cache"},
}


def _read_database(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def _check_database(path: Path, tables: set[str], empty: bool = False) -> None:
    with closing(_read_database(path)) as connection:
        actual = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables <= actual:
            raise ValueError(f"{path.name} is not a compatible Contact Finder database.")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError(f"{path.name} failed its SQLite integrity check.")
        if empty and any(connection.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() for table in tables):
            raise ValueError("The destination already contains data. Import only into an empty profile.")


def import_legacy(source_dir: str) -> list[str]:
    """Called with the app's Store closed and all requests/scans paused.

    Stages all data first. Renames on the destination filesystem preserve the
    original empty databases until the entire import commits; failures roll back.
    A terminated process may leave .finder-import-* recovery directories, never
    delete these automatically at startup.
    """
    source = Path(source_dir).expanduser()
    if not source.is_absolute() or not source.is_dir():
        raise ValueError("Choose the absolute path to the old source folder, with the old app stopped.")
    source = source.resolve()
    destination = Path(data_dir())
    uploads = Path(upload_dir())
    if source == destination or source / ".uploads" == uploads:
        raise ValueError("Source and destination must be different folders.")
    if any(uploads.iterdir()):
        raise ValueError("The destination already contains uploads. Import before uploading a new file.")
    for name, tables in TABLES.items():
        target = destination / name
        if target.exists():
            _check_database(target, tables, empty=True)
        if any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm")):
            raise ValueError("Close every application using the destination databases before importing.")

    originals: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    imported: list[str] = []
    with ExitStack() as stack:
        staging = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix=".finder-import-", dir=destination)))
        staged_uploads = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix=".finder-import-", dir=uploads.parent)))
        replacements: list[tuple[Path, Path]] = []
        for name, tables in TABLES.items():
            old = source / name
            if not old.exists():
                continue
            if old.is_symlink() or not old.is_file():
                raise ValueError("Legacy databases must be regular files, not links.")
            _check_database(old, tables)
            staged = staging / name
            with closing(_read_database(old)) as reader, closing(sqlite3.connect(staged)) as writer:
                reader.backup(writer)
                writer.execute("PRAGMA journal_mode=DELETE")
            _check_database(staged, tables)
            replacements.append((staged, destination / name))
            imported.append(name)

        old_uploads = source / ".uploads"
        if old_uploads.exists():
            if old_uploads.is_symlink() or not old_uploads.is_dir():
                raise ValueError("Legacy uploads must be a regular folder.")
            for old in old_uploads.iterdir():
                if old.is_symlink() or not old.is_file():
                    raise ValueError("Legacy uploads must contain regular files only.")
                shutil.copy2(old, staged_uploads / old.name)
            if any(staged_uploads.iterdir()):
                replacements.append((staged_uploads, uploads))
                imported.append(".uploads")
        if not imported:
            raise ValueError("No legacy .runs.db, .cache.db or .uploads data was found in that folder.")

        try:
            for staged, target in replacements:
                if target.exists():
                    backup = (staging if target.parent == destination else staged_uploads.parent) / (
                        staging.name + "-previous-" + target.name
                    )
                    os.replace(target, backup)
                    originals.append((backup, target))
                os.replace(staged, target)
                installed.append(target)
        except BaseException:
            for target in reversed(installed):
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink(missing_ok=True)
            for backup, target in reversed(originals):
                os.replace(backup, target)
            raise
        else:
            for backup, _ in originals:
                if backup.is_dir():
                    shutil.rmtree(backup)
                else:
                    backup.unlink()
    return imported
