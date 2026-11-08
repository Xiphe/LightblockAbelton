# Lightpad controller host

`LightpadController/` is the maintained Python Remote Script for Live 12.2.7. It controls eight native Group Tracks and an explicitly prepared pool of audio Session layers. It runs inside Live. [Lightpad Bridge](../../../tools/lightpad-bridge/README.md) owns USB/Bluetooth and temporary device programs; both Live ports use `Lightpad Bridge`.

This is the frozen working baseline. The committed configuration has no local manifest, proof path or enabled recording features. Importing it creates no tracks, changes no routing and starts no transport. Without a manifest, uniquely named groups support the original mute/mixer adapter. With a manifest configured, a wrong Set or topology disables all controls rather than falling back to name-only bindings.

## Check the source

From the repository root, without opening Live or MIDI:

```sh
python3 -m unittest discover -s prototypes/controller/live/tests -v
```

The baseline has 587 checks: 586 pass and one documented expected failure. The strict freshness assertion remains in the stress test, so an unexpected success is also reported as a failure. See [current host baseline](../../../docs/integration/current-host-baseline.md) for the workload, evidence limits and source provenance.

## Prepare a fresh scratch Set

1. Use a separate saved Set with eight uniquely named top-level Group Tracks, `Lightpad 1` through `Lightpad 8`. Create the required audio capture layers directly inside each group. Each pool track needs the same empty Session rows. The verified working template uses eight capture tracks and eight empty phrase rows per group. Smaller pools reach capacity sooner.
2. Route capture tracks from the same intended external audio input to their parent group. Keep capture monitoring Off, tracks unfrozen and all capture/other tracks unarmed at enable time. Prepare monitoring separately so existing loop playback is not recorded into new layers. MIDI capture and generic all-input recording are outside this audio baseline.
3. Provide four return sends for the default mixer assignments. Alternatively configure four explicit send/device parameter targets for each group. The adapter retains their identities and disables that mixer if the targets move or disappear.
4. Turn Arrangement Record, Session Record, Overdub, Arrangement Loop, count-in, Link, Tempo Follower and External Sync Off before enabling. Set global clip launch quantization to one bar. Save the Set and record its actual meter, native bar origin, external input and SHA256. An existing accepted pool is not reconstructed after script reload; start a fresh run from the empty saved fixture.
5. Copy [the manifest example](examples/scratch-manifest.example.json) to private local storage and replace every placeholder with verified saved-Set facts. Its sample 4/4 grid and two-layer names are examples, not observations. `set_path` must be an absolute saved `.als` path and `set_sha256` must match that initial file. Group/layer names must describe the actual direct children. `capture_input_channel` must match Live's external input channel. Supply verified sync evidence only when the relevant native properties are unavailable; never assume an absent property means Off.

The script validates saved identity at startup and actual paths, routing, monitoring, ownership and arming before mutations. Saving the same loaded Set later is allowed. It does not create the template or adopt existing clips automatically.

## Configure an installed copy

Close Live before copying `LightpadController/` into its User Library `Remote Scripts` directory. Preserve the previous installed folder. Edit only that local installed `configuration.py`; keep the repository defaults portable.

- Set `AUDIO_CONTROLLER_MANIFEST` to the reviewed private manifest path. Set `SYNC_UI_EVIDENCE` only when separate verified UI evidence is necessary.
- Keep `VALIDATED_AUDIO_FEATURES = ()` until that installation has the required Native evidence. `record` and `cancel` are required together. `wrap`, `accept_next`, `advance` and `exact_length` are independent opt-ins. Offline checks do not grant these capabilities. Exact-length correction additionally requires actual sample/warp coverage and readback before acceptance.
- Enable `NATIVE_BAR_CLOCK` only with verified native BeatTime/grid evidence. Otherwise retain the explicitly fixed manifest meter. Do not infer a grid from screen appearance.
- Leave `AUDIO_PROOF_MODE` and `AUDIO_CONTROLLER_PROBE_MODE` False for the normal controller. Their paths remain None unless deliberately running the separate proof tools.

Start the bridge using its setup instructions, reopen the empty fixture, then select `LightpadController` in Live's Control Surface preferences with Input and Output both `Lightpad Bridge`. The bridge's program source and host protocol must match. Current host protocol is18; older direct-port mappings are historical and should not be layered over this route.

## Responsibilities and behavior

`surface.py` connects MIDI to current role/context gestures in `device_ui.py` and the retained gesture engine in `device_ui_legacy.py`. `touch_protocol.py` accepts complete seven-CC contact frames on all16 finger channels. The Start captures its visible role and context; a later role/context cannot reinterpret a held press. The bridge owns program switching. The modal metronome uses the same host meter/control regions, and Close cancels pending meter desires before a Native completion can flush them.

`audio_controller.py` joins `session.py`, `audio_backend.py`, fixture validation and actual group controls. Begin/Retry wait for the next safe bar or established reference wrap. Accept finishes at the next safe bar. AcceptNext waits for the reference wrap at or after the finish. Late unissued requests remain queued at a safe legal boundary; already issued Native effects are never blindly retried. Cancel discards only owned unaccepted takes and acknowledges after observed cleanup. Accepted clips and references remain intact. Mute uses native group mute, preserving playback phase.

`protocol.py` keeps the internal command/snapshot contract; `compact_wire.py` projects graphics only after logical output selection. The bridge encodes complete A8 word records. `output_pacing.py` admits three ordinary frames per rolling90ms and one reserved frame for protected results or urgent actual global-control feedback. Metadata has a separate bounded budget. Clock phase is sampled at send time. Native actions never wait for graphics receipts.

The helper-only permission lane uses channel16 CC109 plus nonce for a request, CC109 with the same nonce for grant, CC108 for denial. A reply comes from fresh actual host state, bypassing graphics and without running Native effects. The bridge must match the current connection/request, expire permission within one monotonic second, and invalidate it after raw input or Hello. A program Hello cancels old UI contacts and replays confirmed state; it does not recreate or reset the live Session/backend. Switches during owned recording/cleanup are denied.

`proof_command.py` and `summarize_proof.py` support deliberately enabled, isolated Native proof runs. They are not required for normal operation. Local proof results and recorded audio do not belong in this source tree.
