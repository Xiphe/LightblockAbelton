# Lightpad Live controller

`LightpadController/` runs inside Ableton Live. The normal configuration sets `LIVE_STATE_MODE = True` and discovers marked groups in the loaded Set. It does not inspect saved `.als` XML, hashes or private experiment paths. [Lightpad Bridge](../../../tools/lightpad-bridge/README.md) handles USB/Bluetooth and device programs.

The signed production helper, Live script and login agent are installed with rollback retained, and login startup is activated. The combined idle/status startup passed the power-cycle/touch check. Two accepted USB takes, populated reopening and a fresh Set from the installed template passed. Bluetooth now passes recording, layering, mute, volume/pan and Mode switching from the installed template with USB unplugged. The user also confirmed visible diagnostic notifications after disabling Sleep Focus, with mirroring notifications still off. Core startup, USB, Bluetooth, template and notification checks have passed. The [stable release contract](../../../docs/integration/stable-release.md) distinguishes agreed behavior, code checks and remaining Native validation.

## Live setup after installation

1. For a new project, open **Templates > Lightpad Remote** in Live's browser. It creates a fresh Untitled Set; save it under your own name. The installed template is `~/Music/Ableton/User Library/Templates/Lightpad Remote.als`, with [a portable repository copy](../../../templates/Lightpad%20Remote.als). For an existing project, create or use Group Tracks with unique `[R1]` through `[R8]` prefixes. `[R1] Vocals` maps to tile 1. The remaining name and the track's position can change. You may leave tile numbers unused and add unrelated tracks. A marker on a non-group or a duplicate identifier makes that assignment unavailable; other valid groups remain usable.
2. Choose the intended audio interface in Live. New takes create Audio Tracks using Live's defaults, without copying a manually configured source/instrument track or imposing the prototype's fixed mono channel, monitoring mode or routing. Device-side input selection is deferred. Verify the default recording source before real capture.
3. In Link, Tempo & MIDI, choose `LightpadController`, with Input and Output both `Lightpad Bridge`. No Max for Live device or per-Set MIDI mapping is required.
4. Start Live's transport before recording. With transport stopped, a recording gesture silently does nothing; actual overview, mute, mixer and meter controls remain available.

The validated template has eight marked groups, one empty Audio Track per group, four returns and no clips or FileRefs. Live rejects childless saved groups with "Empty track group". Keep the empty child; new takes still create fresh default Audio Tracks.

If Bluetooth is not connected in macOS, use Audio MIDI Setup > MIDI Studio > Bluetooth configuration to connect the Lightpad. The helper uses that OS MIDI connection and does not pair it itself.

Disable competing legacy BLOCKS control surfaces and raw Lightpad Track/Remote mappings when using this bridge; restart Live after removing an old script so its listeners are gone. Keep the Bridge control-surface input/output selected.

For the single bridge path, the old direct BLOCKS control-surface slots 2 and 3 and raw USB Track/Remote input/output switches were disabled. The original machine's `Preferences.cfg` backup is local-only and is not part of a clone. Back up your own preferences before changing them. Restoring the legacy keyboard controller requires re-enabling its direct BLOCKS MIDI preferences as well as reinstalling the archived device program. Do not restore the whole preferences file while Live is open.

Save, copy and reopen the Set normally. Populated tracks are read from Live, not rejected because they contain clips. Audio-file portability still uses Live's normal project/media management.

## Install the template on another Mac

From the controller checkout, copy the portable Set into Live's User Library. This keeps an existing same-named template intact:

```sh
mkdir -p "$HOME/Music/Ableton/User Library/Templates"
cp -n 'templates/Lightpad Remote.als' "$HOME/Music/Ableton/User Library/Templates/Lightpad Remote.als"
```

If your User Library is elsewhere, use that folder instead. Alternatively open the repository Set in Live and choose File > Save As Template. Then use Templates > Lightpad Remote to create each new Set and save under your own project name. The app installer handles the helper/script/login files; it does not install templates or choose audio preferences.

## Actual-state and recording behavior

`live_state.py` resolves groups and their actual Session clips. It chooses the leftmost eligible playing loop, including muted loops and valid MIDI loops, while excluding unfinished recordings and audio that cannot supply beat-loop timing. It supports nonzero loop starts and mixed rows. It reads active clips directly and computes full row occupancy only for allocation/advance, keeping idle work independent of the phrase-grid size.

`live_recording.py` retains only pending operation state and per-take timing. Begin chooses the current reference, or the next safe bar when none exists. A fresh phrase uses a free row after the highest playing row, or after the last occupied row when nothing plays. New layers create default Audio Tracks and scenes; Retry may reuse its own just-emptied track. Track insertion is post-validated; a misplaced new track is preserved, reported and not repeatedly recreated on every retry.

Accept finishes at the next safe bar. Accept-and-next finishes first, then waits for the selected new take's loop wrap; safe coincident boundaries are supported. Late unissued starts/finishes move to a later safe boundary while retaining the request. Already issued Native operations are observed and are never blindly replayed. Actual clip flags and anchors confirm progress; reaching a countdown target alone does not start a take.

The controller adopts an already recording clip in a marked group. The selected recording group wins, including selection of a nonrecording child of that group; otherwise use the leftmost recording track. Reading or selecting this state causes no recording writes. A queued empty reservation never authorizes stopping/deleting a manual clip that later appears there. Cancel and Retry operate on the current unaccepted take only, preserve other clips, and acknowledge observed cleanup.

`live_controller.py` presents snapshots, clocks, readiness and recording selection to `surface.py`. `active_recording()` and `modal_allowed()` are read-only Native accessors. Default mixer controls follow the first four sends; explicit configured targets retain Native identities and disable only the affected control if moved or replaced. `configure_targets()` explicitly authorizes rebinding. Assignment UI is deferred.

The current production source has no persistent external ownership registry. Normal reopen and Save As rediscover Live state. The remaining evidence boundary for fresh-loop normalization, interrupted Native operations and actual group insertion is documented in the release contract.

## Transport and readiness

The maintained `surface.py` retries failed initial production construction every 500 ms. A helper-only atomic health frame on channel 16 CC114–118 is sent every 500 ms independently of the pad Hello. It reports connecting 0, bridge-unavailable 1, Live-unavailable 2, no-groups 3, failure 4 or ready 5, plus a reason and recording/modal flags. Live cannot declare the physical bridge-unavailable state; that is local to the helper/device.

Main/metronome graphics retain protocol 18/A8. Raw contacts use complete seven-CC frames on all 16 finger channels. Partial contact fragments do not resample the full Native state. The short local action-failure flash is receipt-aware, follows the affected group/context and does not send automatic notifications or reset the workflow. Fresh unassigned snapshot 0 renders blank, while stale/unavailable feedback keeps its established X.

Program Hello clears stale contacts and reseeds graphics without replaying musical intent. Metronome permission reads current state and rejects active recording/cleanup or held contacts. Global status taps take a separate helper-owned route and can request a notification even when Live is unavailable.

The shared output budget remains four frames per rolling 90 ms. The formerly expected sustained-urgent-feedback failure is fixed and its strict freshness check now passes normally; current synthetic clock gaps stay below one second and snapshot gaps below two. USB responsiveness, two folded-group layers and populated reopening have passed the user/Native trial. The current signed helper also reaches stable Bluetooth Ready after the upload-health fix. Hannes confirmed Bluetooth recording, Accept, another layer, mute, volume/pan and Mode open/close on the installed template with USB unplugged. Endurance remains pending.

## Offline verification

```sh
python3 -m unittest discover -s prototypes/controller/live/tests -v
```

The new `test_live_state.py` and `test_live_controller.py` use explicit fake Native requests/observations. They cover discovery, rename/reorder, populated reopening, copy identity, mixed playback, default-track allocation, ownership races, selected recording adoption, conservative timing/defer behavior and countdowns. Existing proof tests remain independent regression coverage. The final host suite passed 729 tests. The [tracked validation summary](../../../docs/integration/validation-summary.md) records the offline and runtime results; no numerical offline result proves Native audio or insertion behavior.

## Historical proof mode

The manifest-backed `AudioController`, `AudioFixture`, `LiveAudioBackend` and `Session` are retained for the earlier proof suite. Set `LIVE_STATE_MODE = False` only when deliberately running that separate mode. Its prepared Set path/hash, fixed layout and feature opt-ins are historical constraints; they are not instructions for a normal production project. [The old host baseline](../../../docs/integration/current-host-baseline.md) and [example scratch manifest](examples/scratch-manifest.example.json) preserve that provenance.
