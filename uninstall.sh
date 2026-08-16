#!/usr/bin/env bash
#
# One-step uninstaller.
#
#   ./uninstall.sh
#
# Removes the tool. Prefixes are left exactly as they are: the runtime stays
# installed in the games that got it, because removing it would break them.
# Use --clean-markers if you want the tool to redo every prefix on a future
# install.
#
set -euo pipefail

APP_NAME="proton-vcredist"
INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_NAME"
LAUNCHER="$HOME/.local/bin/$APP_NAME"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$APP_NAME.service"
CACHE="$HOME/.cache/$APP_NAME"

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }

[ "$(id -u)" -ne 0 ] || { echo "Run this as your normal user." >&2; exit 1; }

say "Removing the login service"
systemctl --user disable --now "$APP_NAME.service" 2>/dev/null || true
rm -f "$UNIT"
systemctl --user daemon-reload 2>/dev/null || true
systemctl --user reset-failed "$APP_NAME.service" 2>/dev/null || true

if [ "${1:-}" = "--clean-markers" ]; then
    say "Removing marker files from prefixes"
    for base in "$HOME/.steam/steam" "$HOME/.local/share/Steam"; do
        [ -d "$base" ] || continue
        find "$base" -maxdepth 4 -name '.vcredist-installed' -delete 2>/dev/null || true
    done
fi

say "Removing files"
rm -rf "$INSTALL_DIR" "$CACHE"
rm -f "$LAUNCHER"
rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/applications/$APP_NAME.desktop"

if [ -f "$HOME/.bashrc" ] && grep -qF "# added by $APP_NAME" "$HOME/.bashrc" 2>/dev/null; then
    sed -i "/# added by $APP_NAME/,+1d" "$HOME/.bashrc"
fi

cat <<EOF

$(say "Done.")

  The Visual C++ runtime stays installed in the prefixes that received it.
  That is deliberate -- removing it would break the games now relying on it.
EOF
