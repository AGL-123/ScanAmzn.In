#!/data/data/com.termux/files/usr/bin/bash
# One-time installer: runs the PS5 gift card monitor on this Android phone.
#
# In Termux, either
#   curl -fsSL https://raw.githubusercontent.com/AGL-123/ScanAmzn.In/HEAD/termux/install.sh | bash
# (public repository), or, after cloning the repository yourself,
#   bash ScanAmzn.In/termux/install.sh
#
# Safe to run again: it updates the code and keeps your settings.
set -eu

REPO_URL="${SCANAMZN_REPO:-https://github.com/AGL-123/ScanAmzn.In.git}"
BIN="$PREFIX/bin"
SHORTCUTS="$HOME/.shortcuts"
BOOT="$HOME/.termux/boot"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

case "${PREFIX:-}" in
  */com.termux/*) ;;
  *)
    echo "This installer is for Termux on Android. On a PC, follow README.md instead."
    exit 1
    ;;
esac

step "Installing Python and git (this can take a few minutes)"
pkg update -y
pkg install -y python git
python -m pip --version >/dev/null 2>&1 || pkg install -y python-pip
# Optional faster HTML parser; the bot works without it.
pkg install -y python-lxml >/dev/null 2>&1 || true

# Use the checkout this script lives in, or clone one.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SCRIPT_DIR" ] && [ -d "$SCRIPT_DIR/../scanamzn" ]; then
  APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
  step "Using the code in $APP_DIR"
else
  APP_DIR="$HOME/ScanAmzn.In"
  if [ -d "$APP_DIR/.git" ]; then
    step "Updating the code in $APP_DIR"
    git -C "$APP_DIR" pull --ff-only
  else
    step "Downloading the code to $APP_DIR"
    git clone "$REPO_URL" "$APP_DIR"
  fi
fi

cd "$APP_DIR"  # `python -m scanamzn` runs from the code folder

step "Installing Python packages"
python -m pip install --disable-pip-version-check -q requests beautifulsoup4

step "Installing the 'ps5' command and home-screen buttons"
chmod +x "$APP_DIR/termux/ps5"
if [ "$APP_DIR" != "$HOME/ScanAmzn.In" ]; then
  # Tell ps5 where the code lives.
  printf '#!/data/data/com.termux/files/usr/bin/bash\nSCANAMZN_DIR=%q exec %q "$@"\n' \
    "$APP_DIR" "$APP_DIR/termux/ps5" >"$BIN/ps5"
  chmod +x "$BIN/ps5"
else
  ln -sf "$APP_DIR/termux/ps5" "$BIN/ps5"
fi

mkdir -p "$SHORTCUTS" "$BOOT"
shortcut() {  # name, command
  printf '#!/data/data/com.termux/files/usr/bin/bash\n%s\n' "$2" >"$SHORTCUTS/$1"
  chmod +x "$SHORTCUTS/$1"
}
shortcut "PS5 Start" 'ps5 start; sleep 6'
shortcut "PS5 Stop" 'ps5 stop; sleep 3'
shortcut "PS5 Status" 'ps5 status; echo; read -r -p "Press Enter to close " _'
shortcut "PS5 Test alert" 'ps5 test; echo; read -r -p "Press Enter to close " _'
shortcut "PS5 Settings" 'ps5 setup; echo; read -r -p "Press Enter to close " _'
shortcut "PS5 Check now" 'ps5 check; echo; read -r -p "Press Enter to close " _'

# Restart after the phone reboots (needs the Termux:Boot app), if it was on.
printf '#!/data/data/com.termux/files/usr/bin/sh\ntermux-wake-lock\n"%s/ps5" boot\n' "$BIN" >"$BOOT/ps5-monitor"
chmod +x "$BOOT/ps5-monitor"

step "Setting up your alerts"
if [ -f "$APP_DIR/config.toml" ]; then
  echo "Existing settings found in $APP_DIR/config.toml (change them with: ps5 setup)."
else
  python -m scanamzn -c "$APP_DIR/config.toml" setup </dev/tty
fi

step "Done!"
cat <<'MSG'
Commands (type them in Termux):
  ps5 start    start watching Amazon.in
  ps5 status   is it running? what did it find?
  ps5 stop     stop watching
  ps5 test     send a test alert       ps5 setup   change your settings

Home-screen buttons: install the Termux:Widget app (same store you got Termux
from), long-press your home screen > Widgets > Termux:Widget, and you get
buttons for PS5 Start / Stop / Status / Test alert / Settings / Check now.

IMPORTANT so Android does not stop the monitor: Settings > Apps > Termux >
Battery > Unrestricted (see PHONE.md for your phone brand).
MSG

printf '\nStart the monitor now? [Y/n] '
{ read -r answer </dev/tty; } 2>/dev/null || answer=n
case "$answer" in
  ""|y|Y|yes) "$BIN/ps5" start ;;
  *) echo "OK. Start it later with: ps5 start" ;;
esac
