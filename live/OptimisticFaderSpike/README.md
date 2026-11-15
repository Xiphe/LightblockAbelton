# Disposable optimistic-volume adapter

A one-parameter experiment for the matching bridge fader-spike mode. It controls the unique `[R1]` Group Track’s volume. It has no gestures, views, recording, routing, track/scene creation or selection writes. The device owns immediate interaction; the bridge reconciles intent and confirmation.

## Run with a cheap Set

1. In a fresh Live Set, create one Group Track named `[R1] Probe` containing an empty Audio track. Leave recording unarmed. Save it under a new name. No instruments, effects or clips are required.
2. Make a separate staging copy of this adapter. In that copy’s `configuration.py`, set `TARGET_SET` to the exact saved Set’s absolute path. The committed empty value fails closed, including for Untitled Sets. Keep `ACK_DELAY_SECONDS = 0.0` for the first trial. Set `TRACE_PATH` to a writable local JSONL path only if timing evidence is wanted.
3. Back up the current installed Live script, local configuration and Control Surface settings. Close Live before temporarily installing the staged adapter under the coordinator’s chosen script-folder name. Restart into the cheap Set so Live discovers/imports it, then select that Control Surface with both ports set to the matching bridge’s virtual port. Use the bridge’s explicit fader-spike mode; the production host-UI protocol is not supported here.
4. Restore the backed-up script/settings when ending the experiment. Do not overwrite the heavy working Set or turn this experiment into a production installation.

The original tested fixture is omitted because its stock preset provenance contained obsolete machine-specific paths. Build the minimal Set above instead; there is no fixture generator or installer in this experiment.

## Contract and checks

Channel 16 `CC79=generation` establishes and echoes a stream. Channel 1 frames begin with `CC69=generation`:

- Set: `CC70=sequence, CC71=desired, CC72=sequence`.
- Confirm: `CC73=sequence, CC74=actual, CC75=sequence`.
- Observe: `CC76=revision, CC77=actual, CC78=revision`.

The bridge permits one in-flight command and coalesces subsequent fader intent. The adapter explicitly registers only the five input CCs in Live’s MIDI map. A listener marks changed volume dirty; display callbacks publish the latest observation. Optional acknowledgement delay uses no sleep and leaves Native writes immediate. Optional trace buffers receive/set/ack timestamps and batches file writes in display callbacks.

From the repository root:

```sh
python3 live/OptimisticFaderSpike/test_adapter.py
```

Eight focused checks cover MIDI-map delivery, scope/generation guards, atomic commits, external observations and delayed replies. They do not measure physical responsiveness or prove general controller readiness.

## Matching device and bridge

Build the native helper using the pinned SDK instructions in [the bridge guide](../../bridge/README.md). Stop the normal helper before taking its device connection and virtual ports. Run the resulting binary with the separate device program:

```sh
build/lightpad-bridge --optimistic-fader-spike \
  --device "$LIGHTPAD_SERIAL" \
  --source "$PWD/lightpad/optimistic-fader-spike.littlefoot" \
  --status-file "$TMPDIR/lightpad-fader-spike.json" \
  --spike-ack-delay-ms 0
```

Hold the top-left group for half a second, keep holding and drag the colored Volume bar with a second finger. Release the group finger to close. Only R1 Volume is implemented; the other depicted controls are outside this experiment.

Restart only the helper with `--spike-ack-delay-ms 1000` for the delayed-device-feedback comparison. This delays confirmations and changed observations sent to the device, while Native writes and their acknowledgements remain undelayed. Keep the adapter's own `ACK_DELAY_SECONDS` at zero for that comparison. The bridge drops superseded feedback rather than building a backlog.

Device feedback is one atomic program event `{sequence, actual, header}`. Header bits0–7 are `0xF1`, bits8–9 the seed/observation/ack kind, bits10–16 the epoch and bits17–30 a shared model revision. Active local gestures and newer pending intent remain device-owned. The bridge keeps desired, confirmed and outstanding execution separate.

## Observed result and limits

On 2026-11-15 the user accepted the actual USB interaction with normal feedback as "snappy A.F. love it", and the one-second delayed-feedback comparison as "jup perfekt". Opening, relative dragging, closing/reopening and final-value stability were included. The program uses1686 code bytes and196 heap bytes. This restricted size does not establish capacity for the complete controller.

The normal run observed456 intents,456 Native dispatches and456 confirmations, with no pending write or bridge error at the end. Native parameter-setter time was0.282ms median and0.751ms maximum; these are not total physical-touch latencies. The delayed run coalesced100 intents to98 writes/confirmations, settling to the final requested value with zero bridge errors.

This answers the one-fader ownership/reconciliation question. It does not establish Bluetooth behavior, recording timing, full-controller memory capacity, failure/reconnect guarantees, heavy-Set performance or UI-neutral track insertion. It does not change the normal helper mode by default.

Compiled-device proof from the repository root:

```sh
python3 -m unittest discover -s lightpad/tests -p test_optimistic_fader_spike.py -v
ctest --test-dir build -R '^spike_fader$' --output-on-failure
```
