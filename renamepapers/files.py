"""Filesystem moves, duplicate detection, and collision handling."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


def move_or_deduplicate(pdf: Path, destination: Path, *, dry_run: bool) -> str:
    """Move *pdf* unless destination already has identical content."""
    if destination.exists() and not same_path(pdf, destination):
        if same_file_content(pdf, destination):
            if dry_run:
                return f"DUP {pdf.name} == {destination}"
            pdf.unlink()
            return f"DUP {pdf.name} == {destination} (removed duplicate)"
        destination = unique_path(destination)

    if dry_run:
        return f"DRY {pdf.name} -> {destination}"

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pdf), str(destination))
    return f"OK  {pdf.name} -> {destination}"


def same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def same_file_content(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except OSError:
        return False
    return file_digest(a) == file_digest(b)


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    counter = 2
    while True:
        candidate = path.with_name(f"{stem}-{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1
