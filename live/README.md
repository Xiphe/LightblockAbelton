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

Select the audio interface in Live. Sets without an `[Inputs]` group retain default Audio Track recording. The template's empty child tracks preserve native group structure. Start transport before recording; Begin while stopped silently does nothing.

## Inputs, patches and Constants

Put prepared source tracks under one Group Track named `[Inputs]` (a suffix is allowed). Its descendant leaf tracks appear in Live order, up to 25 sources; organizational subgroups do not appear as sources. Their parent groups are organizational: processing on those parents is bypassed. Put processing intended for capture on each source leaf instead.

Use a short Mode click to open input selection. A short click returns to the performance view; holding Mode for at least 400 ms opens the metronome. Assign sources to a remote group there, then return to the performance view. A Set with `[Inputs]` requires an assigned, available source before recording. Duplicate `[Inputs]` markers fail closed instead of falling back to an external audio input. Source names must be unique because Native routing choices are matched to the actual offered track names.

Audio sources are recorded after their leaf devices. MIDI sources retain their prepared instrument and physical controller input settings. The controller inherits that MIDI input on its own empty recording/monitor tracks and routes them to channel one of the same prepared instrument; it does not clone the instrument. Top-level MIDI effects before that instrument are currently unsupported and make the source unavailable. Processing inside the addressed instrument rack and audio effects after the instrument remain on the prepared source.

Each selected source gets a new Audio or MIDI track for each take. Actual empty `[Patch]` tracks store assignments; `[Return] Rn …` tracks carry source audio into the remote group. These are routing infrastructure, not take destinations: leave them empty. Return tracks use No Input, remain Monitor In, and output to their parent group. Their monitoring must remain active for accepted MIDI layers to keep sounding when you monitor a different group.

The controller manages source output routing and monitoring. It preserves source physical inputs, devices, presets and macros. Only one group is monitored at a time; choosing Monitor again keeps it active. Group mute also mutes that group's monitored sound. A MIDI instrument already used by playing layers belongs to that group even while muted. Clear stops its playback and frees the instrument for another group while retaining the current patch for an immediate fresh phrase. Assigning that idle MIDI instrument to a different group removes it from the previous patch; other sources stay assigned. Audio sources may stay assigned to several groups. Playing or muted MIDI layers block a transfer.

Patches and monitoring are rediscovered from actual Live tracks and routing when the Set reopens. Changes to a source's physical MIDI input are inherited on the next monitor/record action. Manually launching an old MIDI phrase after another group has taken its instrument can leave that instrument's audio routed to the newer group; automatic ownership rerouting for this outside-controller case is not yet implemented.

For four always-running mixer channels, add a unique `Constants` or `[Constants]` Group Track. Its first four direct children, in Live order, become controls 9–12 (internal indices 8–11). A child may itself be a group, so a Drums group stays intact. These controls provide mute and mixer parameters, without recording or phrase advance.

## State and recording

Live is authoritative. Saving, Save As, populated reopening, rename/reorder and manual clip launches require no manifest, saved-file hash or external ownership database. Mixed rows and unequal loop lengths are valid.

The leftmost eligible currently playing loop supplies timing, including muted and valid MIDI loops. Unfinished recordings cannot be references. Each take captures its reference's current length and phase; another take reads Live again. Without a playing reference, use the next safe bar and a free row after the last occupied row.

Accept finishes at the next safe bar. A late unissued action moves to a later legal boundary while keeping the request. Accept-and-next finishes first, then waits for the next take's captured loop wrap; safe coincident boundaries are supported. Retry discards only the current unaccepted take and captures a fresh reference. Actual clip flags and anchors confirm every destination's recording; a countdown reaching zero is not confirmation. Multi-source takes share a requested boundary, with Native observations confirming each track independently. Ambiguous Native failures are never automatically replayed.

Fresh-phrase advance stops all playback in only that group and finds a free row after the highest currently playing row, or the last occupied row if nothing plays. Existing clips and other groups remain untouched. New tracks/scenes are created as needed. Retry may reuse its own emptied track, but arbitrary manual tracks are never recording destinations.

An already running manual recording can be adopted. The selected recording group wins, including a selected nonrecording child of that group; otherwise use the leftmost recording track. Reading state issues no recording writes. A reserved empty slot does not authorize deleting a manual clip that appears there. Already issued Native actions are observed rather than blindly replayed after an exception or reconnect.

## Configuration

Normal configuration in [configuration.py](LightpadController/configuration.py) uses `LIVE_STATE_MODE = True` with no manifest or proof opt-in. Keep those defaults for normal use.

The four white controls default to the first four sends; source `PARAMETER_TARGETS` remains `{}`. This is application-wide script configuration, keyed by zero-based group number. This example changes only R1's first three controls and keeps its fourth on send D. The racks must occupy device positions 0 and 1 with these exact names:

```python
PARAMETER_TARGETS = {0: (
    {"kind": "device", "device_path": [0], "expected_device_name": "Random Rev + Delay V2",
     "parameter_name": "Reverb"},
    {"kind": "device", "device_path": [0], "expected_device_name": "Random Rev + Delay V2",
     "parameter_name": "Delay"},
    {"kind": "device", "device_path": [1], "expected_device_name": "Stereo Random Filter",
     "parameter_name": "FX Vol"},
    {"kind": "send", "index": 3},
)}
```

Names are case-sensitive, and a named parameter must match uniquely. A missing/ambiguous macro or mismatched rack disables only that control; volume, pan, mute and other controls stay usable. Indexed targets using `parameter_index` remain valid, with optional `expected_parameter_name` and `expected_device_name` guards. Indices are zero-based; nested device paths alternate device/chain/device indices. Rack reordering or replacement after binding requires an explicit rebind; name matching does not silently adopt another rack.

Apply source configuration through the matched installer with Live/helper closed, then restart Live to reload the script. Configuration is shared across Sets, so other Sets without those named racks leave the affected bars unavailable. Do not edit an active installed script. Parameter assignment has no device editor; input selection uses the separate patch screen described above.

## Code and tests

| Module | Responsibility |
| --- | --- |
| `live_state.py` | Marked-group discovery, actual clips, phase and Live bar grid |
| `live_recording.py` | Coordinated per-source take timing, allocation, queued actions and guarded cleanup |
| `patching.py` | Source catalog, Native routing, persistent patch discovery and monitoring |
| `live_controller.py` | Snapshots, clocks, mixer bindings and recording adoption |
| `surface.py` / `device_ui.py` | MIDI callbacks, gestures, view reconciliation and local feedback |
| `output_pacing.py` / `health.py` | Shared graphics budget and independent helper health |

```sh
python3 -m unittest discover -s live/tests -v
```

The host suite uses fake Native objects and opens no Live or MIDI. See [architecture](../ARCHITECTURE.md) and [validation](../VALIDATION.md) for timing safeguards and the boundary between offline checks and physical evidence.
