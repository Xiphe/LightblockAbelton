# Lightpad controller for Ableton Live

An eight-group Session View loop controller that can be operated from a ROLI Lightpad without watching the computer. Ableton owns the clips, recording and mixer state. The Lightpad draws and animates locally, and a small macOS helper carries feedback over USB or Bluetooth.

The working checkpoint `35e2a8d` was tested on 8 November 2026 with Live 12.2.7 over USB and Bluetooth. The current tree adds the approved full-screen metronome trial. Automated checks pass. Hannes accepted the digit/raster refinement and double-tap reset over USB and reported that the current version works well over Bluetooth on 9 November 2026. Recording still requires local Live preparation.

## Current controls

- Tap groups to mute or unmute, including multitouch batches.
- Swipe down to record a take or layer. Use the recording view to Cancel, Accept, Retry, or Accept and record next.
- Use a down-and-sideways gesture to advance one group to a free row without deleting earlier recordings.
- Hold a group to access volume, four assignable controls and horizontal pan.
- Short-release the physical Mode button to switch between the main controller and a separate metronome program.
- Hold Mode for at least 400 ms to emit a reserved input-selection request. Input selection is a logged placeholder, with no routing change or selector UI yet.

The main screen's bottom metronome row is visual only. The full-screen metronome has blue and purple meter fields separated by a black center column. White digits always show Live's actual value, including while dragging. Holding a field shows the reachable-detent raster beneath its digits: nonselected lines use the field's resting background color, the selected line is bright, and the remaining background is black. Below them, Click has a green/red switch with a white left/right knob, and Tap Tempo is a plain monochrome pad. Lift pad contacts and short-release Mode to return; there is no on-screen Close button. Other approved follow-ups remain recorded in the [UX specification](docs/ux/device-prototype-spec.md).

Double-tapping either meter field requests a reset of that value to 4. The fields recognize taps independently, so two-finger double-taps can request 4/4 through the normal serialized changes. Single taps do nothing and dragging has no added wait. Hannes confirmed the per-field and simultaneous resets, plus unchanged dragging, over USB on 9 November 2026. Device headroom is unchanged.

## Source and build entry points

| Component | Location |
| --- | --- |
| Main and metronome device programs | [Lightpad sources](prototypes/controller/lightpad/) |
| Ableton Remote Script and offline host tests | [Live controller](prototypes/controller/live/README.md) |
| Native helper, build and app staging | [Lightpad Bridge](tools/lightpad-bridge/README.md) |
| Compiled-device checks | [Current-device tests](verification-tools/test_current_device.py) |
| Frozen checkpoint provenance and measured results | [Working baseline](docs/integration/working-baseline.md) |

Build and test from these maintained paths. Ignored `verification/` experiments, private Live Sets, installed applications and raw recordings are not build or test dependencies. The helper's SDK dependency is external and pinned by the build instructions. No vendor SDK or compiled application is included here.

Live recording requires a separately prepared local Set and explicit configuration. The committed Remote Script defaults leave recording disabled until that setup has been validated. See the Live controller instructions before installing or enabling it.

The [baseline hashes](prototypes/controller/live/baseline-sha256.json) identify the frozen checkpoint, not every later working-tree revision. The current metronome source and host routing must be deployed together even though they retain protocol 18/A8. A subsequent graphics-only fix changes phase sampling in both pad programs; it leaves host, native helper and gesture behavior unchanged.

Run the offline Python and compiled-device checks from the checkout root:

```sh
python3 -m unittest discover -s prototypes/controller/live/tests -v
python3 verification-tools/test_current_device.py
```

The device checks require the source-built `blocksctl` compiler on `PATH`. To build and test the native helper and stage a new app, supply the pinned SDK, a new build/output location, and your own device serial:

```sh
python3 tools/lightpad-bridge/build.py \
  --sdk /path/to/blocksctl/vendor/sdk \
  --build-dir /tmp/lightpad-baseline-build \
  --device YOUR_DEVICE_SERIAL \
  --output '/tmp/Lightpad Bridge.app'
```

Replace the SDK path and serial before running. The staging command does not install or start the app. The helper instructions describe dependency validation and local toolchain requirements.

## Device memory and switching

| Program | Code plus heap | Free of 7,200 bytes |
| --- | ---: | ---: |
| Main | 6,283 | 917 |
| Metronome | 5,331 | 1,869 |

Both programs now reuse one clock-phase sample per repaint for bar tracking and swing drawing. This fixes a deterministically reproduced opposite-edge jump in odd meter, and offline 4/4 endpoint and clock-refresh continuity checks pass. After the fix, the user's USB flash check reported "looks good". On 9 November 2026, Hannes also reported that the current version works well over Bluetooth.

Each program has its own budget because only one is loaded at a time. Switching uploads a temporary program and restores feedback from Live. In the frozen checkpoint's Bluetooth trial, entering the smaller metronome program took about 2.2–2.5 seconds and returning took about 3.4–3.5 seconds. The user accepted that pause for these occasional settings screens. The current version has a positive user-reported Bluetooth check; new numerical switch timings were not measured.

The checkpoint's USB and Bluetooth trials passed modal controls, short/long button recognition, mute/mixer control, recording and layering after returning from the modal. The new layout, ruler and reset received positive USB feedback, followed by a positive general Bluetooth report on 9 November 2026. This evidence does not establish audio quality, sample-accurate onset, long-duration reliability or sleep/wake recovery. Temporary uploads preserve the device's saved startup program; building or staging the app does not install, launch, save a default or update firmware.

A retained stress regression is marked as an expected failure: sustained urgent meter traffic can starve group-clock feedback beyond its freshness limit. Its strict assertion and measured workload remain visible in the host suite and [baseline limitations](docs/integration/working-baseline.md). The cleanup preserves the tested pacing behavior.
