# Lightpad controller for Ableton Live

An eight-group Session View loop controller for a ROLI Lightpad. Ableton owns the music; the pad shows playback, recording and mixer state over USB or Bluetooth. A small macOS helper connects the pad to a Python Remote Script inside Live.

USB/Bluetooth recording, layering, mixer controls, populated reopening, fresh templates, startup persistence and diagnostic notifications passed the bounded device trial. See [validation and limits](VALIDATION.md).

## Install

```sh
git clone https://github.com/Xiphe/LightblockAbelton.git
cd LightblockAbelton
```

Follow the [bridge build and installation guide](bridge/README.md). It covers the pinned compiler/SDK, signed app, matching Live script, saved pad startup and optional login agent. No ROLI desktop app, account, Max for Live device or private experiment folder is needed.

Copy the template into Live's User Library from the checkout root. `cp -n` preserves an existing template:

```sh
mkdir -p "$HOME/Music/Ableton/User Library/Templates"
cp -n 'templates/Lightpad Remote.als' "$HOME/Music/Ableton/User Library/Templates/Lightpad Remote.als"
```

Use your configured User Library if it is elsewhere. Alternatively open the repository Set in Live and choose File > Save As Template.

## Start a session

1. Run Lightpad Bridge, or let its installed login agent start it.
2. Connect the pad by USB. For Bluetooth, connect it in Audio MIDI Setup > MIDI Studio > Bluetooth configuration if macOS has not already done so. The helper discovers OS MIDI connections; it does not pair Bluetooth devices.
3. In Live's control-surface settings, choose `LightpadController`, with Input and Output both `Lightpad Bridge`. Disable competing direct BLOCKS control surfaces and raw Lightpad Track/Remote routes; restart Live after removing an old script.
4. Open **Templates > Lightpad Remote**, then save the Untitled Set as your own project. Choose the audio interface and default recording input in Live.
5. Start Live's transport before recording. A recording gesture while stopped does nothing; mute and mixer controls still work.

The template contains eight marked groups, one empty Audio Track per group and four returns, with no clips or external media references. Live requires a child track in every saved group. New takes create fresh default Audio Tracks and scenes as needed.

For an existing Set, prefix Group Track names with unique `[R1]` through `[R8]`, for example `[R1] Vocals`. The number assigns a pad tile; suffixes, track positions and unrelated tracks can change. Save, copy and reopen normally. There are no Set-path manifests or fingerprints. See [Live configuration](live/README.md).

## Controls

- Tap a group to mute/unmute. Touch several groups to batch changes on the last release.
- Swipe down on a group to record or add a layer.
- In recording: Cancel/Accept are above Retry/Accept-and-next.
- Turn a down-swipe left or right into an L to stop that group and advance to a free row, preserving clips.
- Hold a group for volume, four assignable controls and horizontal pan.
- Short-release the side Mode button for metronome, meter and tempo controls. Holding it is reserved for input selection and currently changes no screen.

A new take uses the leftmost eligible playing loop as its timing reference, or the next safe bar when none plays. Layers can have different lengths and end manually. The pad follows actual Live state, including manually launched clips.

The idle screen is a touchable tunnel with hidden messages. Connection faults show a white Ableton logo, orange `[R]` or red `!`; tap a fault icon for a Mac explanation. Focus can hide notification banners. [The UX contract](lightpad/UX.md) records the full gestures and display behavior.

## Repository

```text
live/          Live Remote Script and host tests
bridge/        macOS helper, build/package/install tools and tests
lightpad/      Device programs, UX contract and compiled-bytecode tests
templates/     Portable Live starter Set
ARCHITECTURE.md
VALIDATION.md
```

[Architecture](ARCHITECTURE.md) explains ownership, recovery and the split device programs. Tests run without Live or hardware:

```sh
python3 -m unittest discover -s live/tests -v
python3 -m unittest discover -s bridge/tests -p 'test_*.py' -v
```

[Device checks](lightpad/README.md#tests) also need the source-built `blocksctl` compiler. Audible capture/continuity, long-duration use and sleep/wake remain outside the completed physical trials.

The original navigation-only Lightpad program is maintained on the [`0.x` branch](https://github.com/Xiphe/LightblockAbelton/tree/0.x).
