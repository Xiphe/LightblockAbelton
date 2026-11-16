# Architecture

Three runtimes share the controller. The Lightpad owns gestures and optimistic UI, a macOS bridge reconciles intent with actual observations, and Ableton owns the music. There is no separate audio engine, Max for Live dependency or per-Set ownership database.

## Ownership

| Runtime | Owns | Entry points |
| --- | --- | --- |
| Lightpad | Contacts, gestures, screen state, optimistic controls and animation | `lightpad/performance-*.littlefoot` |
| macOS bridge | One SDK connection, USB/Bluetooth discovery, stable virtual MIDI ports, program switching, intent reconciliation and notifications | `bridge/native/main.cpp`, `performance.hpp` |
| Live Remote Script | Actual groups/clips, retained mixer targets, recording transactions and separately scheduled preparation | `live/PerformanceController` installed as `LightpadAudioInputs` |

A gesture never waits for a round trip to draw its own feedback. Live receives semantic commands rather than raw contacts. The bridge keeps replaceable parameter intent separate from discrete recording commands, and reconciles later facts without replacing the device's active gesture state. [The wire contract](bridge/native/performance_protocol.md) defines admission, readiness and observation contexts.

The same bridge handles USB and Bluetooth. macOS must first provide the MIDI connection; the helper does not establish Bluetooth pairing. It prefers USB for new connections but retains a healthy Bluetooth connection when USB appears. Other SDK uploaders must be closed.

## Live state and direct actions

The adapter binds only explicitly configured Set paths. Marked `[R1]` through `[R8]` groups supply musical targets. The leftmost eligible playing loop supplies each take's timing reference; that reference is retained for this recording only. New takes observe Live again.

Mute and mixer commands write retained Native objects without scanning the Set. Named macro bindings retain identity and guards. A missing, moved or replaced target disables only that control rather than writing a different parameter.

Begin, Accept and Accept-and-next enqueue prepared Native actions directly when their timing window is ready. Actual clip flags and anchors confirm recording; a finished countdown is not confirmation. An unissued late Accept moves to the next safe bar. Accept-and-next ends the old take and starts its replacement at the same boundary. Native calls for multiple members are sequential, not an atomic transaction.

Recording tokens scope Cancel/Retry to the current unaccepted take. Accepted recordings survive subsequent cancellation. Already issued actions are observed rather than blindly replayed after uncertainty. Manually deleted completed clips do not require dereferencing invalid old Native wrappers. Clear stops only its group and advances to a free phrase row without deleting content.

## Preparation and routing

Preparation runs outside touch-command dispatch. Active observation reads playing slots; occupancy inventory is bounded per background callback. Known clock and take facts are flushed before structural maintenance.

Each group has prepared music Audio lanes followed by a rightmost `[Remote]` subgroup. Technical source helpers live inside `[Remote]`. Source effects stay on the original tracks, tapped Post FX. No source instruments or racks are copied. One group at a time monitors the selected live inputs.

A take captures one destination per selected source. Two sources create two independently named and colored Audio clips with a shared take token, row and timing boundary. Changing input selection does not retarget an already admitted take. Quiet direct Audio tracks with an empty current-phrase slot are reused strictly in Live track order. Source labels, devices, pan and already matching input routes do not influence priority. Preparation configures the chosen input/monitoring and source label/color while preserving devices, mixer, output routing and old clip metadata. All subgroup descendants are excluded by their parent membership.

The current inventory starts with four Audio lanes per group. With automatic overflow enabled, maintenance inserts another lane after the music tracks and before `[Remote]`. Live's structural API is synchronous. It can block the audio engine and cause a dropout in a heavy Set; scheduling it separately does not make it asynchronous. The accepted selection repair restores the previous track/scene/slot after Live selects a newly inserted track. Neither behavior guarantees an invisible viewport change.

## Device programs and transport

The device has a 7,200-byte code-plus-heap limit per program. Main, patch, metronome and status are separate uploads. Main uses 7,192 bytes, leaving eight bytes; adding main-view features requires another measured memory decision. Modal programs have their own budgets.

Short Mode opens patch from main and returns to main from either settings view. A hold of at least 400 ms opens metronome from main/patch and patch from metronome. Releasing a recognized hold does not short-click. Contacts and active recording protect program changes. Uploading a modal preserves Live playback and the virtual ports, then reseeds the device model.

Clocks and display facts are bounded and coalesced. Device interpolation drives smooth progress locally. Host health has a separate lane and continues during uploads with original receipt timestamps. A new transport session clears wire parsing and reseeds current facts without replaying musical actions.

## Startup, diagnostics and installation

A loaded device program, a fresh model and playable Live state are distinct observations. The status program shows touchable idle without a bridge, white Ableton while waiting for Live, orange `[R]` without usable groups, and red `!` for unexpected failure. Fault taps request Mac notifications. Focus and notification permissions control whether macOS displays them.

The signed app's mode selects its complete four-program bundle. The [installer](bridge/README.md#install) validates the matching Live package and local configuration, checks hashes before replacing installed roots, and retains replaced files and receipts for rollback. The login agent starts the app from its installed location with no development arguments. Diagnostics use durable Application Support paths. Saved device startup and Live preferences are separate from app/script installation.

The older `live/LightpadController` uses the legacy `18/A8` host-owned gesture protocol. It remains supported by legacy-mode packaging, but must not be mixed with the current Audio programs and semantic protocol.

[Validation](VALIDATION.md) distinguishes offline tests, observed hardware behavior and remaining limits.
