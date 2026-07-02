#!/usr/bin/env python3
"""Compatibility wrapper for the renamepapers command."""

from __future__ import annotations

import sys
from pathlib import Path

_SIBLING_PACKAGE = Path(__file__).resolve().with_name("renamepapers")
_INSTALLED_PACKAGE_ROOT = Path.home() / ".local" / "lib" / "renamepapers"
if not _SIBLING_PACKAGE.is_dir() and _INSTALLED_PACKAGE_ROOT.is_dir():
    sys.path.insert(0, str(_INSTALLED_PACKAGE_ROOT))

from renamepapers.core import main


if __name__ == "__main__":
    raise SystemExit(main())
