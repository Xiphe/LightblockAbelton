# Lightpad controller for Ableton Live

An eight-group Session View loop controller for a ROLI Lightpad. Ableton owns clips, recording and mixer state; the pad displays that state and sends gestures. Lightpad Bridge connects them over USB or Bluetooth and loads separate main, metronome and status programs.

The installed baseline passed USB/Bluetooth recording and controls, populated reopening, fresh-template loading, startup persistence and visible diagnostic notifications. The [validation summary](docs/integration/validation-summary.md) records the checks and their limits.

## Set up a fresh checkout

```sh
git clone https://github.com/Xiphe/LightblockAbelton.git
cd LightblockAbelton
```

Follow [build, startup installation and app installation](tools/lightpad-bridge/README.md#build-and-stage-offline), then install the template below. This is a source repository; the guide builds the signed helper and installs the matching Live script. It does not need local experiment folders or ROLI desktop apps. The [validation summary](docs/integration/validation-summary.md) records the tested baseline and remaining limits.

The original Lightpad navigation program is maintained separately on the [`0.x` branch](https://github.com/Xiphe/LightblockAbelton/tree/0.x). Its third-party Max for Live companion is not distributed here.

## Using a supported Set

In Live's browser, open **Templates > Lightpad Remote** for a fresh Untitled Set, then save it as your own project. The installed template is `~/Music/Ableton/User Library/Templates/Lightpad Remote.als`; the portable source is [Lightpad Remote.als](templates/Lightpad%20Remote.als). It has eight marked groups, one empty seed Audio Track per group, four returns and no clips or media references. Live requires a child track in every saved group; recordings still create fresh default tracks.

For another Set, prefix Group Track names with `[R1]` through `[R8]`, for example `[R1] Vocals`. The number assigns a tile. Other name text, track order and unrelated tracks can change. Unused tile numbers are allowed.

The normal Live script follows the loaded Set directly, including populated reopening and Save As. It no longer needs an `.als` manifest, a saved-file fingerprint or a path into private experiments. New takes create default Audio Tracks and scenes as needed. Configure the intended audio interface/default recording source in Live; device-side input selection comes later.

Select `LightpadController` in Live's control-surface settings with Input and Output both `Lightpad Bridge`. Run the bridge and connect the pad. If macOS has not connected Bluetooth MIDI, open Audio MIDI Setup, show MIDI Studio, open Bluetooth configuration and connect the Lightpad there. The helper discovers that connection; it does not establish OS Bluetooth pairing. The installer supports the agreed login startup, as an explicit installation step. Start Live's transport before recording; a recording gesture while stopped is a silent no-op.

Detailed instructions are in [the Live controller README](prototypes/controller/live/README.md) and [bridge installation guide](tools/lightpad-bridge/README.md).

## Install the template on another Mac

From the controller checkout, copy the portable Set into Live's User Library. This keeps an existing same-named template intact:

```sh
mkdir -p "$HOME/Music/Ableton/User Library/Templates"
cp -n 'templates/Lightpad Remote.als' "$HOME/Music/Ableton/User Library/Templates/Lightpad Remote.als"
```

If your User Library is elsewhere, use that folder instead. Alternatively open the repository Set in Live and choose File > Save As Template. Then use Templates > Lightpad Remote to create each new Set and save under your own project name. The app installer handles the helper/script/login files; it does not install templates or choose audio preferences.

## Controls

- Tap groups to mute/unmute, including multitouch batches.
- Swipe down to record. The recording screen has Cancel/Accept above Retry/Accept-and-next.
- Use a down-and-sideways gesture to stop only that group and advance to a free row, preserving clips.
- Hold a group for volume, four assignable controls and horizontal pan.
- Short-release Mode for the full-screen metronome. A 400 ms hold remains an input-selection placeholder.

The leftmost eligible playing loop supplies timing for each new take. That take keeps its timing reference until finished; the next take reads Live again. Mixed rows, different loop lengths, muted loops and manual launches remain valid. Existing recordings are preserved, and actual Native recording state is observed rather than inferred from countdown animation.

The idle screen is a touchable elastic tunnel with hidden messages. Connection faults use a white Ableton logo, orange `[R]` or red `!`; tap a fault icon for a Mac explanation. Idle touches never request diagnostics.

The [UX specification](docs/ux/device-prototype-spec.md) records the detailed gestures, metronome raster, recording dots and readiness behavior.

## Source and offline checks

| Component | Entry point |
| --- | --- |
| Live engine, gestures and host tests | [Live controller](prototypes/controller/live/README.md) |
| Native helper, package and reversible installation | [Lightpad Bridge](tools/lightpad-bridge/README.md) |
| Main, metronome and status renderers | [Device sources](prototypes/controller/lightpad/) |
| Compiled-bytecode verification | [Current-device checks](verification-tools/README-current-device.md) |
| Release contract and Native checks still required | [Stable release](docs/integration/stable-release.md) |

```sh
python3 -m unittest discover -s prototypes/controller/live/tests -v
python3 verification-tools/test_current_device.py
python3 verification-tools/test_status_device.py
```

The device checks require the source-built `blocksctl` compiler. The native build additionally requires the pinned external SDK, CMake and Apple Command Line Tools. [Build/staging](tools/lightpad-bridge/README.md) creates a new app without launching, installing or writing to the pad. Ignored `verification/` experiments, installed applications and recordings are not build dependencies.

## Program memory

| Program | Code plus heap | Free of 7,200 bytes |
| --- | ---: | ---: |
| Main | 7,106 | 94 |
| Metronome | 5,331 | 1,869 |
| Status + idle | 5,320 | 1,880 |

Only one program runs at a time. Separate programs preserve local animation and immediate touch feedback while avoiding the combined-program memory limit. Numerical switch timings from older trials do not establish current candidate latency.

## Earlier checkpoints

The [working baseline](docs/integration/working-baseline.md) records bounded USB/Bluetooth trials for `35e2a8d`. Metronome checkpoint `1d31013` and subsequent recording-dot trials have their own dated observations. [Baseline hashes](prototypes/controller/live/baseline-sha256.json) identify the frozen checkpoint, not this working tree.

Audible capture/continuity, long-duration use and sleep/wake remain outside the completed trials. See the [validation limits](docs/integration/validation-summary.md#limits) before relying on those cases.
