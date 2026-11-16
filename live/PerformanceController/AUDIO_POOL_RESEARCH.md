# Preallocated Audio lanes

Research date: 2026-11-15. Research only; no runtime, Set or device changes.

## Evidence boundary

The user reports audible distortion when inserting or duplicating an Audio track in the running heavy Set, including manual insertion outside our controller. Increasing the setting to1024 removed it; adding a MIDI track did not reproduce it. These are user observations, not an instrumented engine diagnosis.

1024 is almost certainly the buffer size in samples, rather than the sample rate. Ableton explains that smaller buffers leave less processing time and can produce crackles/dropouts; larger buffers trade lower processing pressure for more monitoring latency. A short overload can occur even when average or whole-machine CPU looks acceptable: Live's CPU meter measures audio-buffer processing deadlines, and its Current view exposes peaks. These facts fit the observation but do not identify the exact internal cause. [Crackles and dropouts](https://help.ableton.com/hc/en-us/articles/209070329-How-to-avoid-crackles-and-audio-dropouts), [CPU meter](https://help.ableton.com/hc/en-us/articles/360019151379-Live-s-CPU-Meter).

No official guarantee was found that adding an Audio track, changing its internal input/output routing, moving it into a group, or arming it is inaudible under load. Calling the insertion problem an audio-graph rebuild is a plausible hypothesis, not a documented or measured diagnosis here. Interface input/output channel configuration is a different operation from a track's internal routing; evidence about changing the former cannot establish the latter's behavior.

## What a pool solves

A pool created before the performance removes track creation from takes and spare preparation. Fixed lanes inside each of the eight loop groups also avoid relocating tracks and changing their output destination during the performance.

A Session track plays one clip at a time. Four Audio lanes therefore support at most four concurrent Audio layers in that group, including the layer currently recording. They can hold many historical phrases in different rows. Moving to a fresh phrase can reuse the same lanes' empty slots without erasing earlier rows. A take recording two Audio sources consumes two lanes, so four lanes does not necessarily mean four recording actions. These capacity conclusions follow from Live's clip/track model and the proposed multi-source take workflow. [Session View](https://www.ableton.com/en/live-manual/12/session-view/).

The pool removes the observed insertion operation; it does not guarantee silence for recording start, arming, source routing changes or file writing. Those still need the actual heavy Set at the original intended buffer size.

## Fixed per-group lanes versus a shared reserve

| Layout | Benefit | Remaining cost or limitation |
| --- | --- | --- |
| Four fixed lanes in each group,32 total | No track allocation or output reassignment during performance | Four simultaneous Audio layers per group; uneven use strands capacity |
| Larger or uneven fixed counts, assigned before performance | Can give vocal groups more capacity without a runtime move | Capacity distribution is chosen in advance; unused-lane overhead needs measurement |
| Four per group plus16 shared spare tracks,48 total | Flexible capacity if borrowing is reliable | Borrowing must relocate a track or reroute its output; audible safety is unproven |
| Fixed global voice tracks routed dynamically to eight group buses | Avoids moving tracks in the UI | Still changes audio destinations, and physical Session layout no longer reflects group ownership |

Grouping is an audio-routing operation as well as organization: Live normally redirects grouped tracks to their containing group, while preserving existing custom destinations. Group output also determines which group processing a lane receives. Published LOM exposes `group_track` as read-only and does not list a `move_track` Song function. This documents the public Max for Live API, not every private Python Remote Script capability. It provides no supported atomic or inaudible move guarantee. [Mixing and Group Tracks](https://www.ableton.com/en/live-manual/12/mixing/), [Track API](https://docs.cycling74.com/apiref/lom/track/), [Song API](https://docs.cycling74.com/apiref/lom/song/).

A borrowed lane also carries its historical clips. Moving it from R1 to R2 moves those clips in the Session layout and changes their group processing; merely stopping the current phrase does not make that lane globally empty. A shared reserve therefore needs an explicit archive/ownership policy as well as an inaudible assignment mechanism. This follows from the track-based storage/layout and is an architectural implication, not an observed failure of a tested pool.

Do not assume a clever routing pool creates extra CPU headroom. Ableton describes dependencies and summing points in an audio path; group tracks are not inherently worse than equivalent summing buses. A prewired all-to-all matrix would add routing branches and complexity, rather than prove this problem solved. [Multi-core performance FAQ](https://help.ableton.com/hc/en-us/articles/209067649-Multi-core-performance-in-Ableton-Live-FAQ).

## Track labels and historical clip identity

A lane can be labeled for its next source while historical clips keep their own source names and colors. Live documents independent clip naming/color and says newly created clips inherit their track's color. The bulk Assign Track Color to Clips command deliberately rewrites clip colors, so the controller must not use it when retargeting a lane. Both Track and Clip expose separate writable name/color properties. [Clip View](https://www.ableton.com/en/live-manual/12/clip-view/), [Track API](https://docs.cycling74.com/apiref/lom/track/), [Clip API](https://docs.cycling74.com/apiref/lom/clip/).

Recommended model, subject to a Native check: snapshot the source label/color for the recording transaction, label the quiet destination before launch, and persist the new clip's own label/color when it exists. Reuse depends on an available compatible lane and an empty target slot, not the lane's former name. Never relabel older clips or replace their audio. Keep source-specific instruments/effects out of reusable Audio lanes; otherwise retargeting the lane's processing could also alter playback of historical clips.

An input change is distinct from monitoring. Monitor Off suppresses the incoming signal at the track output; it does not make internal routing changes a documented glitch-free operation. Keeping unused lanes Off avoids unwanted doubled monitoring but is not a zero-CPU promise. [Routing and monitoring](https://www.ableton.com/en/live-manual/12/routing-and-i-o/).

## Recommendation and small proof

Start with fixed per-group Audio lanes and no live creation. Choose counts with the user before implementation. Treat a shared reserve as an optional second experiment, not as a proven extension. A stopped transport or muted group does not establish an off-air setup phase: Constants and other monitored inputs can remain audible. Pool expansion therefore needs an explicitly chosen preparation phase, not merely `song.is_playing == False`.

At the intended original buffer setting, compare one change at a time while capturing the actual output and watching Current CPU/overload:

1. Control: repeat manual Audio insertion to reproduce the known interruption.
2. Record, Accept, Next, Retry and Cancel on already configured fixed lanes, with insertion disabled.
3. Reuse a lane on a later phrase; verify old clip names, colors, audio references and loops remain intact.
4. Switch only the quiet lane's already-enabled physical input channel, then test an internal source-track input. Do not change interface channel-enable configuration in this experiment.
5. Only if shared reserves are still valuable, test output reassignment and group relocation separately. A result for one is not evidence for the other.

No runtime fallback should silently insert a track once the pool is full. Pool exhaustion, allocation counts and any expansion must be agreed with the user. New scenes are another structural operation: these sources do not establish whether scene creation causes an audible artifact, so preallocate phrase rows for the proof or measure that operation separately. Likewise, the reported safe empty-MIDI insertion does not establish that inserting an instrument or changing its audio output is safe.

A separate documented lead exists: Live12.3 release notes mention faster new-track insertion when a default Audio/MIDI track is configured. This is not a promise of dropout-free insertion and is not sufficient reason to make a software upgrade the pool's dependency. [Live12 release notes,12.3 Performance](https://www.ableton.com/en/release-notes/live-12/).

## Bounded trial result

The user approved eight fixed Audio lanes on R3 in the original Temple of Loops Set and256samples, with512only as fallback. In the subsequent Bluetooth trial the user reported clean audio and responsive interaction. Private runtime telemetry confirmed the no-creation policy active; the Native trace contains nine recording starts, eight finishes and no Audio-track creation events. This supports removing insertion from this performance path, without proving the engine-internal cause. The captured final trace remains on phrase row0, so physical cross-phrase reuse is not independently established by that trace; policy tests cover it. Final per-group capacities and source-switch/rerouting audio safety remain open.


## Chosen operating policy

On2026-11-16 the user deferred the shared reserve and native Push-mover investigation. Start each loop group with four configured Audio lanes, retaining any existing extras. The Set itself holds that editable starting capacity; no startup scheduler forces a track count. Keep `ALLOW_AUDIO_TRACK_CREATION=True` so ordinary bounded background preparation can add a lane when no compatible local destination remains. The user explicitly accepts the known insertion artifact for this overflow. Reuse existing lanes on later phrase rows and preserve their previous clips. No shared reserve, track movement or archiving fallback is introduced.
