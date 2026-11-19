#!/bin/bash
# Remove the Mac Thread router service and give the radio back to Apple.
set -uo pipefail

LABEL="local.macthreadrouter"
APP="/Library/Application Support/MacThreadRouter"
PLIST="/Library/LaunchDaemons/$LABEL.plist"

sudo -v || exit 1
echo "Stopping the service (this cleanly stops OpenThread)..."
sudo launchctl bootout "system/$LABEL" 2>/dev/null || true
sudo rm -f "$PLIST"
sudo /usr/bin/security delete-generic-password -s local.macthreadrouter.dataset \
  -a active-dataset /Library/Keychains/System.keychain >/dev/null 2>&1 || true
sudo rm -rf "$APP"

echo "Restoring Apple's threadradiod..."
for pid in $(pgrep -x threadradiod); do sudo kill -CONT "$pid" 2>/dev/null; done
sudo launchctl kickstart system/com.apple.threadradiod 2>/dev/null || true
sleep 1
pid="$(pgrep -x threadradiod)" && ps -o pid,state,comm -p "$pid" || echo "threadradiod is not running yet"
echo "Removed. The log is kept at /Library/Logs/MacThreadRouter.log"
