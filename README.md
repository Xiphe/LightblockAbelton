# Lightpad controller for Ableton Live

An eight-group Session View loop controller that can be operated from a ROLI Lightpad without watching the computer. Ableton owns the clips, recording and mixer state. The Lightpad draws and animates locally, and a small macOS helper carries feedback over USB or Bluetooth.

This is the working prototype baseline tested on 8 November 2026 with Live 12.2.7. It is a checkpoint for further development, with local Live preparation required for recording.

## Current controls

- Tap groups to mute or unmute, including multitouch batches.
- Swipe down to record a take or layer. Use the recording view to Cancel, Accept, Retry, or Accept and record next.
- Use a down-and-sideways gesture to advance one group to a free row without deleting earlier recordings.
- Hold a group to access volume, four assignable controls and horizontal pan.
- Short-release the physical Mode button to switch between the main controller and a separate metronome program.
- Hold Mode for at least 400 ms to emit a reserved input-selection request. Input selection is a logged placeholder, with no routing change or selector UI yet.

The main screen's bottom metronome row is visual only. The metronome program currently provides meter editing, audible Click, Tap Tempo and Close. The approved full-screen redesign and other follow-ups are recorded in the [UX specification](docs/ux/device-prototype-spec.md); they are not implemented in this baseline.

## Source and build entry points

| Component | Location |
| --- | --- |
| Main and metronome device programs | [Lightpad sources](prototypes/controller/lightpad/) |
| Ableton Remote Script and offline host tests | [Live controller](prototypes/controller/live/README.md) |
| Native helper, build and app staging | [Lightpad Bridge](tools/lightpad-bridge/README.md) |
| Compiled-device checks | [Current-device tests](verification-tools/test_current_device.py) |
| Scope, provenance and measured results | [Working baseline](docs/integration/working-baseline.md) |

Build and test from these maintained paths. Ignored `verification/` experiments, private Live Sets, installed applications and raw recordings are not build or test dependencies. The helper's SDK dependency is external and pinned by the build instructions. No vendor SDK or compiled application is included here.

Live recording requires a separately prepared local Set and explicit configuration. The committed Remote Script defaults leave recording disabled until that setup has been validated. See the Live controller instructions before installing or enabling it.

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
| Main | 6,279 | 921 |
| Metronome | 4,319 | 2,881 |

Each program has its own budget because only one is loaded at a time. Switching uploads a temporary program and restores feedback from Live. In the bounded Bluetooth trial, entering the metronome took about 2.2–2.5 seconds and returning took about 3.4–3.5 seconds. The user accepted that pause for these occasional settings screens. This is separate from steady-state touch feedback.

USB and Bluetooth trials passed modal controls, short/long button recognition, mute/mixer control, recording and layering after returning from the modal. This evidence does not establish audio quality, sample-accurate onset, long-duration reliability or sleep/wake recovery. Temporary uploads preserve the device's saved startup program; building or staging the app does not install, launch, save a default or update firmware.

A retained stress regression is marked as an expected failure: sustained urgent meter traffic can starve group-clock feedback beyond its freshness limit. Its strict assertion and measured workload remain visible in the host suite and [baseline limitations](docs/integration/working-baseline.md). The cleanup preserves the tested pacing behavior.
