# Mac Thread router service

Keeps this Mac on the home Apple TV Thread network as a Thread router,
starting at boot. In the 2026-11-19 tests the bedside HS2 became fast and reliable
while the Mac was a router. One sleepy child attached to the Mac during the test.
The Mac relays child traffic to the Apple TV over TREL on en8.

Router only: border routing stays disabled and macOS IPv6 forwarding is never
changed.

## Install, check, remove

First copy config.example.json to config.json and fill in your interface,
network name and Extended PAN ID.

    bash install.sh      # asks for sudo; starts the service immediately
    bash status.sh       # service state, Apple daemon state, recent log
    bash uninstall.sh    # stops it, deletes files and the keychain item, restores Apple's daemon

Pause without uninstalling:

    sudo launchctl bootout system/local.macthreadrouter
    sudo launchctl bootstrap system /Library/LaunchDaemons/local.macthreadrouter.plist

## What it does

* Suppresses Apple's threadradiod with daemonslayer while it runs, and resumes
  it on every exit. The Mac's own Home/Matter Thread features are unavailable
  while the service runs.
* Joins as a child first (cannot become leader), then requests the router role
  in the same partition. If it ever becomes leader, the partition changes, or
  it stays detached for 2 minutes, it stops and restarts the session.
* Failures back off (30 s, 60 s, ... up to 30 min). Ten stable minutes clears it.
* The Thread dataset is stored in the System keychain
  (service local.macthreadrouter.dataset) and is never logged.

## Files

* /Library/Application Support/MacThreadRouter/ (root-owned copy of the script,
  ot-cli from work/ot-border-build and the threadradio package)
* /Library/LaunchDaemons/local.macthreadrouter.plist
* /Library/Logs/MacThreadRouter.log

## Limits

* A macOS update can change threadradiod or the TSI radio and break this.
  If the HS2 gets slow again, run status.sh first.
* The job runs the Python interpreter from the pythreadradio venv's base
  install. If that Python is upgraded or removed, re-run install.sh.
  That interpreter runs as root, so keep its install owned by you only.
* If the USB Ethernet adapter gets a different name than en8, edit
  config.json and re-run install.sh.
* If the Mac sleeps, the session may drop; it restarts on wake. Disabling
  system sleep keeps it steady.
* The Home Assistant dataset copy in work/ha-thread-import is still on disk.
