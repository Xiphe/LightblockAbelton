# Validation

The functional baseline passed 842 offline checks and bounded physical trials on one macOS/Ableton/Lightpad setup. These results establish the cases below, not an endurance or sample-continuity guarantee.

## Current Audio controller

The direct-monitoring candidate passed user USB and Bluetooth checks in the cheap eight-group Set: patch selection, local names, mute/mixer controls and recording handoffs remained responsive. Native probes also verified seven-source aligned capture/cancel, two-source Accept/Next/Retry/Cancel/Clear, and saved group selections/monitoring ownership after reopening. Saved arm restoration is reconciled through validated lane metadata without adopting unmarked manual arms.

Current checks pass 162 controller tests, 740 legacy Live tests, 52 compiled performance-pad checks, 12 joined client/bridge/backend paths, 17 installer tests and seven native CTests. Main remains 7192/7200 bytes; patch uses 3883/7200. The installed helper reconnects over Bluetooth after restart.

Temple of Loops now uses its saved stock of 9/7/6/4/4/4/4/4 direct Audio lanes in R1 through R8, with automatic track and scene creation disabled. Trial cleanup removed 23 direct Audio clips; the final saved Set retains all 99 track IDs in the same order, with devices, mixer values, hardware inputs and stored source/group/monitor assignments preserved. Deleting R3's armed prepared lane in actual Live recovered through surviving lanes over Bluetooth with no display error or group failure. Undo restored the original stock, and all eight groups and seven sources returned to Ready without creating tracks or scenes. In that check, two controller maintenance `CustomAction` Undo entries preceded restoration of the deleted lane.

The heavy-Set trial exposed delayed handoff observation during synchronous Audio-track insertion and rejection when the next bundle was not ready. A timestamped capture reproduced both; the compiled client replay confirmed the presses occurred during the displayed fourth bar. Transitions now defer preparation once Native launch/finish is issued, with regression coverage for later resumption and long unissued count-ins. Manual lane deletion also exposed expired Native listener/monitoring handles; regression coverage verifies recovery from surviving lanes without launch replay. User recording-handoff retesting and long-duration operation remain pending. Preallocation capacity and actual Native allocation stalls are separate from boundary scheduling.

The responsive Audio baseline passed complete USB and Bluetooth trials in a cheap Set and Temple of Loops. Those covered mute chords, volume/pan/white controls, recording, Retry, Cancel, same-bar Accept-and-next and metronome switching. At 256 samples, the fixed eight-lane R3 test also completed without the insertion distortion.

The later two-microphone extension passed 95 host checks, 12 joined compiled-device/C++/Python path checks and 153 compiled-device checks before packaging. The user accepted the repaired dual-source USB countdown/Accept timing and manual deletion recovery in Temple of Loops. These are bounded functional observations, not optical latency measurements or a dedicated dual-source Bluetooth/endurance test.

Production packaging adds seven passing native CTests and 40 passing bridge tests. The latter include a real signed app launched in metadata-only mode, matching four-program defaults, configuration isolation, mismatch rejection and transactional installation/rollback tests. Device bytecode and musical command paths are unchanged by packaging. The installed login app then loaded the prepared Temple of Loops Set over USB and reported all eight groups and both sources ready. A subsequent helper restart while Live stayed open returned to main with unchanged source assignments and prepared reserves, zero reported transport errors and no recording-command replay. The latest app-only help-text update retained the same Live script. A full macOS reboot and a dedicated dual-source Bluetooth trial remain untested.

The older checks below describe the legacy controller's completed installation, template and physical trials. Its portable template and host-owned protocol are not the current Audio setup.

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
