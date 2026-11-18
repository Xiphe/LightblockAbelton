# Local performance client

`performance-main.littlefoot`, `performance-patch.littlefoot` and `performance-metronome.littlefoot` implement the matched Audio/MIDI controller. They use the atomic F2 messages and semantic commands described in `bridge/native/performance_protocol.md`. They do not replace the installed controller by themselves.

Main owns every contact, hold, mute chord, reversible down/L preview, mixer view and relative control value locally. No raw contact or host-owned view update is required. The recording view opens locally in preparing state; countdown and actual recording progress require matching Native take facts. A forward F2 revision gap marks a cached-model reseed request. Repaint sends channel16 CC110 value19 at most once per second, preserving contacts, pending values and views; a later cache snapshot repairs the missing fact without a periodic full-model stream. Unknown/unavailable parameter targets use a confirmed128 sentinel and remain at an unfilled, noninteractive control until a valid actual value arrives; zero remains a valid known value. A record action is bound to its take token; Next/Retry can adopt the admitted replacement directly without receiving an intermediate old idle event. Late observations and acknowledgements cannot replace a newer parameter intent or an active control contact.

The eight loop groups retain the existing geometry, group colors, muted pulse, smooth progress, four white controls, horizontal pan, recording buttons, reverse countdown pages, animated8-bar markers and independent real-bar flash. Clear previews an empty phrase immediately; stale populated observations and an admission ACK cannot undo that hint. Actual empty facts end it, rejection restores the last actual populated state, and a shared two-second observation window bounds the hint if no completion arrives. This is a device presentation hint, not evidence that Native stopping finished.

The main also owns source-empty Down feedback and horizontal monitor selection. No assigned source gives a yellow X and suppresses the straight recording trace; the reversible L can still Clear. A horizontal swipe previews and requests monitoring locally. Its blue group dot yields to the white mute-batch indicator. Source availability and confirmed monitor ownership arrive in the packed global fact, without a contact roundtrip.

The separate patch program retains the compact group/input geometry. It owns contact context, group-first and input-first pairing, local name scrolling and desired assignment masks. Contacts emit the affected 13-bit and/or 12-bit bank of a 25-source selection, including multi-source chords. Repeated touches replace each desired bank; stale acknowledgements cannot undo a newer selection in either bank. Its upper input row shows the target and its lower row pulses between prepared and desired states. Source/group facts and routing readiness reconcile separately from command admission. All 25 slots occupy the five-column input grid. Source metadata distinguishes Audio/MIDI, availability and confirmed MIDI ownership. A foreign-owned instrument cannot be added to another group. All names are cached locally as 32 ASCII bytes per slot; holding a source needs no host request.

The separate metronome retains colored meter fields and visible digits, two-LED detents with quarter-LED return hysteresis, held target raster, the green/red click switch, plain monochrome tempo button and independent double-tap-to4. Controls are local and optimistic; actual values reconcile after ownership and pending intent end.

All three clients support the allocation ticket described in the semantic wire contract. Existing contacts finish normally before the yellow center bar appears. A later repaint sends the matching grant, so Live cannot start allocation before a complete busy frame. The host retains that ticket through every track and routing step until the complete next take is ready or preparation fails. New contacts are ignored until the ticket clears, including orphaned moves/releases from touches begun while blocked. Busy does not change views, take tokens or local clock animation.

The four Constants targets are local mixer-only tiles on rows 8–9. Holding one uses the upper seven-pixel faders and lower pan strip, retaining the same optimistic values and contact ownership. Constants have no recording or monitoring gesture. The second MIDI channel carries their 24 parameters without longer frames; command keys and atomic observation contexts are distinct. See the matched contract2 [wire definition](../bridge/native/performance_protocol.md).

## Measured build budget

With the installed `blocksctl` compiler used by the existing harness:

| Program | Code | Heap | Total /7200 | Globals | Observed VM stack |
| --- | ---: | ---: | ---: | ---: | ---: |
| Main | 6740 | 436 | 7176 | 216 bytes | 168 bytes |
| Mixed patch | 3980 | 1104 | 5084 | 76 bytes | 136 bytes |
| Metronome | 3306 | 252 | 3558 | 80 bytes | 160 bytes |

Main has 24 bytes of code-plus-heap margin; patch has 2,116 bytes free. This proves the current client fits; it is not a future-feature headroom claim. Checked VM execution covers the exercised paths and does not prove hardware timing or every firmware execution path. Debug-only getter functions were removed from the production program to fit, without changing controls or transport messages. The tests read canonical compiled VM memory and check its source-declaration/global-count layout rather than compile a transformed diagnostic program.

Main heap holds72 desired values,72 confirmed values with128 marking an unavailable target,72 last parameter sequences, twelve packed mixer models, nine recording/global clock records and sixteen four-byte contacts. Desired/sequence high bits retain dirty/pending flags. Contact words use a discriminated union: group origins preserve both eleven-bit axes, parameter contacts preserve their absolute control key, one eleven-bit axis and captured value, and recording contacts retain their action/token. No contact precision is discarded. The group word's unused high byte retains Clear's sequence and last actual populated bit. Constants never acquire recording clocks.

Source-level simplification uses existing Littlefoot instructions and the pinned compiler. Rendering remains on-device. Exhaustive comparison of the 2,000 muted-pulse time positions,2,000 seeded clock states,150 recording/countdown frames and180 overview/loop-mixer frames matched the accepted baseline. These checks support unchanged display behavior; they do not measure physical firmware latency.

## Verification

Run from the repository root:

```sh
python3 -m unittest discover -s lightpad/tests -p 'test_performance_*.py' -v
```

Focused checks execute canonical compiled Littlefoot bytecode. They cover all groups and mixer targets, no-jump relative contact starts, hold/release with no host, chord preview and retap including reuse of the original anchor contact ID, reversible gestures, semantic generation/sequence frames, stale ACKs, external changes during contacts, Clear completion/rejection/timeout, revision-loss recovery with bounded reseed traffic, actual countdown versus preparing, accepted/retried take tokens, recovery of an actual following take after a rejected Retry without stale-token takeover, and cancellation of a pending replacement with either old or new facts racing, a promised Next/Retry replacement failing before any intermediate take fact, long-loop recording pages, global bar flashes, metronome detents/raster and simultaneous double-tap reset. The mixed patch checks cover 25 slots, cross-bank retaps, foreign MIDI owners, label caching, and local desired feedback, either contact order, combined source selection, reused contact IDs, unavailable-source cleanup, release during pending work, delayed facts, stale ACKs and bounded reseeding. New main checks cover source-empty Down/L behavior and optimistic monitor ownership with mute-dot priority. Metronome ACK checks exercise stale and matching replies after contact release for every seven-bit sequence, including the shift-comparison precedence regression.

The Python harness uses fake time, LEDs and MIDI natives. It cannot establish firmware latency, BLE delivery, audio capture quality or Native Live timing. Those remain matched-system physical acceptance checks. Neither source saves a startup program, writes firmware, opens MIDI ports or installs itself.
