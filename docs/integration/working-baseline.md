# Working controller baseline

Recorded 8 November 2026. This checkpoint preserves the controller that Hannes tested over USB and Bluetooth before the next feature iteration. The tested versions had accumulated in ignored experiment directories while maintained source and packaging still described earlier controllers. Promoting the matched device, helper and Live sources makes the working behavior recoverable and lets future changes be checked without those private directories.

The beneficiary is the performer using the Lightpad as a screen-free loop-station remote for Ableton Session View. Immediate controls, confirmed visual state and preserving previous recordings matter more than avoiding a small background helper. Program memory became the constraint: the combined controller left only two bytes free. The user approved separate temporary modal programs, accepted their measured switching delay, and then requested a committed baseline before implementing more features.

## Matched components

The helper and device use private protocol 18 with typed A8 feedback events. Raw contacts retain captured roles and context. The helper owns discovery, the single SDK connection, program replacement and the physical Mode button. A short release toggles main/metronome immediately. A hold of at least 400 ms emits one input-selection placeholder request and cannot also emit a short action on release.

The Live script owns gestures, Session bookkeeping, recording, actual mixer values and scheduling. Before entering the metronome, the helper asks Live for fresh nonce-correlated permission. A cached overview image is insufficient because graphics can lag a recording action. The read-only permission check denies active takes, cleanup, held or partial contacts, pending gestures and invalid fixture scope. A stream change, stale reply or new touch invalidates the request. Closing the modal forwards its complete release before starting the return upload.

Source recording configuration remains portable and disabled by default. The tested local installation has explicit fixture paths and validated capabilities; those local settings are intentionally not copied into the committed defaults. All other promoted runtime modules retain the tested behavior.

## Frozen device programs

| Program | Source SHA-256 | Binary SHA-256 | Code + heap | Free |
| --- | --- | --- | ---: | ---: |
| Main | `c758e95c76c9564e4269f1c9bfd2433f793e2e49686a982f86f5476d99ad8dcc` | `9462492c242d2749f1574cef986d1c8d77d29be20f47fe5c690be4a733fb7fe6` | 6,279 | 921 |
| Metronome | `ed80dc7a1167c523d4662a1b639d52846f426855e1ced46941a4714ffc3769de` | `8b6204b80a33e6e92ee1158223f24b37bd8cb79ed87276a85233c34744f1e6aa` | 4,319 | 2,881 |

Both budgets include the heap. The SDK's separate globals/stack reserve is not additional program capacity. The one-shot readiness marker is retained in these sources, so the recorded resource numbers match the tested programs.

## Physical observations

The main controller and modal were exercised on the same Lightpad over USB and Bluetooth, with USB unplugged for the Bluetooth trial. The user accepted the modal behavior and switching pause. The final USB and Bluetooth checks covered short Mode clicks, the metronome controls, long press without a second release action, group mute, volume/pan, recording and adding a layer after a modal round trip.

Three automatic USB cycles produced six switches. The device's model-ready marker arrived 633–774 ms after starting the metronome upload and 817–939 ms after starting a main-program upload. Those early measurements included a separate 300 ms single-click wait, which was subsequently removed when short/long press replaced double-click. The quoted upload/model times themselves exclude click recognition and permission roundtrip.

Three manually exercised Bluetooth round trips produced six switches: 2,243–2,532 ms entering metronome and 3,372–3,480 ms returning. These are transport-to-model-readiness observations, not optical measurements. Three accepted Native takes had lengths of 12, 8 and 12 quarter-note beats, exactly matching their requested start/finish differences and observed loop bounds. They were disarmed and playing at completion. Advance to the next row was also observed.

USB hold events arrived 405–408 ms after the helper observed button-down, once per hold. The final Bluetooth hold arrived at 407 ms, once, with no short action or program swap on release. No new bridge errors occurred during the Bluetooth workload. One older stale-feedback error remained in status from USB removal and was not counted as a new Bluetooth failure. The device had been off during the first connection attempt; after power-on the helper restored the controller over Bluetooth without restarting Live.

The tests used a prepared scratch Set with virtual input for silent recording. They do not establish audio quality, input isolation, audible continuity, physical touch-to-audio latency or arbitrary-session compatibility. Existing accepted clips are not reconstructed into controller bookkeeping after a full Remote Script reload. Power-cycle and sleep/wake endurance remain general helper follow-ups.

The factory Ableton BLOCKS script also logged an arm-state exception on Session Record changes. It is separate from this custom script and was left unchanged. The custom controller's bounded workload still passed; this checkpoint does not claim that all other installed control surfaces are error-free.

### Retained feedback stress limit

The promoted host suite exposes one existing limit in the tested output pacer. A synthetic workload supplies urgent global feedback every 40 ms while eight group clocks and eight snapshot heartbeats remain active. The transport frame limit still holds, but a group-clock gap reaches about 1.92 seconds, exceeding its one-second freshness target. Continuous rapid meter editing can therefore compete with background group feedback. This workload was not established as passing by the bounded physical trials.

The strict freshness assertion is retained as a named expected failure. An unexpected pass is reported, so a later scheduling fix must update that explicit baseline. Other tests must pass normally. The cleanup does not change the installed or promoted pacer to address this separate behavior change, and test summaries must distinguish the expected failure from passing checks.

## Maintained-checkout validation

The checkpoint was exported into a new temporary directory using only its selected source files and the earlier committed repository contents. It contained no private experiment tree, installed app, local Live configuration or uncommitted historical prototypes. From that export, 586 host checks passed with the one named expected stress failure; all 12 compiled-device checks, three packaging checks and four native CTests passed. The documented build command staged a new app containing both programs and supplier notices without launching or installing it. Its device source, bytecode hashes and resource sizes matched the frozen table above. Local documentation links resolved within that export.

## Provenance and recovery

The frozen antecedents are the local modal-program trial 34 and short/long-press trial 35. Their raw logs, application snapshots and disposable Live projects remain private and ignored. The maintained checks encode their useful protocol, resource, contact, recording and permission regressions without loading those directories. The source hashes above and the user-observed results preserve the evidence needed to identify the promoted device programs.

The installed helper used for the final trial had SHA-256 `8c53256cdad7c576e20f65efc89d4ae1c0b41330abb9d59d77f900f719cfa9ab`. Maintained native sources are promoted from that tested build. The first maintained rebuild matched all executable code sections, including its 1,389,800-byte text section. Its differing string was the compilation timestamp, which also changes associated link metadata. Build manifests identify the current native inputs and newly built executable; rebuilding under another toolchain can change executable bytes without changing those sources.

No firmware or saved-default writes were used. The saved yellow/green/red probe and the separately archived legacy navigation controller remain available through the previously verified restoration process. No test audio is included in this checkpoint. This cleanup does not replace the running app or reload Live.

## Next iteration

The approved follow-ups remain specifications, not baseline behavior: recognize older phrases launched in Live and target subsequent overdubs there; use a slower, clearer meter grid and the full metronome screen without Close; display completed recorded bars; and design the actual input-selection modal. See the [UX specification](../ux/device-prototype-spec.md).
