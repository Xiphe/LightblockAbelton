# MIDI routing spike

Throwaway Native Live proof, archived on `codex/midi-routing-spike`. This package
is not imported, installed or enabled by the production controller. See
[VERDICT.md](VERDICT.md) for the measured result and remaining decisions.

## Fixture and setup

Use a cheap saved Live Set, not Temple of Loops. Start with `[Inputs]` containing
one Audio source `Probe Audio` and one stock Drift instrument `Probe Synth`, an
outside MIDI generator `4-MIDI`, and two Group Tracks `[R1] Probe` / `[R2] Probe`.
Keep Main at its minimum while configuring routes. No instruments are copied by
the recording workflow.

Copy `LightpadMidiRoutingProbe/manifest.example.json` to `manifest.json` alongside
it. Set the exact absolute scratch Set and mailbox paths and explicit fixture
track names. Install that folder as a temporary Remote Script, restart Live on
the cheap Set and choose it in an unused Control Surface slot with no MIDI ports.
Construction makes no routing, monitoring, transport or recording changes.

Read the actual routes:

```sh
python3 probe_cli.py inspect
```

Submit one explicit operation with a JSON payload. `route` requires the
session-local selector identifier and display name returned by a fresh inspection.
The probe revalidates the actual offered Native object before writing. It never
executes source text or retries an uncertain request. `steps.py` is a small host
helper for these same explicit operations.

`prepare_pair` and `next_pair` accept `midi_monitor: "off"` to leave MIDI capture
lanes silent while the original instrument provides live monitoring. Audio
members retain Monitor Auto. Pair calls are consecutive Native requests, not an
atomic transaction. The current script demonstrates Native behavior only; it
does not implement the production readiness, source ownership or timing engine.

For controlled external notes and CC64, build `midi_input.swift` with `swiftc` and
run `./midi-input --virtual`. Enable its temporary input for Track messages in
Live and route the instrument and MIDI capture lanes to it. Enter decimal
three-byte messages such as `144 65 96`, `128 65 0`, `176 64 127` and `176 64 0`.
Use real host timestamps. Zero-timestamp injections produced negative recording
positions in this trial. `quit` releases the test channel and removes the port.

The fixture, recorded media, local manifest and mailbox are private artifacts,
not committed here. After the trial, stop playback, disarm capture tracks,
restore temporary measurement routing, save the cheap Set and return the
Control Surface slot to None. The production bridge and Live script remain intact.
