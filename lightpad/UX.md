# Device UX contract

Current controls and display behavior for the 15 by 15 Lightpad. See [setup](../README.md), [architecture](../ARCHITECTURE.md) and [validation limits](../VALIDATION.md).

## Screens and timing

The device has a 15 by 15 LED grid. Main overview, recording controls and the held loop/Constants mixers share the main program. The Audio patch selector, full-screen metronome and global connection status are separate programs. The helper keeps the connection and Live's MIDI ports while switching programs.

Short release of the physical Mode button opens the patch selector from main and returns to main from either settings screen. A hold of at least400ms opens the metronome from main or patch, and opens patch from the metronome. Releasing a recognized hold does not trigger another short action. Switching waits for contacts to lift and is unavailable during recording. Mode is inactive on global status. Neither settings screen needs an on-screen Close button.

Ableton is authoritative. The device follows actual playing clips, mute, parameters and recording, including manually launched older layers and mixed rows. The timing reference is the leftmost eligible currently playing loop in the group, including muted loops. A new take captures its length and phase for that take only; another take chooses again. With no playing reference, recording uses the next safe bar.

## Group overview

Eight groups occupy two rows of four at the top. Each is a 3 by 3 tile, with one-pixel gaps. Together they occupy 15 columns and seven rows. Unique Live Group Track prefixes `[R1]` through `[R8]` assign tiles independently of track order and the rest of each name. The Live group color supplies the tile color.

The eight perimeter pixels show clockwise loop progress. Completed segments remain lit while the next pixel gradually brightens. This continuous brightness must not imply that the eight segments are beats.

| State | Perimeter | Center |
| --- | --- | --- |
| Populated, unmuted | Group-colored progress | Group color |
| Populated, muted | Dimmed progress continues | Slow white pulse |
| Empty, unmuted | Extremely faint current-bar progress | Same color and brightness as a filled empty-progress segment |
| Empty, muted | The same extremely faint progress | Off |

An unassigned tile is blank. Stale/unavailable feedback retains the established X. Group-specific action failures use a brief local flash, preserve the current workflow and send no automatic Mac notification. Recording and queued-recording states use the recording view rather than another overview tile state.

Four Constants occupy columns 0–2, 4–6, 8–10 and 12–14 on zero-based rows 8–9, the ninth and tenth physical rows. Their Live colors are bright when unmuted and dimmed when muted, without loop progress. The first four direct children of the unique `Constants` or `[Constants]` group supply these mixer-only targets in Live order. Constants join the same local mute batch as loop groups, with white pending dots on row 10. Down/L and monitoring gestures do not act on Constants. Their source routing is configured in Live.

The bottom row is a visual metronome only. A white dot swings without a trail, moving to the opposite side on each denominator beat. Thus 6/8 has six swings per bar. It continues with the audible click off; stopped transport shows a dim center dot. Main has no bottom-row tempo tap or editor gesture. Both programs sample clock phase once per repaint to avoid opposite-edge flashes.

## Group gestures

Tap an empty or populated group to mute/unmute it. Muting leaves playback phase running. A recording gesture while Live's transport is stopped silently does nothing.

Hold one group to open its mixer while held. Releasing that finger closes the mixer and consumes the mute tap. A second finger adjusts mixer controls.

Touch multiple groups before the mixer opens to form a mute batch. The mixer stays closed even after only one finger remains. All selected groups toggle together on the last release. Released selections remain pending, and re-tapping one removes or re-adds it. Sideways movement does not cancel. Every selected group, touched or released, shows a white dot below it and previews its future state while progress continues.

Drag down on any group, including an empty one, to arm recording. Before release, its center and progressing ring become bright pure red. A compact white vertical guide appears above the tiles. Turn left or right into a downward L to arm fresh-phrase advance instead: the tile blacks out and the guide becomes an L. Move back to a straight I to re-arm recording; move onto the original group to cancel without toggling mute. A new down-drag can re-arm before release.

A down-drag on a group with no assigned Audio source shows a bright yellow X instead of red arming, with no straight white guide. Release stays in overview. Turning into an L still offers fresh-phrase advance and draws its guide; returning to I restores the blocked-recording X.

A horizontal swipe out of a single group activates that group's live-input monitoring. It previews locally with a blue dot below the group, then remains optimistic until confirmation. Multi-mute's white pending dot takes priority at the same position. Monitoring still passes through the group's volume, mute and effects.

The white guide is a compact indicator, not a full finger history. It draws above group tiles and never on the bottom metronome row. Release, cancellation, contact loss or a view change clears it. Multi-group muting has no gesture guide.

Fresh-phrase advance stops all actual playback in only that group. It finds a free Session row after the highest currently playing row, or after the last occupied row if nothing plays. It skips occupied destinations, preserves every existing clip and leaves other groups unchanged. Mixed rows are valid and do not cause the controller to launch other clips to force alignment. There is no device-side phrase picker.

## Recording controls

| Left | Right |
| --- | --- |
| Cancel | Accept |
| Retry | Accept-and-next |

There is no Back or close-and-continue action. Accept and Cancel return to overview. Retry and Accept-and-next remain in recording controls. Every take ends manually; layers may have different lengths.

| Action | Timing and result |
| --- | --- |
| Begin | Next captured group-loop wrap, or next safe bar with no reference |
| Accept | Finish at the next safe bar and keep the take |
| Accept-and-next | Finish at the next safe bar and start the replacement take at that same boundary, without a base-loop wait |
| Retry | Discard the current unaccepted take immediately, choose a fresh reference and wait for its next safe wrap or bar |
| Cancel | Discard only the current unaccepted take and leave recording controls |

A too-late unissued action moves to the next legal boundary. The action is respected without another press. Accept-and-next plans the old finish and prepared replacement start together for the same safe bar. If admission is too late, both move together to the following bar. Already issued operations are observed rather than blindly replayed. Countdown follows the actual queued start; reaching a predicted boundary alone does not pretend that recording began.

Accept and Accept-and-next are inactive before actual recording. A contact begun while inactive stays ignored even if recording starts before release. Controls belong to the touched take and cannot act on its replacement. Cancel removes a queued take; Retry replaces its request. Previously accepted layers survive both. Recording preserves the group's mute state.

A recording already started manually in a marked group can be adopted. The selected Live group wins, including a selected nonrecording child in that group; otherwise the leftmost recording track wins. Adoption reads Live without replaying a lost device action. A reserved destination does not own a manual recording that later appears there.

## Recording border and counts

The recording border is one pixel wide. While waiting it drains backward in red, anchored to the actual queued target and current Live meter. With more than eight bars remaining, a drain spans an eight-bar page and eight white perimeter dots remain visible. The final eight or fewer bars use one-bar drains and decreasing dots. The last bar clamps empty; a due, invalid or stale countdown shows no count dots until actual recording is observed.

During actual recording and pending finish, the group-colored border advances once per recorded bar. This is take-local progress, captured from actual Native start and its bar metadata. A long base loop never distributes all its bars around the recording border. First takes and overdubs use the same bounded geometry. Meter changes mid-take do not reinterpret completed recorded bars.

For completed bar count C, show `(C modulo 8) + 1` white perimeter dots at positions `floor(k * 56 / N)`, where N is that dot count. Show one center dot per completed eight-bar block at column 7, growing upward from row 13. The first current bar already has its top-left dot:

| Completed bars | Perimeter | Center tally |
| ---: | --- | --- |
| 0 | Top-left dot | None |
| 1 | Top-left and opposite bottom-right | None |
| 7 | Eight dots | None |
| 8 | One dot | One dot |
| 16 | One dot | Two dots |

The host retains the true count; the current display caps at 111, with thirteen center dots. Countdown uses `C = clamp(R - 1, 0, 111)` for R remaining bars. Above eight bars, retain eight perimeter dots with `floor(C / 8)` center dots. Thus nine bars shows eight perimeter dots plus one center dot; eight shows eight without a center dot; one shows one.

Marker changes slide/fade locally over 200 ms. The first set for a new take/view appears immediately. Rapid changes can retarget from the preceding target geometry. Animation does not delay touch feedback or Live actions.

The entire border briefly pulses white at each actual project bar, then fades back to the colored progress and markers. This uses the real bar clock, independently of marker positions and take-local laps. It applies during countdown and recording.

## Mixer while held

The held group's row remains visible. The other group row becomes horizontal pan. Five vertical faders occupy rows 8 through 14:

| Control | Columns | Color |
| --- | --- | --- |
| Volume | 0 through 2 | Group color |
| Assignable 1 | 4 through 5 | White |
| Assignable 2 | 7 through 8 | White |
| Assignable 3 | 10 through 11 | White |
| Assignable 4 | 13 through 14 | White |

Columns 3, 6, 9 and 12 are gaps. Touching a control causes no initial value jump. Faders use relative vertical dragging; pan uses relative horizontal dragging. The holding finger does not adjust a control. There is no audio level meter.

Holding a Constant keeps all four compact tiles visible on rows 8–9. The same five seven-pixel faders move to rows 0–6, with row 7 blank. Horizontal pan occupies rows 12–13 above the visual metronome on row 14; row 11 remains blank. The fader columns, colors, relative movement and no-jump touch start are unchanged. The existing loop-group mixer keeps its original layout. All views and feedback remain local.

An unfilled fader means its Live parameter is exactly at minimum. Any positive value shows fill, with the leading pixel gradually brightening between the resting background and the established full-step brightness. Volume keeps its group color and the four assignable controls remain white.

The four white controls may target sends or group-device parameters. Defaults follow the first four sends. Explicit configured assignments retain target identity and disable only the affected control if its target moves or disappears. Assignments are configured in the [Live script](../live/README.md#local-configuration); there is no assignment screen.

## Patch selector

The selector offers up to 25 Audio sources and preconfigured MIDI instruments in the order they appear inside `[Inputs]`, flattening organizational subgroups. Physical inputs follow each original source's Live configuration. The upper four rows show eight compact three-by-two group tiles in two rows, with column gaps. Row4 is the separator. Below it, 25 three-by-two input tiles fill a five-column, five-row grid without pagination. Unused slots remain empty.

Hold a group and touch its input tiles to toggle assignments. There is no persistent group selection after release. The inverse order also works: hold one or more sources, then touch a group to toggle them together. Moving a contact does not repeatedly toggle assignments. With no group held, holding a source scrolls its name locally across the dimmed group area; group hit targets remain available beneath the name. Multiple different held groups provide no assignment context.

Multiple Audio and MIDI sources may be assigned to one group and recorded together as a single take. Assignments apply to subsequent takes; accepted clips and their source identity remain unchanged. Audio sources can be assigned to multiple groups. A MIDI instrument already owned by another group cannot be added; a selected unavailable source can still be removed. Clear releases its MIDI ownership without deleting accepted clips. The latest assignment selects the monitoring group, and the overview's horizontal gesture can change it later.

An assignment reacts locally immediately. Its input tile's upper row shows the desired state; the lower row pulses between prepared and desired states until routing preparation completes. The group also pulses while work remains. Contacts ending do not cancel already admitted changes. Retapping changes the latest desired state rather than growing a queue of toggles. Failure restores actual assignment and flashes briefly. A missing source cannot be added, but can still be removed from a group that already selected it.

## Full-screen metronome

| Area | Use |
| --- | --- |
| Row 0 | Moving visual metronome |
| Columns 0 through 6, rows 1 through 9 | Blue numerator field |
| Columns 8 through 14, rows 1 through 9 | Purple denominator field |
| Columns 0 through 6, rows 10 through 14 | Click switch |
| Columns 8 through 14, rows 10 through 14 | Monochrome Tap Tempo |
| Column 7 below row 0 | Black, inactive gap |

Vertical dragging is relative to the confirmed value at touch start. Each outward step needs two LEDs of travel; quarter-LED return hysteresis prevents boundary flicker. Numerator spans 1 through 99; denominator selects 1, 2, 4, 8 or 16. Keep the captured origin and unclamped detent count while clamping the resulting value. Apply changes directly to Live and display external Live changes. Meter editing remains available with accepted loops; Live keeps their existing lengths even if they no longer align with the new click.

White digits always show confirmed values above the held raster. Reachable detent rows use exactly the resting field background, blue `#071724` or purple `#1b0c26`; intervening and unreachable rows are black. The selected row is bright `#45b6eb` or `#bb6de0`. The local intent raster adds no commands and may remain visible for at most 200 ms after release while waiting for confirmation.

Double-tap a field to reset it to 4 on the second release. Fields have independent histories, allowing two-finger 4/4 reset through serialized changes. Each tap lasts at most 250 ms, the next starts within 350 ms of the first release, and movement stays below one LED. Consume a recognized pair. Single tap does nothing and adds no drag delay.

Click has a green/red background and a stylized switch: a five-by-three track at x1, y11 and a white two-by-three knob, at x1 when off and x4 when on. Tap is a plain monochrome surface, intensity 16 idle and 30 pressed, with no icon or full-white activation flash. The middle gap continues between the two buttons.

## Global status and unavailable controls

The status program uses the touchable idle tunnel and hidden-message interaction while connecting, disconnected or handing off to main. While untouched, it runs the tunnel for 45 seconds, crossfades into FCK/AFD over two seconds, holds FCK/AFD for 15 seconds and crossfades into the Pride rainbow over two seconds. Pride remains until touch. Touch immediately restores the interactive tunnel and restarts the 45-second phase after release. The transitions blend both complete images; the tunnel continues moving during its fade. Manual message mode retains its existing four-screen cycle. Idle gestures never request diagnostics. Waiting for Live shows a white Ableton logo, no remote groups shows orange `[R]`, and unexpected failure shows red `!`. A fault-icon tap requests a Mac explanation; notifications are never automatic. Mode is inactive on global status. Fresh usable host state returns to main after contacts lift. A missing heartbeat does not replace a last-known active recording screen. Focus can suppress a notification banner without changing controller behavior.

The current Audio setup prepares at leastfour compatible child Audio tracks per group and reuses empty slots. More tracks may be prepared in the background when needed, before the rightmost technical subgroup. The user accepts the known audible insertion cost for overflow; the starting count is editable in Live.

MIDI instrument patching, the complete source catalog, device-side phrase selection, accepted-layer undo/recovery, global transport buttons, mute-all and an audio level meter remain outside the current Audio step. Live supplies source routing and audio defaults. The Lightpad supplies no musical notes. Recording quantization stays one bar, with no setting UI.
