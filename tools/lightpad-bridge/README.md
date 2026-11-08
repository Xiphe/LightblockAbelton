# Lightpad Bridge

One background macOS helper connects the Lightpad to the Python Remote Script in Ableton Live. It discovers the explicitly selected device, owns temporary program uploads, preserves the virtual MIDI ports across program changes and transports complete feedback records. Live owns audio, recording, layers and project state; the pad draws and animates locally.

The native helper is unchanged from working checkpoint `35e2a8d`, which passed user checks over USB and Bluetooth, including mute/mixer controls, recording and layering after a modal round trip. The current device/host sources add the approved full-screen metronome trial; automated checks pass, the latest digit/raster refinement and double-tap reset passed the USB trial, and Hannes reported that the current version works well over Bluetooth on 9 November 2026. A short Mode-button release toggles the metronome after pad contacts are lifted. A hold of at least 400 ms logs one reserved input-selection request; releasing the hold does not also trigger a short click. The input selector itself is not implemented.

| Temporary device program | Code + heap | Free of 7,200 bytes |
| --- | ---: | ---: |
| [Main controller](../../prototypes/controller/lightpad/main.littlefoot) | 6,283 | 917 |
| [Metronome](../../prototypes/controller/lightpad/metronome.littlefoot) | 5,331 | 1,869 |

The main screen's bottom metronome row is visual only. The separate metronome program has two seven-column meter fields above seven-by-five Click/Tap buttons, with a black, inactive center column and no Close button. White digits always show confirmed Live values, including changes received while dragging. The held-field raster stays beneath the digits: reachable two-LED detent lines use exactly the resting field colors, blue `#071724` or purple `#1b0c26`; the selected line is bright field color, and other background pixels are black. Unconfirmed raster intent may remain beneath the digits for at most 200 ms after release. Relative dragging retains a quarter-LED return hysteresis. Click uses a green/red background and a five-by-three track with a two-by-three white knob, left when off and right when on. Tap is uniformly dim monochrome with a brief brighter pressed state; it has no icon or full-white flash. The preview adds no transport lane or Native command.

The host also recognizes independent double-taps on the numerator and denominator fields, resetting the tapped value to 4 on the second release. Completing both fields' double-taps requests 4/4 through the existing serialized meter operations. Ordinary single taps have no action, and recognition adds no delay to dragging. Hannes confirmed the per-field and simultaneous double-tap resets, plus unchanged dragging, over USB on 9 November 2026. Helper, device programs, wire format and memory sizes are unchanged by the addition.

A graphics-only follow-up makes both pad programs reuse one phase sample per repaint. A deterministic odd-meter opposite-edge jump now passes its regression, as do offline 4/4 endpoint and clock-refresh continuity checks. The user reported "looks good" in the subsequent USB flash check. Hannes subsequently gave a positive general Bluetooth report for the current version on 9 November 2026. Host, native helper and gesture code are unchanged by the phase fix.

Replacing a program takes a noticeable pause. In the frozen checkpoint, six measured Bluetooth switches reached model readiness in about 2.2–2.5 seconds entering the smaller metronome program and 3.4–3.5 seconds returning. The user accepted this for occasional settings. Those are device-model markers, not optical timing or a guarantee for the new larger program. A positive user-reported Bluetooth check is recorded for the current version; new numerical switch timings were not measured. The [working-baseline document](../../docs/integration/working-baseline.md) and [baseline hashes](../../prototypes/controller/live/baseline-sha256.json) remain frozen provenance for that checkpoint.

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

Live script preparation and explicit scratch-Set opt-in are documented in [the host README](../../prototypes/controller/live/README.md). Deploy the current modal source and host routing together, preserving the reviewed local configuration. Protocol 18/A8 and native helper bytes are unchanged, so a version number alone does not distinguish the old and new control geometry. Restart the helper to load changed source files and explicitly reload the updated Live script. Default checked-in host configuration does not enable recording in arbitrary projects.

## Runtime contract

- Set both ports of the `LightpadController` control surface to **Lightpad Bridge**. The generic Track input for that virtual port is disabled in the tested setup.
- The helper uses protocol 18/A8 and forwards complete state, clock and UI records as checksummed SDK packets. Raw contact frames retain all sixteen channels and recording-view wakeups. Native recording scheduling does not wait for graphics.
- Modal entry asks Live for fresh, nonce-correlated permission. Recording, pending cleanup, held contacts or invalid project scope deny entry. Stale grants and new raw input cancel a request. Lift all pad contacts before using a short Mode release to return to the main program.
- Device discovery verifies the exact serial, standalone topology and matching CoreMIDI endpoint identity. USB is preferred for a new connection; a healthy Bluetooth connection is retained when USB is plugged in.
- One helper owns the SDK session. Singleton and SDK per-port locks reject competing owners. Do not run a separate CLI uploader, Dashboard or BLOCKS Code against the same device concurrently.
- Reconnection discards stale feedback, temporarily reloads the main program and handshakes again. Program changes preserve the Live Session object and accepted audio; they reset local pad contacts and reseed display state.

Opening the installed `.app` starts the helper with no Dock window. Status, lock and event files are under `~/Library/Application Support/Lightpad Bridge/`; the log is under `~/Library/Logs/Lightpad Bridge/`. Login autostart is not enabled. Closing the helper ends its temporary SDK session and leaves the saved startup program intact. Nothing in this build/stage flow writes firmware or saves a new startup program.

The checkpoint's bounded trial had no new bridge errors during Bluetooth performance. One retained stale-FIFO error arose during USB removal before recovery; an old error string can remain in status while `state` is `ready`. The current UI has positive USB and general Bluetooth feedback. Power-cycle, sleep/wake and long-duration workloads remain follow-up checks.
