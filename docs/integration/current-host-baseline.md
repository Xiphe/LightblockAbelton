# Current host baseline

The maintained source is [the Live Remote Script](../../prototypes/controller/live/LightpadController/). It preserves the tested protocol18 host used with the split main/metronome bridge trial. All30 Python modules except `configuration.py` match that frozen host byte for byte. The committed configuration deliberately contains portable safe defaults, with no local Set paths, feature evidence or proof opt-in. Setup is in [the host README](../../prototypes/controller/live/README.md).

No recording, Session, native timing, parameter, permission or output-pacing behavior was changed during promotion. The promotion makes current role parsing, device-owned auxiliary control routing, the reserved fourth output slot and fresh host modal permission maintained code. Tests now use maintained fake Native fixtures instead of ignored local experiment folders. The base gesture engine remains tested directly, and separate current-role tests cover its actual Surface wiring.

## Checks and known limitation

```sh
python3 -m unittest discover -s prototypes/controller/live/tests -v
```

There are587 checks:586 pass and one expected failure. Existing timing/ownership/cleanup assertions remain active. Role fixtures now encode current Start context and visible recording eligibility. Tests needing a queued protected result explicitly occupy the reserved slot, rather than assuming all output stops after three frames. Twelve modal-permission checks retain both negative examples: fresh graphics can lag Begin, and invalid scope can report zero recording phases while an owned take survives. Permission itself reads current host state and performs no Native writes.

The known failure is `test_output_pacing_feedback.ClockFeedbackPacingTests.test_sustained_urgent_global_changes_preserve_all_stream_expiries_and_shared_budget`. Its12-second workload injects urgent global-control feedback every40ms, alongside eight group clocks and eight group snapshot heartbeats. The preserved pacer respects its four-frame rolling90ms limit, but a group clock gap reaches1.92seconds and exceeds the device's one-second clock freshness. The strict `<1.0s` assertion is unchanged under `unittest.expectedFailure`; an unexpected success fails the suite and calls for review.

This is retained negative evidence. The physical split-program trial did not establish this sustained25Hz urgent-edit workload. Ordinary matched scheduling checks and human responsiveness passes cannot be extended to that stress case, and this baseline does not authorize additional feature opt-ins. No pacer fix was folded into cleanup.

## Scope of validation

The offline suite uses explicit fake Live objects. It checks actual controller callback paths, Native intent ordering, accepted identity/reference preservation, queued cancellation, ownership guards, callback read batching, frame budgets and contact lifecycle. It does not establish audio continuity, physical firmware wall time, long-duration Bluetooth reliability or arbitrary external clip adoption.

The earlier matched actual-bytecode workload exercised18 scheduling scenarios with108 dispatch/application observations. Native dispatch/application stayed in the same callback; apply-to-visible median/p95/max was60/100/140ms in the simulated immediate-setter/instant-wire fixture. Those figures describe the bounded fixture, not physical latency. Device, bridge and install/reconnect validation are tracked with their own maintained components.

For future work, preserve the explicit boundary between observed playing clips and owned accepted references. Current Session state is held in memory and cannot reconstruct accepted audio references after script reload. Any external old-clip reconciliation, cumulative recording-bar display or new modal control must receive its own design and verification; none is implemented by this promotion.
