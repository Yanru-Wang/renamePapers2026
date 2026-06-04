#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
TARGET="${BIN_DIR}/renamepapers"

mkdir -p "${HOME}/Papers/Inbox" "${HOME}/Papers/Renamed" "${BIN_DIR}"
cp "${SCRIPT_DIR}/renamepapers.py" "${TARGET}"
chmod +x "${TARGET}"

if ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' "${HOME}/.zshrc" 2>/dev/null; then
  printf '\n# Personal commands\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "${HOME}/.zshrc"
fi

echo "Installed ${TARGET}"
echo "Use: renamepapers"
echo "Dry run: renamepapers --dry-run"
