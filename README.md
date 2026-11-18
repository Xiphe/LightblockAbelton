# Lightpad controller for Ableton Live

An eight-group Session View loop controller for a ROLI Lightpad. The pad handles gestures and optimistic feedback locally, a macOS bridge reconciles intent with Live, and Live owns the music. USB and Bluetooth use the same bridge.

The controller offers up to 25 original Audio sources and preconfigured MIDI instruments from `[Inputs]`, separately or together in one take. It reuses prepared recording tracks, keeps effects and instruments on their original source tracks, and controls each group's volume, pan and four assignable parameters. See [validation and limits](VALIDATION.md) for the physical checks and remaining limits.

## Install

```sh
git clone https://github.com/Xiphe/LightblockAbelton.git
cd LightblockAbelton
```

Follow the [bridge build and installation guide](bridge/README.md), choosing **Audio performance mode**. This installs a matched four-program app and the `PerformanceController` Live package under the name `LightpadAudioInputs`. No ROLI desktop app, account or Max for Live device is needed.

Audio mode needs an explicitly configured, prepared Set. Its [Live setup](live/README.md) describes the marked groups, source tracks, technical subgroups and local configuration. The older `templates/Lightpad Remote.als` belongs to the legacy controller; it does not prepare the Audio input workflow.

## Start a session

1. Let the installed login helper start Lightpad Bridge, or open the app manually.
2. Connect the pad by USB. For Bluetooth, connect it in Audio MIDI Setup > MIDI Studio > Bluetooth configuration if macOS has not already done so. The helper discovers OS MIDI connections; it does not pair devices.
3. In Live's control-surface settings, choose `LightpadAudioInputs`, with Input and Output both `Lightpad Bridge`. Keep competing controller scripts off these ports.
4. Open your configured, prepared Set. A new name or location must also be added to the local configuration before use.
5. Start Live's transport before recording. Mute and mixer controls also work while stopped.

The app contains all four device programs and their includes. Installed runtime files and diagnostics do not depend on a development checkout. A helper reconnect reseeds the current model without replaying recording commands.

## Controls

- Tap a group to mute/unmute, including empty groups. Touch several groups to batch changes on the last release.
- Swipe down on a group to record or add a layer.
- In recording: Cancel/Accept are above Retry/Accept-and-next. Accept ends at the next safe bar; Accept-and-next starts its replacement at that same boundary.
- Turn a down-swipe left or right into an L to stop that group and advance to a free row, preserving clips.
- Hold a group for volume, four assignable controls and horizontal pan.
- Short-release Mode to open patch. Hold a group and tap source tiles to toggle inputs. Short-release Mode again to return.
- Hold Mode for at least 400 ms to open the metronome. Short-release returns to main; holding from the metronome opens patch.
- Swipe horizontally on a group to move live monitoring to it. A blue indicator identifies the monitoring group.

Each take captures the current leftmost playing loop as its timing reference. Layers may have different lengths and end manually. Each selected source creates its own Audio or MIDI clip, controlled together as one take. A MIDI instrument belongs to one group at a time; its existing instance plays the recorded MIDI layers, with audio returning through that group. Existing clips retain their source names and colors when a track is reused in another phrase.

Prepared lanes avoid Audio-track insertion during ordinary recording. When they run out, the accepted overflow policy creates another track, which can cause a dropout in a heavily loaded Set. Increase prepared capacity before a performance when needed.

Idle uses the touchable tunnel and slideshow. Connection faults show a white Ableton logo, orange `[R]` or red `!`; tap a fault icon for a Mac explanation. Focus can hide notification banners. [The UX contract](lightpad/UX.md) records the gestures and display behavior.

## Repository

```text
live/PerformanceController/  Audio/MIDI Live adapter, preparation and host tests
bridge/                      macOS helper, packaging, installation and tests
lightpad/                    Device programs, UX contract and bytecode tests
templates/                   Legacy portable starter Set
ARCHITECTURE.md
VALIDATION.md
```

[Architecture](ARCHITECTURE.md) explains ownership and recovery. [Live adapter details](live/PerformanceController/README.md) cover scheduling and routing. The older `live/LightpadController` remains available with a legacy-mode app; its wire contract must not be mixed with Audio mode.

The original navigation-only Lightpad program is maintained on the [`0.x` branch](https://github.com/Xiphe/LightblockAbelton/tree/0.x).
