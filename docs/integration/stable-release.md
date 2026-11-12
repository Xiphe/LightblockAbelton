# Stable release contract and installed candidate

Agreed on 12 November 2026 for Hannes's home-streaming setup. Opening, saving, copying and reopening a supported Live Set should leave the Lightpad usable without a developer rebuilding private configuration. USB and Bluetooth must retain the responsiveness established in the earlier trials. Ableton owns the music and the current state.

The signed production helper, Live script and login agent are installed with rollback retained, and login startup is activated. The combined idle/status startup passed the power-cycle/touch check. Two accepted USB takes, populated reopening and a fresh Set from the installed template passed. Bluetooth now passes recording, layering, mute, volume/pan and Mode switching from the installed template with USB unplugged. The user also confirmed visible diagnostic notifications after disabling Sleep Focus, with mirroring notifications still off. Core startup, USB, Bluetooth, template and notification checks have passed. [The tracked validation summary](validation-summary.md) records completed checks, identifying hashes and remaining limits. Each new build generates its own build information and installation receipt.

## Everyday setup after installation

1. For a new project, open **Templates > Lightpad Remote** in Live's browser and save the new Untitled Set normally. The installed template is `~/Music/Ableton/User Library/Templates/Lightpad Remote.als`; [the repository copy](../../templates/Lightpad%20Remote.als) is portable and has no clips or media references. For an existing Set, give each controller Group Track a unique prefix, `[R1]` through `[R8]`, for its Lightpad tile. For example, `[R1] Vocals`. The rest of the name and the track's position can change. Unused tile numbers are allowed, and unrelated tracks remain outside controller operations.
2. Select the intended audio interface and inputs in Live. New recording layers use Live's default Audio Track settings; the controller does not impose the old mono input, monitoring mode or routing. Check the default recording source before the first real take. Device-side input selection is deferred.
3. In Live's Link, Tempo & MIDI settings, choose `LightpadController`, with Input and Output both `Lightpad Bridge`. These are Live application settings, not per-Set mappings.
4. Let Lightpad Bridge run, then connect the configured pad over USB or Bluetooth MIDI. The installed login agent is activated for automatic startup. If macOS has not connected Bluetooth MIDI, use Audio MIDI Setup > MIDI Studio > Bluetooth configuration to connect the Lightpad. The helper discovers existing OS MIDI connections and does not perform Bluetooth pairing.
5. Start Live's transport before recording. A recording gesture while transport is stopped is a silent no-op. Mute, mixer and actual-state feedback remain available.

There is no production `.als` manifest, saved-file fingerprint or private experiment dependency. `LIVE_STATE_MODE = True` selects the normal engine. The older manifest-backed proof mode remains available for historical tests and is not the production setup procedure.

## Musical contract

- Discover marked groups and their actual clips from the loaded Live Set. Saving, Save As and populated reopening do not require updating paths or hashes.
- Live remains authoritative after manual launches, clip edits, track reordering and reconnects. Mixed rows and unequal loop lengths are valid. Never relaunch other clips merely to force a coherent phrase row.
- The leftmost eligible currently playing loop supplies timing. Muted loops still supply timing; unfinished recordings do not. A new take snapshots that loop's current length and phase for that take only. Later takes choose again from Live. The first accepted take is no longer a permanently retained group reference.
- With no playing reference, a new take uses the next safe bar and a fresh free row after the last occupied row. Starting a fresh phrase in a mixed group stops all actual playback in that group, then chooses a free row after its highest currently playing row. Existing clips remain intact; other groups keep playing.
- New takes create default Audio Tracks and Session scenes as needed. They do not appropriate quiet manually created input, instrument or effect tracks. Retry may reuse its own just-emptied track. A failed group insertion preserves the new track and does not create unlimited replacements on repeated retries.
- Accept finishes at the next safe bar. A too-late unissued request moves to a following legal boundary. Accept-and-next finishes first and queues the next captured loop wrap, including a coincident boundary when Native admission is safe. Retry discards only the current unaccepted take and obtains a fresh reference for its replacement.
- The controller can adopt a recording already running in a marked group. Prefer the selected Live recording group, including selection of a nonrecording child in that group; otherwise use the leftmost recording track. Reading this state must not issue recording operations.
- Only a confirmed issued start or the adopted current take authorizes cancellation of that take. Reserving an empty destination does not own a clip that later appears there. A manual clip in an unissued following reservation must not be stopped, deleted or disarmed.
- Native state confirms start, finish and cleanup. An exception after an issued operation does not authorize replaying it. Manual completion or replacement wins; ambiguous audio is preserved.

Volume, pan and the four group controls read confirmed Live parameter values. Default controls follow the four send positions. Explicit device/return assignments retain their target identities and refuse to silently control a different target after reordering. Failure of one assignment leaves other controls usable. Assignment UI remains deferred.

## Global readiness and feedback

The status program integrates the exact accepted v9 tunnel and hidden messages. Connecting state 0, bridge-unavailable state 1 and ready state 5 use that idle renderer; the former rainbow perimeter and blue chain are superseded. Ready host state still hands control back to main. Idle touches operate the animation and hidden messages, with no diagnostic taps.

| State | Appearance | Meaning and action |
| --- | --- | --- |
| Connecting or bridge unavailable | Accepted v9 elastic tunnel | Wait during loading/recovery; if it persists, check USB/Bluetooth and that the bridge is running. |
| Waiting for Live | White Ableton logo | Open Live and check the control surface's two Bridge ports. |
| No remote groups | Orange `[R]` | Add a valid `[R1]` through `[R8]` prefix to a Group Track. |
| Unexpected failure | Red `!` | Tap for the observed cause and the next useful action. Do not infer a cause from silence alone. |

Tapping a fault icon requests a local Mac notification. Idle gestures never request a diagnostic notification. Nothing automatically opens a window, plays a notification sound or sends an error notice. Notification permission and Focus settings can affect delivery; the bridge also provides the explanation on demand. The helper-owned diagnostic route works while Live is unavailable. Mode is inactive on the global status screen.

A fresh usable host state returns to the main program automatically after contacts are released. An absent health heartbeat does not replace a last-known active recording screen. A loaded program, a fresh device model and usable Live groups are separate observations; none should be presented as proof of the others.

The combined idle/status startup program replaced the saved yellow probe. The user power-cycled the pad and confirmed idle/touch behavior returned. Main and metronome remain temporary uploads. The legacy navigation program remains archived and reinstallable. The accepted idle branch is integrated from commit `bb90f24`. Packaging includes `idle-animation.littlefoot` and `idle-statements-prototype.littlefoot`, with both include hashes recorded alongside the status source and binary.

Group-specific problems stay local. A rejected action gives the affected group or mixer a short flash and preserves the workflow. It does not open a global screen or automatically notify the Mac. An unassigned tile is blank; the existing X still identifies unavailable/stale feedback, rather than a new tiny error-icon vocabulary.

## Module boundaries

| Component | Responsibility |
| --- | --- |
| `live_state.py` | Rediscover `[R]` groups, current playing/recording clips, loop phase and the Native bar grid. Idle reads use the active slot index; full phrase occupancy is read only when needed. |
| `live_recording.py` | Ephemeral recording transactions, allocation, pinned per-take timing, observed completion and guarded cleanup. |
| `live_controller.py` | Surface facade, actual snapshots/clocks, readiness, recording adoption and per-control mixer bindings. |
| `surface.py` / `device_ui.py` | Touch context, gestures, current view reconciliation, local failure flash and the helper health heartbeat. Partial touch CC fragments do not resample Native state. |
| Native bridge | One SDK connection, program selection, health supervision, local notifications and persistent virtual MIDI ports. |
| `main.littlefoot` / `metronome.littlefoot` / `status.littlefoot` | Local graphics and immediate touch feedback. Each program has a separate 7,200-byte code-plus-heap budget. |

The helper-only health frame is channel 16 CC114–118, with sequence/state/reason/flags/matching sequence. It is published every 500 ms independently of the main Hello gate. Program graphics retain protocol 18/A8. A failed initial Live construction is retried at 500 ms intervals; transient initialization failure does not require reopening the Set by hand.

## Offline verification and remaining release checks

The host baseline passed 729 tests, including eleven integrated flash-delivery and 27 pacing tests. The integrated candidate passes 21 package/install tests and five native CTests. Status coverage is now 17 checks: twelve status checks and five differential comparisons against accepted v9. The full offline total is 842 checks: 729 host, 21 package/install, five native and 87 compiled-device checks. [The tracked validation summary](validation-summary.md) records the checked files and runtime observations. Build information and installation receipts are generated locally and remain outside Git.

The former sustained-urgent-feedback expected failure is now a normal strict passing regression. Current synthetic traces keep clock gaps below one second and snapshot gaps below two seconds, with measured clock gaps of 0.72–0.84 seconds and the existing four-frame-per-90-ms budget. These simulated scheduling bounds are not physical Bluetooth latency measurements.

Actual USB validation completed two accepted layers while group 1 was folded. Live created two new Audio Tracks inside native group 16, using Ext. In 1 T-Bone and Monitor Off; both accepted loops saved as 32 beats. Hannes reported that responsiveness felt as expected. The populated copy reopened without manifests, and the bridge automatically returned to Ready. Closing Live produced the white Ableton status. These observations establish those particular cases, not audible sample continuity or the full recovery matrix.

Two legacy BLOCKS control surfaces and raw USB Track/Remote input/output were disabled after a preferences backup. Their old listeners remained in the first Live process and required a restart. The production Bridge input/output selection was already correct.

Live rejected a zero-child GroupTrack as corrupt with "Empty track group". The clean starter therefore retains one original empty Audio Track per marked group. Live accepted it, saved it through File > Save As Template, and loaded it from **Templates > Lightpad Remote** as a new Untitled Set. The canonical [repository template](../../templates/Lightpad%20Remote.als) contains eight marked groups, eight empty Audio Tracks, four returns, zero clips and zero FileRefs. These seed tracks only keep the native group structure valid; recording creates fresh default tracks.

The helper's host-health lane now remains active during device uploads, with each heartbeat's actual MIDI receipt time retained. This fixes a Bluetooth main upload taking about 3.4 seconds and repeatedly exceeding the unchanged three-second health timeout. The installed signed helper now reaches stable Bluetooth Ready with fresh health and zero errors. Hannes confirmed "works great" for recording, Accept, another layer, mute, volume/pan and Mode open/close from the installed template with USB unplugged.

The signed helper is installed, strictly signature-verified and registered with Launch Services at its final path. The user enabled Lightpad Bridge notifications. Full-bundle signing fixed the earlier incomplete-identity authorization error. Later, requests were authorized and recorded as delivered but banners remained hidden. Hannes identified active Sleep Focus, disabled it and confirmed "works now, also with mirroring off" after retries. Visible notification presentation has passed; no further notification code change was needed. The global mirroring/sharing notification preference remains off. Focus and notification settings remain the user's choices, and a hidden banner is not proof that the bridge missed the diagnostic tap.

The following checks remain:

- Exercise reconnect, sleep/wake, crash recovery, rollback and long-duration home streaming with many layers/scenes.
- Verify audible capture/continuity, asynchronous timing, manual recording adoption, mixed playback, manual loop edits, cancellation races and failed-start recovery beyond the bounded trials.
- Expand group-insertion coverage to nested cases as needed. A childless saved group is unsupported by Live itself, rather than a controller recovery case.

Core checks are complete: login activation, saved-startup power cycle, responsive USB and Bluetooth recording/controls, populated reopening, fresh-template loading and visible diagnostic notifications.

Fresh-recording length normalization requires the observed finish anchor, unchanged fresh loop/end markers and actual sample/warp coverage. An external stop, relaunch or observable marker edit wins. If a user changes both end markers before the first completion observation, that history cannot always be distinguished from Native rounding by a later snapshot alone. That is an evidence boundary for the physical trial, not a claim of perfect recovery.

## Historical evidence

Checkpoint `35e2a8d` passed the bounded USB/Bluetooth controller and modal trials on 8 November. Metronome checkpoint `1d31013` received positive USB and general Bluetooth feedback on 9 November. Recording-dot trials 45/46 were accepted over USB; trial 47 added paging/animation and was installed, with its physical validation still pending in the record. Those trials do not validate this production engine, installer or health protocol.

[The working baseline](working-baseline.md), [the host baseline](current-host-baseline.md) and [baseline hashes](../../prototypes/controller/live/baseline-sha256.json) remain frozen provenance. Their old manifest rules and expected pacing failure describe that checkpoint, not the current production source.
