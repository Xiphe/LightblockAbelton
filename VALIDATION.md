# Validation

The functional baseline passed 842 offline checks and bounded physical trials on one macOS/Ableton/Lightpad setup. These results establish the cases below, not an endurance or sample-continuity guarantee.

## Offline checks

| Suite | Passing checks | Run from checkout root |
| --- | ---: | --- |
| Live host | 729 | `python3 -m unittest discover -s live/tests -v` |
| Package/install | 21 | `python3 -m unittest discover -s bridge/tests -p 'test_*.py' -v` |
| Native helper | 5 | `bridge/build.py` runs CTest while building |
| Compiled device | 87 | [Nine device test commands](lightpad/README.md#tests) |
| Total | 842 | |

Host checks use fake Native objects. Native CTests cover framing, transport policy, modal permission, short/long Mode recognition and host health. Device checks execute actual compiled Littlefoot bytecode with fake graphics/time/touch/MIDI. They comprise twelve canonical, twelve status, five differential idle, six failure-flash, fourteen recording, ten countdown, eleven marker-animation, fourteen metronome-layout and three metronome-swing checks. None opens Live, sends MIDI or writes a device program.

Coverage includes group discovery/reopen/reorder, mixed/manual playback, selected recording adoption, allocation, ownership races, safe boundary deferral, countdowns, partial touch batching, local failure feedback and shared output pacing. Synthetic workloads keep clocks fresh within one second and snapshots within two seconds at the four-frame rolling 90 ms budget. Those are simulated scheduling bounds, not measured Bluetooth latency.

## Physical checks

| Case | Observed result |
| --- | --- |
| Saved startup | Power cycle restored combined idle/status and touch interaction |
| USB | Two accepted layers in a folded group; user confirmed responsiveness |
| Track creation | New default Audio Tracks inside the intended group; existing tracks/clips preserved |
| Populated reopen | Loaded recorded Set automatically returned to Ready without configuration edits |
| Fresh template | Live browser created a new Untitled Set with eight marked groups, eight empty Audio Tracks and four returns, without media dependencies |
| Bluetooth | USB unplugged: record, Accept, another layer, mute, volume/pan and Mode open/close passed from the installed template |
| Status/recovery | Closing Live showed the Ableton icon; reopening recovered to the controller |
| Notifications | User confirmed visible diagnostic banners with app permission enabled and Sleep Focus disabled; mirroring notifications remained off |

Live requires at least one child track in a saved group. The template's empty tracks preserve that structure; new recordings still use new default tracks. Input/default routing was observed on the tested interface and remains Live configuration.

Builds record current source/include/compiler/SDK hashes in the app's `Contents/Resources/build-info.json`. Installations retain their own local receipts and replaced files for rollback. Raw captures, recordings and machine preferences are not repository dependencies.

## Remaining limits

The completed trials do not establish sample-accurate audible continuity, microphone/input quality, long-duration home streaming, sleep/wake, a complete crash/rollback rehearsal, nested-group insertion or every manual-edit/cancellation race in actual Live. Offline coverage does not replace those physical checks. The [architecture](ARCHITECTURE.md) describes the observation boundary for fresh clip normalization.
