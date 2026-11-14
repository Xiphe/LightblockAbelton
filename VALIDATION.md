# Validation

The current input-patching build passes 933 offline checks. The established looping baseline also passed bounded physical trials on one macOS/Ableton/Lightpad setup. The new routing checks below are separate from those user trials; neither establishes endurance or sample continuity.

## Offline checks

| Suite | Passing checks | Run from checkout root |
| --- | ---: | --- |
| Live host | 803 | `python3 -m unittest discover -s live/tests -v` |
| Package/install | 21 | `python3 -m unittest discover -s bridge/tests -p 'test_*.py' -v` |
| Native helper | 5 | `bridge/build.py` runs CTest while building |
| Compiled device | 104 | [Device test commands](lightpad/README.md#tests) |
| Total | 933 | |

Host checks use fake Native objects. Three additional host checks replay the actual publisher and output pacer into compiled patch bytecode. Native CTests cover framing, transport policy, modal permission, short/long Mode recognition and host health. Device checks execute compiled Littlefoot bytecode with fake graphics, time, touch and MIDI. They cover the overview, Constants, patch catalog/names, faders, recording/countdown markers, metronome and status/idle. None of these offline checks opens Live, sends MIDI or writes a device program.

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

## Native input-routing checks

A separate lightweight Live Set with stock Drift verified the actual offered routing objects and source/receiver topology. A source routes its audio to an owned Audio Track inside the remote group at Track In; that receiver uses No Input, Monitor In and parent-group output. MIDI layers inherit the prepared instrument's physical MIDI input and target its direct channel-one instrument endpoint. A computer-keyboard signal reached the instrument, receiver and remote group with Main at its minimum. A Post FX MIDI tap with the prepared source Monitor Off did not forward that signal and is deliberately not used.

The final normal engine was exercised through a temporary guarded, fixed-operation wrapper in the same small Set. Audio and MIDI assignment completed after Native routing settled. A coordinated take started at beat 12 and finished at beat 76, with both clips retaining 64-beat loops. Its MIDI clip contained a computer-keyboard note; replay produced matching instrument/receiver meter activity while another group was monitored and Main stayed at zero. A busy instrument was refused in the second group; Clear stopped playback without deleting either clip, then an idle reassignment removed only the old MIDI patch. Aborting another queued take left both destinations unarmed with no new clips. The test wrapper was removed, all playback stopped and Main restored before the normal app/script installation.

Native routing tests also established that dependent channel choices can remain stale within the callback that writes a routing type. Offline regressions reproduce this delay and verify display-only preparation, no early arm/fire, safe boundary deferral, one completion and no repeated add/remove intent.

 These are Native routing and scheduling observations. They do not establish physical microphone capture, MPE/sustain behavior, physical patch-screen usability or new-feature Bluetooth latency. The existing user's instrument input devices were not all connected during this work.

## Remaining limits

The completed trials do not establish sample-accurate audible continuity, microphone/input quality, long-duration home streaming, sleep/wake, a complete crash/rollback rehearsal, nested-group insertion or every manual-edit/cancellation race in actual Live. Offline coverage does not replace those physical checks. The [architecture](ARCHITECTURE.md) describes the observation boundary for fresh clip normalization.
