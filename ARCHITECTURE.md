# Architecture

Three runtimes share a controller. Ableton owns the musical state, a macOS bridge owns the device connection, and Littlefoot programs draw the interface locally. There is no separate audio engine, Max for Live dependency or per-Set sidecar database.

## Ownership

| Runtime | Owns | Entry points |
| --- | --- | --- |
| Ableton Remote Script | Actual groups/clips, gesture interpretation, recording actions, mixer bindings and view state | [live/LightpadController](live/LightpadController/) |
| macOS helper | One SDK connection, transport discovery, program uploads, stable virtual MIDI ports, Mode button and diagnostic notifications | [bridge/native](bridge/native/) |
| Lightpad | Graphics, clock interpolation, progress/marker animation and immediate touch feedback | [lightpad](lightpad/README.md) |

The same bridge handles USB and Bluetooth. macOS must first provide the MIDI connection; the helper does not establish Bluetooth pairing. It prefers USB for new connections but retains a healthy Bluetooth connection when USB appears. Other SDK uploaders must be closed.

## Live is authoritative

`live_state.py` discovers `[R1]` through `[R8]` Group Tracks and reads actual playing/recording clips. Names after the prefix, track ordering, unrelated tracks, mixed rows and different loop lengths can change. Save As and populated reopening do not need file manifests, hashes or remembered phrase ownership.

The leftmost eligible playing loop supplies the current group reference, including a muted loop. A take captures its timing for that take only; another take chooses from Live again. Without a playing reference, use the next safe bar. Idle reads use active slot indices; full row occupancy is read only for allocation or advancement.

`live_recording.py` keeps ephemeral pending/active take state. New takes create default Audio Tracks and scenes as needed. A manual track is never appropriated as a recording destination. Track creation is followed by actual group-membership checks; a misplaced new track is preserved, and retries do not create unlimited replacements. The template retains one empty child per group because Live rejects childless saved groups.

Actual Native flags and anchors confirm start/finish. An unissued late request moves to the next legal boundary; an already issued operation is observed rather than replayed after uncertainty. Reserved slots confer no ownership over manual clips that appear there. Cancel/Retry affect only the confirmed current unaccepted take. Manual recording selection can be adopted without issuing writes.

`live_controller.py` exposes snapshots, clocks, readiness and mixer operations. Volume/pan/mute are independent of the four assignable controls. Explicit parameter bindings retain Native identity for the loaded Set; reordering/replacement disables the affected control instead of silently writing another target.

## Responsiveness and graphics

`surface.py` and `device_ui.py` interpret complete touch contacts and reconcile screens from actual Live state. Partial seven-CC touch fragments do not trigger full Native reads. A callback shares a bounded read cache; mutation boundaries invalidate it. Recording controls are bound to the touched take/context so a late release cannot act on a replacement.

The pad receives state and timing, then animates locally. Brightness interpolates progress within a pixel, metronome motion uses a shared repaint phase, and marker movement does not delay touch or musical actions. Graphics share a four-frame rolling 90 ms output budget. Host health has its own lane and continues during program uploads; each heartbeat retains its actual MIDI receipt time.

The device has a 7,200-byte code-plus-heap limit per program. Main contains performance controls; metronome is a separate occasional modal; status contains idle and connection explanations. This keeps the main interaction local and leaves each modal its own memory budget. Switching programs preserves Live playback and virtual ports, clears stale contacts, and reseeds state. Held contacts and active recording/cleanup prevent unsafe modal entry.

Short Mode release requests main/metronome switching. A hold of at least 400 ms emits one reserved input-selection request and no short action on release. No input-selection screen is implemented.

## Readiness and recovery

A loaded program, fresh device model and playable Live state are separate observations. Host health arrives every 500 ms independently of the device Hello gate. Failed initial host construction retries at 500 ms intervals; a reconnect reseeds graphics without replaying lost musical intent.

Status has touchable idle for connecting/disconnected, white Ableton for waiting on Live, orange `[R]` for no remote groups and red `!` for unexpected failure. A three-second missing-health timeout normally opens status; a last-known active recording protects its recording screen. Fresh usable host state returns to main after contacts lift. Group action failures stay local as brief flashes; unassigned tiles are blank, and stale feedback retains its X.

Fault-icon taps use a helper-owned route and can request a Mac notification without Live. Idle touches do not request diagnostics. Full-app signing and Launch Services registration establish notification identity. macOS permission and Focus still control banner presentation; `--explain` provides the same text without a banner.

Main/metronome graphics use protocol 18/A8. Helper health is the atomic channel-16 CC114 through CC118 sequence/state/reason/flags/matching-sequence frame. Status uses helper messages independently of Live's gesture gate. App and script are installed together so those contracts match.

## Packaging and limits

The [installer](bridge/README.md#install) stages a signed helper, complete Live script and optional login agent, then checks hashes before replacing any installed root. It retains local receipts/replaced files for rollback. Saved pad startup, template installation and Live preferences are separate explicit steps.

The observation model has limits. If both fresh clip end markers are edited before the first completion observation, a later snapshot cannot always distinguish that edit from Native rounding. Audio normalization therefore requires the observed finish anchor, unchanged fresh markers and sample coverage. Broader real-world timing, manual-edit and endurance cases remain listed in [VALIDATION.md](VALIDATION.md).

## Audio performance implementation branch

The opt-in `--audio-performance` block replaces the default host-owned gesture/view path with local device ownership. The accepted fader proof showed that local hold/open/drag/close remains responsive even with one-second delayed confirmations. `performance-main.littlefoot` and `performance-metronome.littlefoot` interpret their own contacts and display optimistic intent. `bridge/native/performance.hpp` reconciles received intent separately from actual Live facts; `live/PerformanceController` uses retained Native targets and a separately scheduled preparation workflow. The wire contract is colocated with the bridge implementation.

Recording launch consumes a ready Audio destination. Maintenance reuses a compatible empty slot or inserts before an existing rightmost technical subgroup, then verifies actual membership/routing before making it ready. The user accepted restoring the prior selection after Live's automatic insertion selection jump. This does not make Native creation asynchronous or guarantee a hidden viewport change. The recovery block uses a fixed mono Audio input and a gated cheap Set; it does not yet restore input patches or Constants.

The matched block passed compiled/host checks and the user accepted the complete USB workflow in the cheap Set, including mixer, multi-mute, recording, Retry, Cancel, same-bar Accept+Next and modal switching. Bluetooth and heavy-Set responsiveness remain separate delivery gates. The existing implementation and heavy Set are preserved until those trials; this opt-in path is not the default production installation.
