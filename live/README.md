# Live controller setup

The current Audio controller uses [PerformanceController](PerformanceController/). It accepts semantic commands from the bridge and uses retained Native targets for immediate mixer and recording actions. Gestures, views and optimistic feedback belong to the device.

## Install and connect

Use the [matched Audio installer](../bridge/README.md#audio-performance-installation) to install the complete package as `~/Music/Ableton/User Library/Remote Scripts/LightpadAudioInputs`. Restart Live after replacing script files. Do not edit an active installed script.

In Link, Tempo & MIDI settings select `LightpadAudioInputs`, with Input and Output both `Lightpad Bridge`. Disable competing scripts on these ports and competing raw Lightpad routes. The helper and Live package must both use Audio performance mode.

Open a prepared Set whose absolute path appears in the installed `configuration.py`. `TARGET_SETS` is an explicit allowlist; an empty list falls back to `TARGET_SET`. Untitled and unlisted Sets are unavailable. After Save As or moving a Set, update the local path configuration through a staged install and restart Live. The installer does not change Live preferences or prepare the Set automatically.

## Prepared Set

- Give Group Tracks unique `[R1]` through `[R8]` prefixes. Unused numbers are allowed.
- Each usable group needs an existing rightmost direct subgroup named `[Remote]`.
- In each `[Remote]`, prepare `[Input 1]` and `[Input 2]` Audio helpers for the two configured sources.
- Place source tracks named `T-Bone` and `SM-58` inside a uniquely named `[Inputs]` hierarchy. Its subgroups are organizational. Effects remain on each source; helpers take its Post FX signal.
- Keep prepared music Audio tracks directly inside each remote group, before `[Remote]`. Four per group is the current starting inventory, not a hard limit. Two selected sources consume two lanes per take.

The existing Temple of Loops installation already has this structure. Its source hardware inputs are deliberately preserved. Both currently use hardware input 1; change the original source tracks in Live when connecting your actual microphones.

Only one group monitors live input at a time. Source selection or a horizontal swipe selects that group. Group mute, volume and effects apply to its monitored signal as well as its loops.

New recordings use the first available direct Audio track from left to right with an empty slot in the current phrase. Names and existing input routing do not give a later track priority. Effects and mixer settings stay intact; preparation adapts the chosen track's recording input and monitoring. Every subgroup's children stay outside this pool. Earlier clips keep their own source name and color. When `ALLOW_AUDIO_TRACK_CREATION = True`, maintenance may add tracks before `[Remote]`; insertion can interrupt audio under heavy load. No instruments or source effect racks are cloned.

## Local configuration

Pass a private Python configuration file to `bridge/install.py stage --configuration FILE`. The installer copies it into the staged Live package, validates its mode and records hashes. Local Set paths, bindings and diagnostics stay outside the public repository.

Required Audio settings include:

```python
TARGET_SETS = ('/absolute/path/Your Project/Your Set.als',)
AUDIO_SOURCES = (
    {'name': 'T-Bone', 'helper_name': '[Input 1]'},
    {'name': 'SM-58', 'helper_name': '[Input 2]'},
)
ALLOW_AUDIO_TRACK_CREATION = True
```

Start from [configuration.py](PerformanceController/configuration.py) and retain its other defaults. Set `TRACE_PATH` and `STATUS_PATH` to durable writable local files, or leave them `None`. Diagnostics must not point into a development experiment folder.

`PARAMETER_BINDINGS` maps zero-based group indices to four white controls. Volume and pan are independent. A send target is `{'kind': 'send', 'index': 0}`. A named device target is:

```python
{'kind': 'device', 'device_path': [0],
 'expected_device_name': 'Random Rev + Delay V2',
 'parameter_name': 'Reverb'}
```

For the Temple racks the first three controls target `Reverb`, `Delay` and `FX Vol`; the fourth remains send index 3. Names must match uniquely. Missing or replaced targets disable only their own control, rather than writing to an unrelated parameter.

## Recording and checks

Start transport before recording. Begin uses a prepared destination and the current playing-loop reference. Accept ends at the next safe bar, moving to the following bar if the imminent boundary cannot be reached safely. Accept-and-next finishes and starts its replacement at the same boundary. Cancel and Retry affect only the current unaccepted take. Clear stops only its group and advances to a free phrase row without deleting clips.

See the [adapter documentation](PerformanceController/README.md) for transaction ownership, partial failures and maintenance. Host tests open no Live or MIDI:

```sh
python3 -m unittest discover -s live/PerformanceController -p 'test_*.py' -v
```

The legacy `LightpadController` package and `templates/Lightpad Remote.als` use a different protocol and setup. Keep them paired with a legacy app; that template does not supply Audio-mode source helpers or configuration.
