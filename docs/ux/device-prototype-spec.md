# Lightpad device prototype specification

Confirmed on 7 November 2026 after Hannes's final shared-understanding check. Hannes authorized building and trying this temporary prototype on the device.

This is a temporary on-device prototype. It must let Hannes judge touch targets, gestures, animation and multitouch on the actual Lightpad. A successful functional test does not approve a new UX choice.

### Current full-screen metronome trial, 8 November 2026

The main screen has only a visual metronome indicator. A short release of the physical Mode button toggles a separate metronome program after pad contacts are lifted. Holding Mode for at least 400 ms emits one input-selection request; release after a hold does not also trigger a short action. Input selection is currently a logged placeholder.

The current source implements the approved full-screen meter layout, slower detents and local ruler intent described below. Hannes accepted the visible-digit/raster refinement with "top" over USB. Hannes also confirmed the per-field and simultaneous double-tap resets, plus unchanged dragging, over USB on 9 November 2026. The earlier USB/Bluetooth result and frozen hashes belong to checkpoint `35e2a8d`, before this geometry change; see [the working baseline](../integration/working-baseline.md). Recalling older phrases and completed-bar dots remain follow-up work.

## Terms

- A **group** is one of eight independently performed parts in Live. It can contain many hidden layer tracks.
- A **phrase** is the collection of accepted layers currently used by a group.
- A **take** is the current recording attempt. Accept keeps it as a layer; Cancel or Retry discards the current attempt.
- The **group reference** is the first accepted take in the current phrase. It defines the group's reference loop length and phase. Other layers may have different lengths.
- **Advance group**, also called clear in the discussion, stops the group's current playback and selects a fresh Session row for that group. Previous recordings remain intact.

## Overview

Eight groups occupy two rows of four at the top of the 15 by 15 LED display. Each group has a 3 by 3 representation. With one-pixel gaps this occupies 15 columns and seven rows.

The eight perimeter pixels show clockwise group-reference progress in the corresponding Live group's color when available. The center shows state:

| State | Perimeter | Center |
| --- | --- | --- |
| Populated, unmuted | Group-colored clockwise progress | Group color |
| Populated, muted | Dimmed group-colored clockwise progress continues | Gentle white pulse |
| Empty, unmuted | Very faint group-colored progress through the current one-bar recording grid | Same color and brightness as a filled empty-progress segment |
| Empty, muted | The same very faint progress | Off |

Progress uses continuous brightness within each pixel's portion of the loop. Completed portions stay lit while the next pixel gradually brightens. Progress must not step between pixels in a way that implies those steps are beats. The same fractional fill applies to the recording border and countdown; white bar-marker dots remain fixed.

Recording and queued-recording states belong in the recording view. They do not need overview representations because the recording view remains open until the take is accepted or aborted.

An empty group has no reference loop yet. Its progress therefore follows the shared one-bar grid and shows where the next quantized recording start will fall. Its unmuted center matches a filled segment of that very faint progress ring; its muted center is off. This idle progress should be much subtler than a populated group's progress.

The bottommost row is a moving metronome in the overview only. A white dot swings from side to side, without a trail. Each denominator beat moves it to the opposite side, so 6/8 has six swings per bar. The visual continues when the audible click is off. When Live is stopped, a dim center dot replaces the swing.

The current graphics fix reuses one clock-phase sample per repaint for bar tracking and swing drawing in both pad programs. It corrects a deterministically reproduced odd-meter jump to the opposite edge. Offline 4/4 endpoint and clock-refresh continuity checks pass. The user subsequently reported "looks good" for the physical USB flash check; on 9 November 2026 he also reported that the current version works well over Bluetooth. This fix adds no UX control or gesture change.

The bottom row on the main screen has no tap-tempo or editor-opening action. Use the physical Mode button to enter the metronome program.

The full-screen metronome has a moving indicator across row 0. Below it, numerator uses `0 <= x < 7` and denominator `8 <= x < 15`, both at `1 <= y < 10`. The seven-column fields are blue and purple. Click fills the lower-left seven columns and Tap Tempo the lower-right seven columns at `10 <= y < 15`, making each button seven by five pixels. Column 7 is black and inactive throughout the control area. There is no Close button. Lift contacts and short-release Mode to return to the controller.

Dragging vertically changes each number relative to its value at touch start, without an initial jump. Each outward step requires two LEDs of travel. A quarter-LED return hysteresis keeps a value from flickering when the finger rests near a boundary. The numerator is 1–99; the denominator steps through 1, 2, 4, 8 and 16. The captured origin and unsaturated detent count stay fixed while the result is clamped to those ranges. Changes apply directly to Live, and changes made in Live appear on the device. Meter editing remains available with accepted loops present.

Double-tapping a meter field requests that value be reset to 4 on the second release. Numerator and denominator keep independent tap histories; two-finger double-taps on both fields request 4/4 through the existing serialized meter operations, rather than one atomic reset. Each tap lasts at most 250 ms, the next tap must start within 350 ms of the first release, and observed travel stays below one LED. Recognized pairs are consumed. An ordinary single tap does nothing, and the detector adds no wait to dragging or other controls. Recognition is host-only; confirmed Live values remain authoritative. Hannes accepted the USB trial with "wunderbar" on 9 November 2026 after being asked to check independent and simultaneous resets and normal dragging. Later on 9 November 2026, Hannes reported "bluetooth funktioniert klasse" for the current version. This is general functional feedback, without separate numerical timing measurements or an itemized Bluetooth regression report.

White digits always show confirmed Live values above the background and raster, including while dragging and when a new actual value arrives from Live during the hold. Every reachable two-LED detent has a full-width line in exactly that field's resting background color: blue `#071724` or purple `#1b0c26`. The selected line is bright blue `#45b6eb` or purple `#bb6de0`. Background space between lines and unreachable positions is black; white digit pixels remain visible over it. This local raster previews intent without issuing extra commands. The newest touch in each field owns only that field's visual preview.

On release, confirmed Live digits remain visible. If intent is still unconfirmed, the raster may remain beneath the digits until confirmation or a maximum of 200 ms. Reset, stale feedback and leaving the overview context clear it. A locally received starting value can lag Live during concurrent edits, so the raster represents intent rather than confirmation.

Click has a green/red background and a five-by-three switch track at column 1, row 11. Its two-by-three white knob starts at column 1 when off and column 4 when on. Tap Tempo is a plain uniform monochrome region: grayscale 16 at rest and briefly 30 when pressed. The earlier icon and full-white tap flash are removed.

Changing meter follows Live's behavior: accepted loops keep their lengths in quarter-note beats and can move out of alignment with the new bar boundaries. Group-reference wraps still schedule new layers. Next-bar actions and the recording-border bar flash follow Live's actual current bar grid.

Recording quantization is fixed at one bar; there is no quantization setting or gesture.

## Group interactions

| Input | Action |
| --- | --- |
| Tap and release a group, empty or populated | Mute or unmute immediately; playback phase continues |
| Straight swipe down from a group | Queue the first take if empty, or a new layer if populated, and enter recording controls |
| Swipe down, then turn left or right | Advance that populated group to a fresh row; ignore an empty group |
| Hold a group | Open its mixer while the finger remains down |

Touching multiple groups before the mixer opens forms a multi-group mute gesture. The mixer stays closed, even if the groups are held or only one participating finger remains. On the last participating finger's release, each selected group toggles once, together, including empty groups. Tapping a group again during the gesture removes its pending toggle; tapping it again re-adds it. A sideways swipe does not cancel. Multi-group gestures cannot record, add a layer or advance a group. Once the mixer has opened from a single hold, secondary touches continue to operate its controls.

Pending groups preview their future mute state while progress continues. Empty groups use their dim-color/off center to preview the future mute state. Actual mute changes still wait for the final release. A steady white dot immediately below every selected group marks its pending change, including empty groups and groups still being touched. The dot disappears when that group's pending toggle is removed or when the final release commits the changes. The marker should represent pending membership, not finger contact.

A hold consumes the touch. Releasing it closes the mixer and does not trigger a tap. Downward gestures must be distinguished from their complete paths before an action is sent, so an L gesture does not first trigger add-layer.

Once a downward recording drag is recognized, the selected group's center becomes full-brightness pure red. Its perimeter continues the same clockwise loop progress in red, with completed progress reaching full red and the next pixel filling smoothly. This also applies to empty and muted groups. The preview follows the held gesture; release submits the current intent. A straight down release requests recording and opens recording controls. Turning into an L changes the pending preview to a black 3 by 3 group. Releasing advances a populated group; an empty L remains a no-op. Moving the finger back into the vertical leg restores recording intent. Returning onto the original group tile clears the pending gesture; releasing there does nothing and must not toggle mute. A further straight downward drag can re-arm recording before release.

A one-pixel white guide indicates the pending gesture. The recording guide is a clean vertical line; the advance guide is a clean down-then-left/right L. It is a compact indicator rather than a painted history of every movement. The line is drawn above group tiles, including red progress and black advance previews. It never paints the bottom metronome row. The guide and pending group preview clear on release, cancellation, contact loss or a view change; multi-group muting does not show a recording or advance guide.

Advancing affects only the selected group. It stops that group's playback and scans forward from its active phrase to the next free row, skipping rows containing any of that group's layer clips. It preserves all accepted recordings and leaves other groups unchanged. This also applies after recalling an older phrase. A device-side phrase picker is deferred; detecting an older phrase launched in Live is part of the follow-up work below.

## Recording controls

The recording view has four controls in this arrangement:

| Left | Right |
| --- | --- |
| Cancel | Accept |
| Retry | Accept and record next |

There is no Back or close-and-continue action. Accepted takes return to the overview, except Accept and record next, which keeps the recording view open. Cancel returns to the overview. Retry stays in recording controls.

| Action | Timing and result |
| --- | --- |
| Start first take | Use one-bar recording quantization |
| Add layer | Start at the next group-reference wrap; use the next bar before a reference exists |
| Accept | Finish at the next bar, keep the take and return to overview |
| Accept and record next | Finish the current take at the next bar, then wait in countdown until the group reference wraps and start the next take |
| Retry | Discard the current attempt immediately and restart at the next group-reference wrap; use the next bar before a reference exists |
| Cancel | Discard the current attempt and return to overview |

Every take ends manually. Additional layers do not automatically end after one group-reference cycle. They may have different lengths. Accept can finish a layer at any bar boundary, including partway through a group-reference cycle.

If an Accept or Accept and record next press arrives too late to reach the upcoming bar reliably, recording continues until the following safe bar. The press is retained; another press is not required. The actual deferred finish defines the first take's reference length. For an existing group, its reference stays unchanged, and Accept and record next waits for the next reference wrap at or after that deferred finish.

Starting a new layer from the overview, Retry and Accept and record next all align the next take's start with the group-reference wrap. Accept and record next first finishes the current take at the next bar, like plain Accept. It then enters the red waiting/countdown state until the next reference wrap. If the scheduled finish is exactly a reference wrap, the next take starts at that same boundary without waiting another full cycle. During the first take, accepting defines the reference from its original start to this next-bar finish; that finish is therefore a wrap of the new reference, and the next take starts there immediately.

Start and Retry retain a valid request when its intended start is too close or an unissued start misses its safe enqueue window. They move to the next safe bar before a first reference exists, or the next safe wrap of the unchanged group reference afterward. The existing countdown shows the actual queued start, and Cancel and Retry remain available while waiting. Retry still discards the mistaken unaccepted take immediately. Timing-only delays do not require another gesture. Genuine recording failures remain failures; a failed restart must leave usable recovery controls rather than strand the recording view.

Recording preserves the group's mute state during and after the take. Previously accepted layers survive Retry and Cancel.

Before recording actually starts, Accept and Accept and record next are inactive because there is no take to keep. A press begun while inactive stays ignored even if recording starts before release. Recording controls belong to the take touched and cannot act on a replacement take. Cancel removes the queued take and returns to the overview. Retry queues the next group-reference wrap, or the next bar before the first accepted take exists.

A one-pixel border around the recording view fills clockwise in red while waiting for recording. It continues the group's current reference-loop phase instead of resetting to zero when requested. Without a reference it follows the shared one-bar phase, matching the empty overview group. Once recording starts, the border uses the group color. Progress remains on that same reference or one-bar clock. Recording starts at the next wrap of that clock, without an extra preparation cycle.

White bar-marker dots on the border show bar boundaries across the full group-reference loop, or the one-bar cycle before the first accepted take. Their positions stay the same during countdown and recording.

At each actual bar boundary, the whole recording border briefly pulses white, then fades back to the current colored progress and fixed markers. This applies during both countdown and recording. The flash is per bar, not per beat, and does not reset progress.

## Mixer while held

The row containing the held group stays visible. The other group row becomes a horizontal pan control. The lower display area contains five vertical faders, in order: volume and four assignable group controls. Volume is three pixels wide at columns 0–2. The assignable controls are two pixels wide at columns 4–5, 7–8, 10–11 and 13–14, with one-pixel gaps at columns 3, 6, 9 and 12. All five faders occupy rows 8–14. Volume uses the group color; all four assignable faders use white. The four controls are generic parameter assignments and are not restricted to sends. Assignment configuration is deferred until the next design session; this change adds no assignment screen or gesture. There is no audio level meter.

The original finger holds the mixer open and does not adjust a control. Another finger adjusts the controls. Releasing the original finger restores the overview and its visual metronome.

Touching a fader or pan control does not change its value. Dragging adjusts it relative to its value at touch start.

The first physical trial confirmed that mixer entry, exit and control access work well. Hannes requested the group color for the playing center, a slower muted pulse and horizontal pan. The second physical trial confirmed those changes and the recording controls. He then requested dimmer group-colored progress while muted and white bar markers on the recording border. Faders use vertical dragging; pan uses horizontal dragging. The muted pulse remains white.

## Inputs and preparation

The experiment uses input routing prepared in Live. Device-side input selection per group is a future requirement. Audio and MIDI workflows remain in scope; the Lightpad itself is a remote and supplies no musical input.

The transport start and stop controls are deferred. The integration experiment must state its required Live preparation, including transport and monitoring, rather than quietly adding device controls.

## Deferred features

- Assigning the four group controls to parameters.
- Input selection on the device.
- A device-side phrase picker. Recognizing an earlier phrase launched in Live is planned below.
- Undoing an accepted layer and restoring removed content.
- Global transport controls and mute-all.
- An audio level meter.

## Trial details and engineering checks

LED glyphs for recording actions, gesture thresholds, pulse rate, brightness, unavailable-color fallback and queued-action animation are not established UX decisions. They can be proposed as visible trial details, not silently promoted to final behavior.

The small pan region and the finger holding a group may affect access to the controls. Test that on the device. The row that remains visible has no additional agreed mixer-mode tap actions.

Exact group-reference-wrap scheduling, asynchronous layer recording, cancel/retry, group-only phrase advancement, multiple groups, mixer control and reliable gesture recognition still need engineering validation. The completed integration spike proves one-group mute and actual-state feedback over USB and Bluetooth, not those other operations. See [the integration spike](../../prototypes/integration/README.md).

Temporary uploads should preserve the saved startup program. The legacy controller is separately archived and has already passed restoration and welcome_home checks.

## Historical metronome implementation trial

Hannes confirmed this metronome behavior on 7 November 2026. He also approved moving gesture interpretation and screen selection into the existing Live script after the combined Littlefoot program exceeded the 7,200-byte program-and-heap limit. The Lightpad retains local rendering, clock interpolation and immediate touch feedback. No extra application is required. The architecture must be tested over Bluetooth for responsiveness and leave room for future controls. The first code pass and offline checks do not establish Bluetooth performance.

That earlier physical trial used a 500 ms editor hold, a 100 ms full-row white touch flash, compact 3 by 5 meter digits and a click toggle directly above the digits. Those controls were superseded by the separate modal program and the current full-screen trial.

## Follow-up requests, 8 November 2026

Hannes approved the proposed metronome layout, slower stepped meter controls and completed-bar dots with "LGTM". The layout and meter changes have automated coverage and positive USB and general Bluetooth feedback. Completed-bar dots and older-phrase reconciliation remain follow-up work. Recalling an older phrase will make it the destination for subsequent overdub layers, and clearing will advance that group to the next available free row.

### Reconcile playback launched manually in Live

When Hannes manually starts an older layer in a group, update the device to reflect actual playback rather than showing only the controller's most recently selected phrase. Observe actual playing clip slots and playback anchors. Detecting playback must preserve existing recordings and must not silently move clips or launch the rest of a row.

Launching an older phrase makes that phrase active for its existing group. Update the device feedback and recording reference accordingly. New overdub layers belong to that recalled phrase and its row, preserving the layers already there; they must not keep recording into the previously selected newer phrase. Clearing stops the active phrase and advances only that group to the next available free row, skipping occupied rows and preserving both older and newer recordings.

The current model retains only its own active accepted layers and reference anchor, so implementation must reconcile them with Live's actual playback. Remaining cases to settle are a single old layer launched while other rows or different lengths still sound, changes during an active take, and clips edited outside the controller. The confirmed whole-phrase recall behavior does not imply silently launching every clip in a row when only one layer is triggered.

### Make meter changes more predictable

The current trial reduces the old one-LED sensitivity to two LEDs per step, with a quarter-LED return hysteresis and a visible ruler. Dragging remains relative, with no jump on initial touch. Numerator steps remain one; denominator steps remain 1, 2, 4, 8 and 16. Values continue to apply directly to Live.

The gain and touch-triggered raster are approved for an on-device trial; exact brightness and detent feel can be refined there. Confirmed white digits stay visible above the reachable detent lines throughout the gesture.

### Use the full metronome screen

Hannes approved a full-screen overhaul with the following layout. A Close button is unnecessary because Mode already returns to the controller.

| Area | Use |
| --- | --- |
| Top row | Existing moving visual metronome, spanning the device |
| Main area | Seven-column blue/purple fields separated by an inactive black column; confirmed white digits always visible above the held-field colored detent raster |
| Bottom area | Click switch with green/red background and white knob; plain monochrome Tap Tempo pad |
| Physical Mode button | Short release returns to the controller; no on-screen Close button |

With the shared-phase fix and always-visible-digit refinement, the current raster trial compiles to 4,751 bytes of code plus a 580-byte heap, totaling 5,331 of 7,200 bytes and leaving 1,869 free. The main controller uses 5,751 bytes of code plus a 532-byte heap, totaling 6,283 with 917 free. Host routing and the modal source must be updated together for the new geometry; the phase fix changes graphics timing in both pad programs while host/native code, gestures and the transport format remain unchanged. Automated checks pass. The USB flash check passed, and Hannes accepted the visible-digit/raster refinement with "top" before requesting the double-tap feature.

### Show elapsed bars during a take

Show how many bars have actually been recorded, including a first take with no established loop length. Hannes suggested white perimeter dots distributed similarly to the existing accepted-loop bar markers.

Count from the actual Native recording start, excluding count-in. Retry and Accept-and-next begin a new count at the actual replacement take's start. Continue counting through a deferred Accept until recording actually finishes. The host already retains the recording anchor; the current device clock contains only repeating phase, so provide an authoritative cumulative count instead of inferring it from locally observed wraps.

The approved starting design is one white dot per completed bar, spread around the perimeter. Until the take is accepted, its final length is unknown; redistribute dots as new bars complete, using the existing real-bar white flash to mask repositioning where timing permits. Keep ordinary progress smooth, and do not make individual pixel movement imply beats. Refine the animation on the device.

The dots count completed bars. Remaining cases are starts off the current project bar grid, long takes beyond the available perimeter positions, and transport or meter changes during a take.
