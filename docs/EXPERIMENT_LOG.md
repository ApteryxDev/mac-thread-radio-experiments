> Working notes kept during the experiment, in the order things were tried. Paths such as `../work/...` are relative to the `bridge/` folder. Home network identifiers have been removed.

# Mac mini Thread radio experiment

The bridge connects one local TCP client carrying HDLC-framed Spinel to the
Apple TSI radio. It reuses the installed pythreadradio package and exits when
the client disconnects. The default session accepts one protocol-version query. The optional
capabilities session accepts five specific read-only queries. Both relay
only matching replies or status responses. The bridge and probe do not log
frame contents. Use the full-session option only with a trusted local client.
Loopback is accessible to other local processes.

## First hardware test

A live test with Apple’s service undisturbed found the TSI UUID, but
`os_channel_create` failed. The error is consistent with the channel being
owned, though it does not identify the cause by itself. The experiment could
not inspect the daemon’s current state or run privileged process controls.

Use the same supervised radio handoff that worked for the sniffer. Keep the
daemon suppression process in its own foreground terminal with its default
resume behavior. Stop any sniffer before opening the bridge. No changes to SIP
or AMFI are required for this attempt.

In one terminal, run the following from this directory.

```sh
"$HOME/pythreadradio/.venv/bin/python" spinel_bridge.py --live-radio
```

Within 30 seconds, run this in a second terminal from the same directory.

```sh
"$HOME/pythreadradio/.venv/bin/python" spinel_bridge.py --probe
```

The probe times out after two seconds. It issues one Spinel
`PROP_VALUE_GET(PROTOCOL_VERSION)` with no reset, channel change, dataset
operation, or Thread start. A matching reply establishes a basic hardware
round trip. It does not establish OpenThread compatibility.

Stop the bridge first if it is still running, then interrupt the daemon
suppression process with Control-C. Check `pgrep -x threadradiod` from
Terminal and use `ps -o pid,state,comm -p <PID>` with the resulting PID to
confirm the daemon is present and its state does not start with `T`.
If absent, check that the handoff process has exited, then ask launchd to
start the registered service and verify its state.

```sh
"$HOME/pythreadradio/.venv/bin/python" spinel_bridge.py --probe
```

The basic hardware test passed during a supervised handoff. After the
handoff, the kickstart command restored Apple’s service.

## Read-only capabilities test

Repeat the supervised handoff and add `--capabilities` to both the
`--live-radio` and `--probe` commands above. The probe prints the Spinel
protocol version, Spinel capabilities, RCP API version, minimum host API
version, and radio capability bitmask. It reports a Spinel status or timeout
for any unanswered property. Close the handoff and verify Apple's service
again before further experiments.

## OpenThread integration

The Mac radio reported Spinel 4.3, RCP API 11, minimum host API 4,
and radio capability mask 127. OpenThread commit
`806842ffc62f7a10f0bc26366e0bec77df3d8b9b` accepts those versions and
its required radio bits are present. The locally built CLI is in
`../work/ot-build/src/posix/ot-cli`.

The accompanying patch makes the explicit `no-reset` path proceed to its
normal compatibility checks without waiting for a reset notification.
It also disables OpenThread's optional temporary metrics storage and ends the
dry run before normal instance initialization can enable the radio's PHY.
The dry run completed against a simulated RCP with the Mac's reported
capabilities. The simulator observed exactly seven GETs and no writes.
The dry run uses isolated settings and leaves Thread down.

During a supervised handoff, start the bridge with this command from the
output directory.

```sh
"$HOME/pythreadradio/.venv/bin/python" spinel_bridge.py --live-radio --openthread-check
```

Run the check in a second terminal within 30 seconds.

```sh
"$HOME/pythreadradio/.venv/bin/python" check_openthread.py
```

The bridge accepts only the seven GET requests used by the pinned build's
startup check. The checker uses a private Unix socket to relay HDLC to Mac
loopback. The successful result is
`OpenThread dry run passed with Thread left down.`
Restore Apple's daemon and check its process state after the test.

The read-only dry run also passed on the actual Mac radio. For the next
stage, use `--live-radio --openthread-init` on the bridge and add
`--initialize` to the checker. This mode allows only the pinned CLI's
startup queries and radio-local configuration writes. It enables the PHY
and sets radio addresses and freshly generated test MAC keys in the RCP.
The checker uses an empty temporary settings directory, runs only
`state` and `ifconfig`, and exits. The simulated test returned
`disabled` and `down`. Restore Apple’s daemon immediately after the run.

A separate macOS build with native network interface, UDP, and border router
features enabled compiled at `../work/ot-net-build/src/posix/ot-cli`.
Full initialization with Thread disabled passed on the Mac radio using the
radio-only build. The Ethernet runtime check uses the second build.

The Mac reported an active 5 Gb/s link on the USB Ethernet adapter
`en8`. Its built-in Ethernet port `en0` was inactive. In the checker
terminal, run `sudo -v` before starting the supervised radio handoff.
Start the bridge with `--live-radio --openthread-init` as above, then run
`check_openthread.py --ethernet en8` using the same Python environment.
The checker verifies the Ethernet link, starts the networking build,
and checks that Thread is disabled and macOS allocated a Thread tunnel.
This does not test IPv6 forwarding. Restore Apple’s daemon immediately
after the check.

Full border routing remains a separate stage. If OTBR runs in a Linux VM,
verify its path back to the Mac bridge and its IPv6 and multicast
connectivity to the home Ethernet LAN. The Docker client was installed
but its daemon was unavailable during initial inspection.

For the Apple TV network, use only its existing Active Operational Dataset,
obtained through an authorized, consent-based path. Confirm it matches the
preferred network and the intended Apple TV network before applying it. Treat
the dataset as a secret and keep it out of shell history, logs, and shared
files. Channel 11 traffic alone does not identify the Apple TV network.

The end-to-end test is Mac attachment to the existing Thread network,
bidirectional IPv6 reachability across the border router, and stable operation
of the already-paired bedside HS2. If the Thread radios lack a usable RF path,
verify that both border routers can use Thread over Infrastructure (TREL)
before treating Ethernet as a substitute for that path.


## Temporary attachment to the existing mesh

Home Assistant received the Apple TV dataset from the iPhone. Its network
name and active timestamp matched the home Apple TV's Bonjour
announcement. The Extended PAN ID was consistent with the announcement's
escaped display.

`check_thread_attachment.py --preflight` verifies the expected dataset
and binary locally, without a radio handoff. For the live test, start the
bridge with `--live-radio --full-session` during a supervised handoff and
run `check_thread_attachment.py` within 30 seconds. The dedicated build
uses RAM-only settings and has OpenThread logging disabled. The checker
loads the dataset through local standard input, disables router eligibility,
and waits up to 30 seconds for the child role. It stops Thread and takes
its interface down afterward. A synthetic test verified the timeout
and cleanup path. The live test timed out even with the Apple TV awake.
Three valid frames reached OpenThread during that run. The attachment
build now emits fixed stage labels that the checker counts, including
Parent Responses, accepted candidates, and Child ID requests and
responses. It suppresses other child-process diagnostics. The simulated
test verified that stage counts and cleanup complete on timeout.

Restore Apple's daemon immediately after the test. A successful attachment
does not establish border routing or the HS2's RF reachability.

## Attachment over Ethernet with TREL

The home Apple TV advertised a `_trel._udp` service. Its target
hostname and Extended PAN ID matched the Apple TV and imported dataset.
The dedicated `ot-trel-build` adds TREL and OpenThread-managed multicast
DNS to the RAM-only attachment build.

For this test, run the same supervised handoff and full-session bridge
as before, then add `--trel en8` to `check_thread_attachment.py`. The
checker waits to discover a matching TREL peer on the active USB Ethernet
interface before it starts Thread. It reports Ethernet packet counters
and the attachment-stage counts, then shuts down Thread and discovery.
This checks attachment only and does not enable border routing.

The first live TREL check failed before attachment because the OpenThread
build excluded its mDNS public API and CLI. The corrected build includes
the API and uses periodic address polling on macOS. With a simulated radio,
the corrected build discovered the matching live Apple TV TREL peer on
`en8` without starting Thread. The live TREL-enabled attachment then passed. OpenThread counted three
inbound and four outbound TREL packets, processed a Parent Response and
Child ID Response, entered the child role, and detached at the end of
the check. Border routing and HS2 reachability remain unverified.

## Temporary border routing trial

The RAM-only networking build is in `../work/ot-border-build/src/posix/ot-cli`.
In the checker terminal, run `sudo -v` before starting the supervised
handoff. Use the full-session bridge and run
`check_thread_border_routing.py` within the bridge's 30-second window.

The trial attaches as a child via the existing Apple TV network, requests
router promotion, and checks that the partition stays the same. It then
temporarily enables macOS IPv6 forwarding if needed and enables OpenThread
border routing on `en8`. The route preference published in Thread Network
Data is low. When the trial announces the 75-second window, press a bedside
HS2 button already assigned a Home action. The checker reports aggregate
unicast forwarding, router advertisement, TREL, and child counts. Those
counts do not identify the HS2 or prove the route its traffic took.

The checker disables border routing, restores the starting forwarding value,
stops Thread, and exits. Restore Apple's daemon after it returns. The
success and failure cleanup paths were verified with simulated CLI and
forwarding controls. Live router promotion and forwarding remain unverified.

The completed live trial entered the router role in the existing partition,
ran border routing for 75 seconds, and restored IPv6 forwarding to 0.
It recorded four successful Router Advertisements, 69 inbound and 70
outbound TREL packets, and a peak of one attached Thread child. Both
border-routed unicast counters remained zero. The trial did not identify
the child, establish unicast forwarding, or verify an HS2 improvement.
