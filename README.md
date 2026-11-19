<div align="center">

# 📡 Mac Thread Radio Experiments

### Turning a Mac's built-in Thread radio into a router for an Apple Home mesh

A personal hardware experiment: driving the Thread radio inside an Apple Silicon Mac with open-source OpenThread, joining an existing Apple TV Thread network, and fixing a slow bedside button by giving it a closer parent.

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![OpenThread](https://img.shields.io/badge/Stack-OpenThread-2E7D32?style=for-the-badge)](https://openthread.io/)
[![macOS](https://img.shields.io/badge/Platform-macOS-111111?style=for-the-badge&logo=apple&logoColor=white)](https://www.apple.com/macos/)
[![launchd](https://img.shields.io/badge/Service-launchd-555555?style=for-the-badge&logo=apple&logoColor=white)](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)

![Radio](https://img.shields.io/badge/Radio-Spinel_4.3%2C_RCP_API_11-blue?style=flat-square)
![Role](https://img.shields.io/badge/Result-Router_in_existing_partition-green?style=flat-square)
![Transport](https://img.shields.io/badge/Backhaul-TREL_over_Ethernet-orange?style=flat-square)
![Status](https://img.shields.io/badge/Status-Experimental-red?style=flat-square)

**Personal project · not affiliated with Apple or the OpenThread project**

</div>

> ⚠️ **Read this first.** This project pauses Apple's own `threadradiod` system daemon and talks to the radio directly. It is experimental, unsupported and may stop working after any macOS update. While it runs, the Mac's own Home and Matter Thread features are unavailable. Use it only on your own devices and your own Thread network.

## 📖 Overview

My bedside HS2 button (a battery-powered Thread device) was slow and often missed presses. The only Thread border router at home is the Apple TV in another room, so I wanted to know whether the Thread radio built into my Mac could help.

Newer Apple Silicon Macs contain a Broadcom radio with a Thread RCP that speaks Spinel. [pythreadradio](https://github.com/JJTech0130/pythreadradio) showed that it can be opened directly once Apple's daemon is out of the way. This repository builds on that in stages:

| | Stage | What it showed |
|:---:|---|---|
| 1️⃣ | **Spinel bridge** | A one-client TCP bridge to the radio, with allowlists for each test stage |
| 2️⃣ | **Read-only probe** | The radio reports Spinel 4.3, RCP API 11, minimum host API 4 and radio capability mask 127 |
| 3️⃣ | **OpenThread dry run and init** | A pinned OpenThread build accepts the radio, first read-only, then with radio-local writes |
| 4️⃣ | **Attach over TREL** | The Mac attaches as a child to the Apple TV network, using Thread over Infrastructure on Ethernet |
| 5️⃣ | **Router and border routing trial** | The Mac is promoted to router in the same partition; the HS2 became fast and reliable |
| 6️⃣ | **Boot service** | A launchd service keeps the Mac on the mesh as a router from boot |

---

## 🏆 Key Results

The border routing trial was run three times for 75 seconds each on 19 November 2026, with the HS2 pressed during the window.

| Measure | Run 1 | Run 2 | Run 3 |
|---|:---:|:---:|:---:|
| Router role in the existing partition | ✅ | ✅ | ✅ |
| Peak Thread children attached to the Mac | 1 | 2 | 1 |
| Border-routed unicast packets (in / out) | 0 / 0 | 0 / 0 | 0 / 0 |
| Router Advertisements sent | 4 | 4 | 4 |
| TREL packets (in / out) | 69 / 70 | 102 / 103 | 56 / 57 |
| HS2 response | not recorded | **much faster, worked across the room** | **fast again** |

**What I took from the experiment:**

- **The improvement came from radio coverage, not from border routing.** Both unicast counters stayed at zero, so the Mac never routed traffic to the home LAN. What changed is that a Thread router was now close to the bed.
- **The Mac and the Apple TV did not need a direct radio path.** All traffic between them went over TREL on the Ethernet link.
- **A sleepy child attached to the Mac each time.** Its 240-second timeout fits a battery button, but I did not formally match its address to the HS2, so this is strong evidence and not proof.
- **The robust fix is a mains-powered Thread device near the bed.** The boot service here does the same job with hardware I already had, at the cost of pausing Apple's daemon.

When it was installed as a service, the Mac went from boot script to router in the existing partition in **7 seconds**.

---

## 🗺️ How It Works

```text
Apple TV (leader, border router)
        ↕  TREL over Ethernet (en8)
Mac: OpenThread ot-cli  ←→  Unix socket  ←→  TSI channel  ←→  Thread radio (RCP)
        ↕  802.15.4 radio
Bedside HS2 (sleepy child)
```

1. `daemonslayer` (from pythreadradio) kills Apple's `threadradiod` and pauses any new instance, which frees the radio's TSI channel.
2. A small relay passes HDLC-framed Spinel between OpenThread and the radio.
3. OpenThread loads the network's existing Active Operational Dataset, finds the Apple TV over TREL and attaches as a **child**. Router eligibility is off at this point, so the Mac cannot start its own network.
4. Router eligibility is turned on and the Mac asks for the **router** role inside the same partition.
5. Nearby devices, like the HS2, can now pick the Mac as their parent.

<details>
<summary><b>🔍 Why the Mac never runs its own partition</b></summary>

If a router-eligible device cannot find its network, OpenThread may make it the leader of a new partition. Devices could then attach to that separate network. To avoid this, the Mac always joins as a non-eligible child first. The service then treats leader role, a partition change, or two minutes detached as a reason to stop and start again from scratch.

</details>

---

## 🚀 Start here: tutorial

**What you need:**
- An Apple Silicon Mac whose Thread radio pythreadradio supports (M3 or later; **not** the newer Apple N1 chip). This was tested on a Mac with a USB Ethernet adapter.
- An existing Thread network with a border router that advertises TREL, like a recent Apple TV.
- A wired Ethernet connection from the Mac to the same LAN as that border router.
- Python 3.12+, Xcode command line tools, CMake and Ninja (`brew install cmake ninja`).
- Your network's Active Operational Dataset, obtained with consent (see step 3).

### 1. Get this repository and pythreadradio

```sh
git clone https://github.com/ApteryxDev/mac-thread-radio-experiments.git
cd mac-thread-radio-experiments

git clone https://github.com/JJTech0130/pythreadradio.git ~/pythreadradio
python3.12 -m venv ~/pythreadradio/.venv
~/pythreadradio/.venv/bin/pip install -e ~/pythreadradio
```

### 2. Build the pinned OpenThread

The scripts expect OpenThread builds under `work/`, which Git ignores.

```sh
mkdir -p work && cd work
git clone https://github.com/openthread/openthread.git
git -C openthread checkout 806842ffc62f7a10f0bc26366e0bec77df3d8b9b
git -C openthread apply ../../bridge/openthread-no-reset.patch

cmake -G Ninja -S openthread -B ot-border-build \
  -DOT_PLATFORM=posix -DOT_FTD=ON -DOT_SETTINGS_RAM=ON -DOT_LOG_LEVEL=NONE \
  -DOT_THREAD_VERSION=1.4 -DOT_PLATFORM_NETIF=ON -DOT_PLATFORM_UDP=ON \
  -DOT_BORDER_ROUTER=ON -DOT_BORDER_ROUTING=ON -DOT_MDNS=ON \
  -DOT_TREL=ON -DOT_TREL_MANAGE_DNSSD=ON -DOT_COMPILE_WARNING_AS_ERROR=OFF \
  -DCMAKE_CXX_FLAGS="-DOT_THREAD_ATTACH_TRACE=1 -DOPENTHREAD_CONFIG_MULTICAST_DNS_PUBLIC_API_ENABLE=1 -DOPENTHREAD_POSIX_CONFIG_MDNS_ADDR_MONITOR=OT_POSIX_MDNS_ADDR_MONITOR_PERIODIC"
ninja -C ot-border-build ot-cli
cd ..
```

The patch makes OpenThread's `no-reset` startup continue without waiting for a reset notification (the Mac radio must not be reset), and adds fixed attach-stage labels that the checkers count. The earlier stages used smaller builds (`ot-build`, `ot-net-build`, `ot-attach-build`, `ot-trel-build`); the experiment log lists what each one adds.

### 3. Provide your network's dataset

I used Home Assistant: the Home Assistant Companion app on the iPhone can share the Apple Thread credentials with Home Assistant. Copy Home Assistant's `.storage/thread.datasets` to:

```text
work/ha-thread-import/.storage/thread.datasets
```

Then copy `bridge/network.example.json` to `bridge/network.json` and fill in your network name and Extended PAN ID (shown on Home Assistant's Thread page). The scripts refuse to use any dataset that does not match both values. Treat the dataset as a secret: both files are ignored by Git.

### 4. Run a supervised test

You need three Terminal tabs. In **Tab 3**, run `sudo -v` first.

```sh
# Tab 1: release the radio (leave it running)
sudo ~/pythreadradio/.venv/bin/daemonslayer threadradiod

# Tab 2: start the bridge
~/pythreadradio/.venv/bin/python bridge/spinel_bridge.py --live-radio --full-session

# Tab 3: within 30 seconds
~/pythreadradio/.venv/bin/python bridge/check_thread_border_routing.py
```

When Tab 3 says `Border routing running on en8`, press your Thread button and let the full 75 seconds run. The checker prints the child table, routing and TREL counters, then cleans up.

Afterwards, in **Tab 1**, press **Control-C** and restore Apple's daemon:

```sh
launchctl kickstart system/com.apple.threadradiod
pid="$(pgrep -x threadradiod)" && ps -o pid,state,comm -p "$pid"
```

If `STAT` starts with `T`, the daemon is still paused: run `sudo kill -CONT "$pid"`.

### 5. Install the boot service (optional)

```sh
cd service
cp config.example.json config.json   # then edit: interface, network name, Extended PAN ID
bash install.sh                      # asks for sudo
bash status.sh                       # after about a minute
```

A healthy log ends with `Found the Apple TV over TREL.`, `Attached as a child…`, `Promoted to router…` and `Role: router`. To remove everything and give the radio back to Apple, run `bash uninstall.sh`. See [`service/README.md`](service/README.md) for what the service does and its limits.

**Need help?** [Full experiment log](docs/EXPERIMENT_LOG.md) · [Service details](service/README.md)

---

## 🧪 Tests

Two test files run without the radio:

```sh
python3 tests/check_border_trial_mock.py   # border trial: promotion, cleanup, forwarding restore, child table
python3 tests/test_service_logic.py        # boot service: join order, leader refusal, partition guard
```

The other files in `tests/` are the simulated-radio checks used during the experiment. They need the matching local OpenThread builds and, for some, pythreadradio.

---

## 📂 Project Structure

```text
mac-thread-radio-experiments/
├── bridge/
│   ├── spinel_bridge.py              TCP bridge to the TSI radio, with per-stage allowlists
│   ├── check_openthread.py           Dry run, init and Ethernet checks
│   ├── check_thread_attachment.py    Child attachment, optionally over TREL
│   ├── check_thread_border_routing.py  75-second router and border routing trial
│   ├── openthread-no-reset.patch     Patch for the pinned OpenThread commit
│   └── network.example.json          Template for your network identity
├── service/
│   ├── macthreadrouter.py            Boot-time router supervisor (root launchd daemon)
│   ├── install.sh · uninstall.sh · status.sh
│   ├── config.example.json
│   └── README.md
├── tests/                            Mock and simulated-radio checks
├── docs/EXPERIMENT_LOG.md            Stage-by-stage notes
└── README.md
```

The Thread dataset, network identifiers, OpenThread source and builds, and logs are not published.

---

## 🤝 Contributions and AI Assistance

| My contribution | AI-assisted work |
|---|---|
| The idea, the hardware, and all decisions about what to test and when to stop | Planning the stages and designing the safety limits of each test |
| Running every supervised handoff and live test on my own Mac and network | Python bridge, checker and service code, and the OpenThread patch |
| Pressing the HS2 and judging whether it improved | Interpreting counters and logs between runs |
| Exporting the dataset through Home Assistant with consent | Documentation and README drafting |

ChatGPT, Codex and Claude assisted throughout. The code and prose are not represented as exclusively manual work. pythreadradio and OpenThread remain the work of their original authors.

---

## 🛠️ Built With

| Tool | Role |
|---|---|
| 🐍 **Python 3.12** | Bridge, checkers and boot service |
| 📡 **[pythreadradio](https://github.com/JJTech0130/pythreadradio)** | Access to the Mac's TSI radio channel and `daemonslayer` |
| 🧵 **[OpenThread](https://github.com/openthread/openthread)** | Thread stack (POSIX host, RCP mode) |
| 🌐 **TREL** | Thread over Infrastructure between the Mac and the Apple TV |
| 🏠 **Home Assistant** | Consent-based export of the Thread dataset |
| 🍎 **launchd · Keychain** | Starting at boot and storing the dataset |

---

## 📚 Sources and Credits

- **[pythreadradio](https://github.com/JJTech0130/pythreadradio)** by JJTech0130: the TSI channel access and `daemonslayer` this project depends on. It is not included here; install it separately.
- **[OpenThread](https://github.com/openthread/openthread)**: the Thread stack, used at commit `806842f` with a small local patch.
- **[Spinel protocol](https://datatracker.ietf.org/doc/html/draft-rquattle-spinel-unified)**: the host to RCP protocol.
- **[Thread 1.4 / TREL](https://www.threadgroup.org/)**: Thread over Infrastructure.

This repository does not replace the licences of the projects above.

---

<div align="center">

**📡 A personal experiment by [ApteryxDev](https://github.com/ApteryxDev)**  
*Mac Thread radio · OpenThread · Apple Home*

</div>
