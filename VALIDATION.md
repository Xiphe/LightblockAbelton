# Validation

This file describes the accepted Audio/MIDI controller baseline. Detailed intermediate trials remain in Git history. Offline checks establish the specified cases; the physical checks below are bounded observations on one macOS/Ableton/Lightpad setup.

## Offline coverage

| Area | Passing checks | What they establish |
| --- | ---: | --- |
| Current Live adapter | 223 | Recording transactions, retained mixer targets, source routing, exclusive MIDI ownership, saved metadata, deletion recovery and bounded preparation |
| Compiled device clients | 165 | Actual Littlefoot bytecode, gestures, optimistic state, delayed replies, modal controls and memory limits |
| Joined device/bridge/host paths | 16 | Compiled clients and the actual C++ reconciler against a Python backend with fake Native endpoints |
| Native bridge | 7 CTests | Framing, transport policy, semantic reconciliation, modal switching and health handling |

The [Live adapter](live/PerformanceController/README.md), [device](lightpad/PERFORMANCE.md) and [bridge](bridge/README.md#tests) document the test commands. Fake Native objects, clocks and audio endpoints do not measure hardware latency or audible continuity.

Main uses 7,190 of 7,200 bytes. Patch uses 5,084 and metronome 3,558; each is a separate program with its own budget. Main's ten-byte margin is not capacity for an unmeasured feature addition.

## Physical acceptance

- Audio: USB and Bluetooth trials passed in a small Set and Temple of Loops. They covered patch selection, source names, monitoring, mute chords, volume/pan/assignable controls, recording, Retry, Cancel, same-boundary Accept-and-next and metronome switching.
- Audio routing: Native trials verified seven-source aligned capture/cancel and two-source handoffs. Prepared recording lanes carry monitoring directly through their R-group; original source effects stay in place. Brief double monitoring around a handoff is accepted.
- MIDI routing: actual Live trials verified direct MIDI capture and playback through the original instrument, a shared Audio receiver per group, stored CC64 envelopes, exclusive assignment, Clear/reassignment and explicit reacquisition. Physical LPK25/Drift USB recording produced independently playing MIDI clips with recorded notes. The user also accepted Horns/Kontakt recording in Temple of Loops over USB at 512 samples.
- Overflow: Native track insertion can interrupt audio and block Live control in a heavy Set. The user accepted that fallback. The yellow Busy phase now covers the complete next bundle, including routing, and the user accepted Next/Cancel after it clears over Bluetooth. A fixed prepared Audio pool was clean at 256 samples.
- Preparation performance: the user accepted the shared Audio/MIDI routing correction and reported a slight improvement. Three measured MIDI insertions took 1.59–1.84 seconds inside Native creation, followed by a 2.25–3.06ms routing batch and 0.22–0.25ms appearance update. A further 0.79–0.86-second interval before routing remains unattributed. This was not a controlled before/after benchmark and included no new Audio-track insertion. Further optimization is deferred by the user.
- Manual edits and persistence: deleting prepared Audio lanes recovered through surviving stock without a global failure. Source assignments and monitoring ownership survived an actual Set save/reopen. Reopening Temple of Loops after the latest host installation restored all eight groups and the saved MIDI assignment.
- Startup and recovery: the saved idle/status program survived a power cycle; helper reconnects reseeded state without replaying recording commands. Closing Live displayed its status icon and reopening recovered. The installed helper starts through its login agent. Diagnostic notifications were accepted after enabling permission and disabling Sleep Focus.

## Installation and rollback

The signed app packages all four device programs, includes, supplier notices and source/compiler/SDK hashes. The matched installer validates configuration and mode, records installed hashes and retains replaced app/script/login files for rollback. The runtime uses installed paths rather than a development checkout. The current host-only preparation update retained the exact bridge app, device programs and private configuration.

Temple of Loops is a private, explicitly configured Set. The legacy portable template does not prepare this Audio/MIDI setup. Set copies or renamed paths need an allowlist update as described in [Live setup](live/README.md).

## Before the first production session

The agreed four Constants mixer targets are still to be added to this baseline. Then prepare the private performance Set and run a cold-start check plus a sustained rehearsal with the actual interface and sources. Physical MPE, audible sustain/CC behavior, the other instrument plug-ins, long-duration streaming, sleep/wake and a full crash/rollback rehearsal are not established by the completed checks.
