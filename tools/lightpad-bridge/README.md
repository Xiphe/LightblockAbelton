# Lightpad Bridge

One background macOS helper connects the Lightpad to the Python Remote Script in Ableton Live. It discovers the explicitly selected device, owns temporary program uploads, preserves the virtual MIDI ports across program changes and transports complete feedback records. Live owns audio, recording, layers and project state; the pad draws and animates locally.

This is the working side-button baseline, before the next metronome and input-selection UX changes. It has passed user checks over USB and Bluetooth, including mute/mixer controls, recording and layering after a modal round trip. A short Mode-button release toggles the metronome. A hold of at least 400 ms logs one reserved input-selection request; releasing the hold does not also trigger a short click. The input selector itself is not implemented.

| Temporary device program | Code + heap | Free of 7,200 bytes |
| --- | ---: | ---: |
| [Main controller](../../prototypes/controller/lightpad/main.littlefoot) | 6,279 | 921 |
| [Metronome](../../prototypes/controller/lightpad/metronome.littlefoot) | 4,319 | 2,881 |

The main screen's bottom metronome row is visual only. The separate metronome program currently keeps the tested meter, Click, Tap and Close regions. The full-screen redesign is follow-up work. Replacing a program takes a noticeable pause: six measured Bluetooth switches reached model readiness in about 2.2–2.5 seconds entering the metronome and 3.4–3.5 seconds returning. The user accepted this for occasional settings. Those are device-model markers, not optical timing or a guarantee for every connection.

## Build and stage from a checkout

Requirements are macOS, Python 3, CMake, Apple Command Line Tools and the pinned `blocksctl` compiler/SDK source. Install or build `blocksctl` separately from its repository. The supplier revision and SDK manifest are recorded in [sdk-lock.json](sdk-lock.json). The build checks all 685 SDK files before compiling and also retains the SDK's critical inline connection hash guard. It does not patch vendor files or accept license terms.

From the repository root, supply the SDK directory, a build directory, the exact 16-character device serial and a **new** output app path:

```sh
python3 tools/lightpad-bridge/build.py \
  --sdk /path/to/blocksctl/vendor/sdk \
  --build-dir /tmp/lightpad-baseline-build \
  --device EXACT_16_CHAR_ID \
  --output '/tmp/Lightpad Bridge.app'
```

`EXACT_16_CHAR_ID` is a placeholder; replace it with the actual 16-character serial. `build.py` uses the selected `DEVELOPER_DIR` or the installed Command Line Tools. It builds Release, runs the four native CTests, compiles both device sources through `blocksctl`, rejects either program above the 7,200-byte code-plus-heap limit, and stages a windowless app. Upstream ROLI/JUCE/GPL/zlib/libc++ notices accompany the app. Generated metadata records the binary, program and native-source hashes without local source paths. The serial is supplied locally and is never a repository default.

The command does **not** install or start the app, open MIDI ports, write firmware, save a device default or install a Live script. To stage from an already-built executable, use `package.py --help`; both device sources are mandatory and compile/budget checks still run. Existing output apps are never overwritten.

Packaging checks run offline:

```sh
python3 tools/lightpad-bridge/test_package.py -v
```

Live script preparation and explicit scratch-Set opt-in are documented in [the host README](../../prototypes/controller/live/README.md). Use matching device, helper and host versions together. Default checked-in host configuration does not enable recording in arbitrary projects.

## Runtime contract

- Set both ports of the `LightpadController` control surface to **Lightpad Bridge**. The generic Track input for that virtual port is disabled in the tested setup.
- The helper uses protocol 18/A8 and forwards complete state, clock and UI records as checksummed SDK packets. Raw contact frames retain all sixteen channels and recording-view wakeups. Native recording scheduling does not wait for graphics.
- Modal entry asks Live for fresh, nonce-correlated permission. Recording, pending cleanup, held contacts or invalid project scope deny entry. Stale grants and new raw input cancel a request. Close End reaches Live before returning to the main program.
- Device discovery verifies the exact serial, standalone topology and matching CoreMIDI endpoint identity. USB is preferred for a new connection; a healthy Bluetooth connection is retained when USB is plugged in.
- One helper owns the SDK session. Singleton and SDK per-port locks reject competing owners. Do not run a separate CLI uploader, Dashboard or BLOCKS Code against the same device concurrently.
- Reconnection discards stale feedback, temporarily reloads the main program and handshakes again. Program changes preserve the Live Session object and accepted audio; they reset local pad contacts and reseed display state.

Opening the installed `.app` starts the helper with no Dock window. Status, lock and event files are under `~/Library/Application Support/Lightpad Bridge/`; the log is under `~/Library/Logs/Lightpad Bridge/`. Login autostart is not enabled. Closing the helper ends its temporary SDK session and leaves the saved startup program intact. Nothing in this build/stage flow writes firmware or saves a new startup program.

The latest bounded trial had no new bridge errors during Bluetooth performance. One retained stale-FIFO error arose during USB removal before recovery; an old error string can remain in status while `state` is `ready`. Power-cycle, sleep/wake and long-duration workloads remain broader helper follow-up checks.
