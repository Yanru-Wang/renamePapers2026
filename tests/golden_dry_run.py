#!/usr/bin/env python3
"""Check real-PDF dry-run outputs against expected basenames."""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "tests" / "golden_renames.tsv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="TSV with path, expected_basename, and optional note columns.",
    )
    parser.add_argument(
        "--command",
        default="renamepapers",
        help="renamepapers command to test, e.g. /Users/wyr/.local/bin/renamepapers.",
    )
    parser.add_argument(
        "--strict-missing",
        action="store_true",
        help="Fail when a manifest PDF path is missing instead of skipping it.",
    )
    return parser.parse_args()


def manifest_rows(manifest: Path) -> list[dict[str, str]]:
    with manifest.open(newline="", encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(
                (line for line in handle if not line.startswith("#")),
                delimiter="\t",
            )
            if row.get("path") and row.get("expected_basename")
        ]
    return rows


def dry_run_basename(command: str, pdf: Path) -> str:
    completed = subprocess.run(
        [command, "--dry-run", "--in-place", str(pdf)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise RuntimeError(stderr or completed.stdout.strip() or "dry-run failed")

    for line in completed.stdout.splitlines():
        if " -> " in line:
            return Path(line.rsplit(" -> ", 1)[1]).name
    raise RuntimeError(f"could not parse dry-run output: {completed.stdout!r}")


def main() -> int:
    args = parse_args()
    rows = manifest_rows(args.manifest)
    failures = 0
    skipped = 0

    for row in rows:
        pdf = Path(row["path"]).expanduser()
        expected = row["expected_basename"]
        if not pdf.exists():
            message = f"SKIP missing {pdf}"
            if args.strict_missing:
                message = f"FAIL missing {pdf}"
                failures += 1
            else:
                skipped += 1
            print(message)
            continue

        try:
            actual = dry_run_basename(args.command, pdf)
        except Exception as exc:  # noqa: BLE001 - report every manifest row.
            failures += 1
            print(f"FAIL {pdf}: {exc}")
            continue

        if actual != expected:
            failures += 1
            print(f"FAIL {pdf}: expected {expected}, got {actual}")
        else:
            print(f"OK   {pdf.name}")

    if failures:
        return 1
    print(f"golden dry-run ok ({len(rows) - skipped} checked, {skipped} skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
