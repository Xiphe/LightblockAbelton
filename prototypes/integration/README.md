# Archived Live integration spike

This is an archived proof, not the production setup. Use the [current controller guide](../controller/live/README.md) and [release contract](../../docs/integration/stable-release.md) for the maintained eight-group controller.

This temporary engineering spike controls one Live Group Track. Its layout and gestures have no UX approval; the controller UX still needs an explicit design pass.

Copy the entire `live/LightpadIntegrationSpike` folder into the Live User Library's `Remote Scripts` folder, restart Live, and select **LightpadIntegrationSpike** as a Control Surface with the Lightpad's matching input and output ports. [Ableton's installation instructions](https://help.ableton.com/hc/en-us/articles/209072009-Installing-third-party-remote-scripts).

Use a copied scratch Set with exactly one top-level Group Track named **Feedback probe**. Remove the previous learned CC20 mapping and disable other control surfaces that could answer the probe. The script controls no track if the exact eligible name is absent or ambiguous. Renaming a previously ineligible track into the target requires refreshing the script or changing the track list.

The existing saved Lightpad probe sends channel 16 CC20 values 0/127 for the desired inactive/active state. The script sets native `Track.mute` to the inverse and replies with CC20 derived from the actual mute property, including external changes. It ignores the pad's CC21 measurement acknowledgment. Muting preserves running clip phase. No recording, playback, routing, or other mixer properties are changed.

When the target disappears, feedback stops; the existing probe can retain its last color until a request times out. There is no invented inactive confirmation. A pad reconnect can request the current state with an unchanged desired value.

Offline callback-path checks:

```sh
/opt/homebrew/bin/python3 -m unittest discover -s prototypes/integration/tests -v
```

All six offline tests pass. They exercise the actual script's callback path with fake Live objects: native CC forwarding, requested and external mute changes, authoritative feedback, unchanged-state replies, target guards, removal, refresh, and listener cleanup.

The script also ran in Live 12.2.7 against a copied scratch Set with the native learned MIDI mapping removed. External Live activator changes produced matching pad acknowledgments, and physical USB taps toggled the group with the expected red/green confirmation. Automated results were:

| Connection | Matched requests / ACKs | Custom script median | Custom script range | Earlier native mapping median |
| --- | --- | --- | --- | --- |
| USB | 20 / 20 | 33.537 ms | 15.849–58.004 ms | 219.904 ms |
| Bluetooth | 20 / 20 | 45.289 ms | 37.908–60.214 ms | 251.117 ms |

Each run had 20 requests and 20 matching acknowledgments, with no missing, extra, or duplicate messages. Live's logged actual group state alternated with all 20 commands in each run. The metric is host arrival of the pad's CC20 request to host arrival of the matching pad CC21 acknowledgment. It excludes the host-to-pad trigger leg. The temporary measurement program adds a guarded CC22 trigger while retaining the existing UI and CC20 protocol. These are short, separate runs with the scratch Set's transport stopped; they do not establish the cause of the remaining delay.

Both temporary upload sessions ended with Ctrl-C and exit status 0; neither sent a save-default request. The saved startup program remains unchanged. The user confirmed that the saved probe still controlled the group over Bluetooth after the temporary session ended. It initially showed yellow, obtained its first confirmed green state on release, then continued with matching green/red feedback.

Recording, layers, multiple groups, mixer controls, and prolonged operation remain unvalidated. Touch latency, audible latency, and LED refresh timing were not measured. Muting during running playback and phase continuity also need a playback test.

To back out, set Live's Control Surface row 6 and its input/output to **None**. Close Live, then remove only the newly added `LightpadIntegrationSpike` folder from the User Library's `Remote Scripts` folder. The other pre-existing rows were left unchanged, and the original preferences were backed up privately. The original Set was preserved; the mapping removal applies only to the scratch copy.

To repeat the automatic measurement, use the installed `blocksctl` release. Keep the temporary upload attached in one terminal:

```sh
blocksctl upload prototypes/integration/lightpad/trigger-probe.littlefoot --device SERIAL --timeout 20
```

In another terminal, list the current endpoints, then use the matching source and destination IDs:

```sh
python3 verification-tools/measure_live_feedback.py --list
python3 verification-tools/measure_live_feedback.py --source SOURCE_ID --destination DESTINATION_ID --samples 20 --interval 0.5 --seconds 30 --first-value 0
```

The first value assumes the group is active. Use `127` if it is inactive. Leave the pad and Live controls untouched during a run. End the upload with Ctrl-C afterward. `upload` preserves the saved startup program; this test never calls `save-default`.
