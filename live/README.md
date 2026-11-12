# Live controller

`LightpadController/` is a Python Remote Script loaded by Ableton Live. It discovers marked groups in the current Set and owns gestures, recording actions and mixer changes. [Lightpad Bridge](../bridge/README.md) handles the device connection and programs.

## Setup

Install the entire `LightpadController` directory through the [matched installer](../bridge/README.md#install). Its default target is `~/Music/Ableton/User Library/Remote Scripts/LightpadController`. Restart Live after installation.

In Link, Tempo & MIDI settings, choose `LightpadController`, with Input and Output both `Lightpad Bridge`. No per-Set MIDI mapping or Max for Live device is required. Disable competing direct BLOCKS control surfaces and raw Lightpad Track/Remote routes. Restart Live after removing an old script so its listeners are gone. Back up your own preferences before changing them; do not replace the whole preferences file while Live is open.

For a fresh project, install [the template](../templates/Lightpad%20Remote.als):

```sh
mkdir -p "$HOME/Music/Ableton/User Library/Templates"
cp -n 'templates/Lightpad Remote.als' "$HOME/Music/Ableton/User Library/Templates/Lightpad Remote.als"
```

Run these commands from the checkout root, or use File > Save As Template in Live. Use your configured User Library if it is elsewhere. Open **Templates > Lightpad Remote** to get a new Untitled Set, then save under your own name.

For another Set, give Group Tracks unique `[R1]` through `[R8]` prefixes. `[R1] Vocals` maps to tile 1 regardless of its suffix or position. Unused numbers are allowed. A duplicate number or a marker on a non-group makes that assignment unavailable; other valid groups remain usable. Keep at least one child track in each group because Live rejects childless saved groups.

Select the audio interface and recording source in Live. New takes create Audio Tracks with Live's defaults. They do not clone an input/instrument track or force a fixed input, monitoring mode or routing. The template's empty child tracks only preserve native group structure. Start transport before recording; Begin while stopped silently does nothing.

## State and recording

Live is authoritative. Saving, Save As, populated reopening, rename/reorder and manual clip launches require no manifest, saved-file hash or external ownership database. Mixed rows and unequal loop lengths are valid.

The leftmost eligible currently playing loop supplies timing, including muted and valid MIDI loops. Unfinished recordings cannot be references. Each take captures its reference's current length and phase; another take reads Live again. Without a playing reference, use the next safe bar and a free row after the last occupied row.

Accept finishes at the next safe bar. A late unissued action moves to a later legal boundary while keeping the request. Accept-and-next finishes first, then waits for the next take's captured loop wrap; safe coincident boundaries are supported. Retry discards only the current unaccepted take and captures a fresh reference. Actual clip flags and anchors confirm recording; a countdown reaching zero is not confirmation.

Fresh-phrase advance stops all playback in only that group and finds a free row after the highest currently playing row, or the last occupied row if nothing plays. Existing clips and other groups remain untouched. New tracks/scenes are created as needed. Retry may reuse its own emptied track, but arbitrary manual tracks are never recording destinations.

An already running manual recording can be adopted. The selected recording group wins, including a selected nonrecording child of that group; otherwise use the leftmost recording track. Reading state issues no recording writes. A reserved empty slot does not authorize deleting a manual clip that appears there. Already issued Native actions are observed rather than blindly replayed after an exception or reconnect.

## Configuration

Normal configuration in [configuration.py](LightpadController/configuration.py) uses `LIVE_STATE_MODE = True` with no manifest or proof opt-in. Keep those defaults for normal use.

The four white controls default to the first four sends. `PARAMETER_TARGETS` optionally gives four explicit targets per zero-based group number. For example, R1 can use two sends, a device parameter and another send:

```python
PARAMETER_TARGETS = {0: (
    {"kind": "send", "index": 0},
    {"kind": "send", "index": 1},
    {"kind": "device", "device_path": [0], "parameter_index": 3},
    {"kind": "send", "index": 3},
)}
```

Indices are zero-based. A nested path alternates device/chain/device indices, such as `[0, 1, 2]`. Check target names in your Set before performing. Explicit assignments retain Native identity for the loaded Set and disable only the affected control if its target is reordered or replaced. Default sends follow Live's send order. Volume, pan and mute remain independent. Assignment and input-selection screens are not implemented.

Change source configuration and use the matched installer with Live/helper closed; do not edit an active installed script. The app and script must retain matching protocol/health support.

## Code and tests

| Module | Responsibility |
| --- | --- |
| `live_state.py` | Marked-group discovery, actual clips, phase and Live bar grid |
| `live_recording.py` | Per-take timing, allocation, queued actions and guarded cleanup |
| `live_controller.py` | Snapshots, clocks, mixer bindings and recording adoption |
| `surface.py` / `device_ui.py` | MIDI callbacks, gestures, view reconciliation and local feedback |
| `output_pacing.py` / `health.py` | Shared graphics budget and independent helper health |

```sh
python3 -m unittest discover -s live/tests -v
```

The host suite uses fake Native objects and opens no Live or MIDI. See [architecture](../ARCHITECTURE.md) and [validation](../VALIDATION.md) for timing safeguards and the boundary between offline checks and physical evidence.
