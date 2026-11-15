# Local Audio performance client

`performance-main.littlefoot` and `performance-metronome.littlefoot` implement the matched experimental Audio block. They use the atomic F2 messages and semantic commands described in `bridge/native/performance_protocol.md`. They do not replace the installed controller by themselves.

Main owns every contact, hold, mute chord, reversible down/L preview, mixer view and relative control value locally. No raw contact or host-owned view update is required. The recording view opens locally in preparing state; countdown and actual recording progress require matching Native take facts. A forward F2 revision gap marks a cached-model reseed request. Repaint sends channel16 CC110 value19 at most once per second, preserving contacts, pending values and views; a later cache snapshot repairs the missing fact without a periodic full-model stream. Unknown/unavailable parameter targets use a confirmed128 sentinel and remain at an unfilled, noninteractive control until a valid actual value arrives; zero remains a valid known value. A record action is bound to its take token; Next/Retry can adopt the admitted replacement directly without receiving an intermediate old idle event. Late observations and acknowledgements cannot replace a newer parameter intent or an active control contact.

The eight-group main retains the existing geometry, group colors, muted pulse, smooth progress, four white controls, horizontal pan, recording buttons, reverse countdown pages, animated8-bar markers and independent real-bar flash. Clear previews an empty phrase immediately; stale populated observations and an admission ACK cannot undo that hint. Actual empty facts end it, rejection restores the last actual populated state, and a shared two-second observation window bounds the hint if no completion arrives. This is a device presentation hint, not evidence that Native stopping finished.

The separate metronome retains colored meter fields and visible digits, two-LED detents with quarter-LED return hysteresis, held target raster, the green/red click switch, plain monochrome tempo button and independent double-tap-to4. Controls are local and optimistic; actual values reconcile after ownership and pending intent end.

## Measured build budget

With the installed `blocksctl` compiler used by the existing harness:

| Program | Code | Heap | Total /7200 | Globals | Observed VM stack |
| --- | ---: | ---: | ---: | ---: | ---: |
| Main | 6721 | 476 | 7197 | 184 bytes | 160 bytes |
| Metronome | 3210 | 252 | 3462 | 72 bytes | 148 bytes |

Main has only three bytes of code-plus-heap margin. This proves the current client fits; it is not a future-feature headroom claim. VM stack measurements cover the exercised paths and do not prove hardware timing or every firmware execution path. Debug-only getter functions were removed from the production program to fit, without changing controls or transport messages. The tests read canonical compiled VM memory and check its source-declaration/global-count layout rather than compile a transformed diagnostic program.

Main heap holds48 desired values,48 confirmed values, with128 marking an unavailable target,48 last parameter sequences,48 pending flags, eight packed group models, nine clock records and sixteen packed contacts. Contact origin/current coordinates use11-bit lanes. The group word's unused high byte retains Clear's sequence and last actual populated bit; it does not travel to Live. All arithmetic remains on the device callback path. No Native object or saved Set metadata is stored here.

## Verification

Run from the repository root:

```sh
python3 -m unittest discover -s lightpad/tests -p 'test_performance_*.py' -v
```

Thirty-two focused checks execute canonical compiled Littlefoot bytecode. They cover all groups and mixer targets, no-jump relative contact starts, hold/release with no host, chord preview and retap including reuse of the original anchor contact ID, reversible gestures, semantic generation/sequence frames, stale ACKs, external changes during contacts, Clear completion/rejection/timeout, revision-loss recovery with bounded reseed traffic, actual countdown versus preparing, accepted/retried take tokens, recovery of an actual following take after a rejected Retry without stale-token takeover, and cancellation of a pending replacement with either old or new facts racing, long-loop recording pages, global bar flashes, metronome detents/raster and simultaneous double-tap reset.

The Python harness uses fake time, LEDs and MIDI natives. It cannot establish firmware latency, BLE delivery, audio capture quality or Native Live timing. Those remain matched-system physical acceptance checks. Neither source saves a startup program, writes firmware, opens MIDI ports or installs itself.
