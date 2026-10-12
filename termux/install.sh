#!/data/data/com.termux/files/usr/bin/bash
# One-time installer: runs the PS5 gift card monitor on this Android phone.
#
# In Termux:
#   curl -fsSL https://raw.githubusercontent.com/AGL-123/ScanAmzn.In/HEAD/termux/install.sh | bash
#
# Safe to run again: it updates the code and keeps your settings.
#
# Everything is inside main(), which runs on the last line: with `curl | bash`
# bash has then read the whole script before any command (that might read
# from stdin) runs.

set -eu

main() {
  local REPO_URL="${SCANAMZN_REPO:-https://github.com/AGL-123/ScanAmzn.In.git}"
  local BIN="$PREFIX/bin"
  local APP_DIR SCRIPT_DIR

  step "Installing Python and git (this can take a few minutes)"
  # Termux needs its packages upgraded before installing new ones.
  pkg update -y </dev/null
  pkg upgrade -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold </dev/null
  pkg install -y python python-pip git </dev/null
  # Faster HTML parser, prebuilt for Termux; the bot works without it.
  pkg install -y python-lxml </dev/null >/dev/null 2>&1 || true

  # Use the checkout this script lives in, or the usual one in $HOME.
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
  if [ -n "$SCRIPT_DIR" ] && [ -d "$SCRIPT_DIR/../scanamzn" ]; then
    APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
  else
    APP_DIR="$HOME/ScanAmzn.In"
  fi
  if [ -d "$APP_DIR/.git" ]; then
    step "Updating the code in $APP_DIR"
    repair_old_install "$APP_DIR" "$BIN"
    git -C "$APP_DIR" pull --ff-only </dev/null
  elif [ -d "$APP_DIR/scanamzn" ]; then
    step "Using the code in $APP_DIR"
  else
    step "Downloading the code to $APP_DIR"
    git clone "$REPO_URL" "$APP_DIR" </dev/null
  fi
  cd "$APP_DIR" # `python -m scanamzn` runs from the code folder

  step "Installing Python packages"
  python -m pip install -q --upgrade requests beautifulsoup4 tzdata </dev/null

  step "Installing the 'ps5' command and home-screen buttons"
  chmod 700 "$APP_DIR/termux/ps5"
  # A small launcher so ps5 always finds the code. Remove first: an older
  # installer made this a symlink, and writing through it would overwrite
  # termux/ps5 itself.
  rm -f "$BIN/ps5"
  printf '#!/data/data/com.termux/files/usr/bin/bash\nSCANAMZN_DIR=%q exec %q "$@"\n' \
    "$APP_DIR" "$APP_DIR/termux/ps5" >"$BIN/ps5"
  chmod 700 "$BIN/ps5"
  "$BIN/ps5" install-shortcuts

  step "Setting up your alerts"
  if [ -f "$APP_DIR/config.toml" ]; then
    echo "Existing settings found in $APP_DIR/config.toml (change them with: ps5 setup)."
  else
    # A failed test alert must not stop the installation; only a missing
    # config does.
    python -m scanamzn -c "$APP_DIR/config.toml" setup </dev/tty || true
    if [ ! -f "$APP_DIR/config.toml" ]; then
      echo "Setup was not finished. Run it any time with: ps5 setup"
      exit 1
    fi
  fi

  step "Done!"
  cat <<'MSG'
Commands (type them in Termux):
  ps5 start    start watching Amazon.in
  ps5 status   is it running? what did it find?
  ps5 stop     stop watching
  ps5 test     send a test alert       ps5 setup   change your settings

Home-screen buttons: long-press your home screen > Widgets > Termux
(Termux:Widget), drag it out, and you get PS5 Start / Stop / Status /
Test alert / Settings / Check now.

IMPORTANT so Android does not stop the monitor: Settings > Apps > Termux >
Battery > Unrestricted, and allow "Display over other apps". See PHONE.md.
MSG

  local answer
  if "$BIN/ps5" running; then
    printf '\nThe monitor is running. Restart it with the new version? [Y/n] '
    { read -r answer </dev/tty; } 2>/dev/null || answer=n
    case "$answer" in
      "" | y | Y | yes) "$BIN/ps5" restart ;;
      *) echo "OK. It will use the new version after: ps5 restart" ;;
    esac
  else
    printf '\nStart the monitor now? [Y/n] '
    { read -r answer </dev/tty; } 2>/dev/null || answer=n
    case "$answer" in
      "" | y | Y | yes) "$BIN/ps5" start ;;
      *) echo "OK. Start it later with: ps5 start" ;;
    esac
  fi
}

repair_old_install() { # app dir, bin dir
  # The first installer version linked $PREFIX/bin/ps5 to termux/ps5, and
  # re-running a newer installer could then overwrite termux/ps5 with the
  # launcher (which then calls itself forever). Put the real script back.
  if grep -q '^SCANAMZN_DIR=' "$1/termux/ps5" 2>/dev/null; then
    echo "Repairing termux/ps5 from an earlier installation"
    git -C "$1" checkout -- termux/ps5
  fi
  if [ -L "$2/ps5" ]; then
    rm -f "$2/ps5"
  fi
}

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

case "${PREFIX:-}" in
  */com.termux/*) main "$@" ;;
  *)
    echo "This installer is for Termux on Android. On a PC, follow README.md instead."
    exit 1
    ;;
esac
