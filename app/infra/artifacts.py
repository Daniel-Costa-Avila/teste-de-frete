from __future__ import annotations

import time
from pathlib import Path


_ARTIFACT_CLEANUP_EXTENSIONS = {".html", ".png"}


def cleanup_expired_artifacts(artifacts_dir: str, retention_days: int) -> list[str]:
    if retention_days <= 0:
        return []

    root = Path(artifacts_dir)
    if not root.exists():
        return []

    cutoff = time.time() - (retention_days * 24 * 60 * 60)
    deleted: list[str] = []

    for path in root.rglob("*"):
        # Cleanup is best-effort: damaged entries must not prevent startup.
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        if path.suffix.lower() not in _ARTIFACT_CLEANUP_EXTENSIONS:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > cutoff:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            continue
        deleted.append(str(path))

    directories: list[Path] = []
    for path in root.rglob("*"):
        try:
            if path.is_dir():
                directories.append(path)
        except OSError:
            continue

    for directory in sorted(directories, reverse=True):
        try:
            next(directory.iterdir())
        except StopIteration:
            try:
                directory.rmdir()
            except OSError:
                pass
        except OSError:
            pass

    return deleted
