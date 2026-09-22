#!/usr/bin/env bash
# Install the `jev` command for the current user (no sudo) into ~/.local/bin.
#
# Usage: scripts/install.sh [--typesafe] [--editable]
#   --typesafe   also install the optional native TypeSafe SDK provider
#   --editable   link to this checkout so source edits take effect immediately
#
# Uses `uv tool` if available, else `pipx`, else a private venv in
# ~/.local/share/jev-decisions. Re-running upgrades/reinstalls.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$HOME/.local/bin"
EXTRAS=""
EDITABLE=""

for arg in "$@"; do
  case "$arg" in
    --typesafe) EXTRAS="[typesafe]" ;;
    --editable) EDITABLE="-e" ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

SPEC="$REPO$EXTRAS"

if command -v uv >/dev/null 2>&1; then
  echo "Installing with uv tool..."
  uv tool install --force $EDITABLE "$SPEC"
elif command -v pipx >/dev/null 2>&1; then
  echo "Installing with pipx..."
  pipx install --force $EDITABLE "$SPEC"
else
  VENV="$HOME/.local/share/jev-decisions/venv"
  echo "Installing into $VENV..."
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
    || { echo "Python >= 3.11 required" >&2; exit 1; }
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet --upgrade $EDITABLE "$SPEC"
  mkdir -p "$BIN_DIR"
  ln -sf "$VENV/bin/jev" "$BIN_DIR/jev"
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "NOTE: $BIN_DIR is not on PATH. Add to your shell rc:"
     echo "  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

echo "Installed: $("$BIN_DIR/jev" --version 2>/dev/null || echo 'jev (open a new shell to use)')"
