#!/bin/bash
# Install the Mac Thread router as a launchd service that starts at boot.
# Run from your own Terminal (not with sudo); it asks for sudo itself.
set -euo pipefail

LABEL="local.macthreadrouter"
APP="/Library/Application Support/MacThreadRouter"
PLIST="/Library/LaunchDaemons/$LABEL.plist"
LOG="/Library/Logs/MacThreadRouter.log"
KEY_SERVICE="local.macthreadrouter.dataset"
KEY_ACCOUNT="active-dataset"
KEYCHAIN="/Library/Keychains/System.keychain"

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
BRIDGE="$ROOT/bridge"
BIN="${OT_CLI:-$ROOT/work/ot-border-build/src/posix/ot-cli}"
THREADRADIO="$HOME/pythreadradio/threadradio"
VENV_PY="$HOME/pythreadradio/.venv/bin/python"
fail() { echo "install: $*" >&2; exit 1; }
[ -f "$HERE/config.json" ] || fail "copy config.example.json to config.json and fill in your network first"

IFACE="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["interface"])' "$HERE/config.json")"
[ "$(id -u)" -ne 0 ] || fail "run this without sudo; it will ask for your password"
[ -x "$BIN" ] || fail "OpenThread build not found at $BIN"
[ -f "$THREADRADIO/skywalk.py" ] || fail "pythreadradio not found at $THREADRADIO"
[ -x "$VENV_PY" ] || fail "pythreadradio venv not found"
/sbin/ifconfig "$IFACE" 2>/dev/null | grep -q 'status: active' || fail "$IFACE has no active link"

PY="$("$VENV_PY" -c 'import os, sys; print(sys._base_executable)')"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 12))' || fail "Python 3.12+ required ($PY)"
echo "Using Python: $PY"

sudo -v

echo "Stopping any previous copy of the service..."
sudo launchctl bootout "system/$LABEL" 2>/dev/null || true

echo "Copying files to $APP..."
sudo mkdir -p "$APP/lib"
sudo rm -rf "$APP/lib/threadradio"
sudo cp -R "$THREADRADIO" "$APP/lib/threadradio"
sudo rm -rf "$APP/lib/threadradio/__pycache__"
sudo install -m 755 "$BIN" "$APP/ot-cli"
sudo install -m 755 "$HERE/macthreadrouter.py" "$APP/macthreadrouter.py"
sudo install -m 644 "$HERE/config.json" "$APP/config.json"
sudo rm -f "$APP/state.json"
sudo chown -R root:wheel "$APP"
sudo chmod -R go-w "$APP"

echo "Storing the Thread dataset in the System keychain..."
# The dataset goes through a pipe into 'security -i', so it never appears
# in any process's command line or in shell history.
"$VENV_PY" -I -c '
import sys
sys.path.insert(0, sys.argv[1])
from check_thread_attachment import load_dataset
print("add-generic-password -U -s %s -a %s -T /usr/bin/security -w %s %s"
      % (sys.argv[2], sys.argv[3], load_dataset().hex(), sys.argv[4]))
 ' "$BRIDGE" "$KEY_SERVICE" "$KEY_ACCOUNT" "$KEYCHAIN" | sudo /usr/bin/security -i >/dev/null
sudo /usr/bin/security find-generic-password -s "$KEY_SERVICE" -a "$KEY_ACCOUNT" "$KEYCHAIN" >/dev/null \
  || fail "dataset was not stored in the System keychain"

echo "Installing the launchd job..."
sudo tee "$PLIST" >/dev/null <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>-I</string>
    <string>$APP/macthreadrouter.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ExitTimeOut</key><integer>45</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
sudo chown root:wheel "$PLIST"
sudo chmod 644 "$PLIST"
plutil -lint "$PLIST" >/dev/null

echo "Starting the service..."
sudo launchctl bootstrap system "$PLIST"
echo
echo "Installed. It now starts at every boot."
echo "Check it in about a minute with:  $HERE/status.sh"
