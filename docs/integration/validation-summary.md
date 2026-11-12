# Installed baseline validation

Recorded 12 November 2026 on one macOS home-streaming setup with a ROLI Lightpad and Ableton Live. This is the portable summary of the installed functional baseline. Raw MIDI captures, recordings, application backups, receipts and machine preferences stay in ignored local `verification/` storage; they are not shipped or required by a clone.

## Completed checks

| Check | Observed result |
| --- | --- |
| Offline host | 729 passing tests, including eleven integrated failure-flash and 27 pacing tests |
| Package/install | 21 passing tests |
| Native helper | Five passing CTests |
| Compiled device | 87 passing checks: twelve canonical, twelve status, five differential idle, six local flash, fourteen recording, ten countdown, eleven marker-animation, fourteen metronome-layout and three metronome-swing |
| Total offline | 842 passing checks |
| Saved startup | Combined idle/status saved, then user power-cycle and touch behavior confirmed |
| USB recording | Two accepted layers in a folded group; user reported expected responsiveness |
| Native allocation | Two new default Audio Tracks inside the marked group, Ext. In 1 T-Bone and Monitor Off; both accepted loops saved as 32 beats; earlier tracks preserved |
| Populated reopen | Live reopened the populated Set and the controller returned to Ready without manifests or configuration edits |
| Fresh template | Live saved and reopened the minimal template as a fresh Untitled Set, with eight marked groups, eight empty Audio Tracks and four returns |
| Bluetooth | USB unplugged, fresh installed template: recording, Accept, another layer, mute, volume/pan and Mode open/close passed; user said "works great" |
| Connection feedback | Closing Live displayed the white Ableton status; reopening recovered to the controller |
| Notifications | Signed app authorized; user confirmed visible diagnostic banners after disabling Sleep Focus, while leaving mirroring notifications off |

Live rejected a saved GroupTrack with zero children as corrupt, reporting "Empty track group". The template therefore keeps one empty seed Audio Track in each group. Recording never appropriates those seed tracks; it creates new default Audio Tracks.

Bluetooth upload exposed a separate lifecycle bug: a main-program upload could take about 3.4 seconds while host health expired after three seconds. The helper now processes the host-health lane during uploads and retains actual MIDI receipt timestamps. The three-second timeout is unchanged. The user Bluetooth result above includes this fix.

Full-bundle signing and Launch Services registration fixed the earlier incomplete notification identity. Authorized requests still had hidden banners while Sleep Focus was enabled. Disabling Sleep Focus resolved presentation without another notification code change. No global mirroring/sharing notification preference needs to be enabled.

## Identifying the checked files

Builds record their own source, include, compiler, SDK and binary hashes in the app's `Contents/Resources/build-info.json`. Installation retains hashes and replaced files under `~/Library/Application Support/Lightpad Bridge/revisions/`. Signing and toolchain metadata can change executable bytes on a rebuild.

Recorded final native build input SHA-256:

```text
01468be46250b27da56daa52b53870563543ec8ae1be7238eb92546a1a6767eb
```

Recorded final packaged native executable SHA-256:

```text
d4c4f7059cc71c22c7e43f156401db576be4e5909db5b7983beece3549b8da6b
```

The portable [template](../../templates/Lightpad%20Remote.als) SHA-256 is `c87ee176ab49ef2708e86dd8a8b62bdfa0eae8e26048f7e5c7ace37b7344e54d`. Device fixtures in [verification-tools](../../verification-tools/README-current-device.md) identify current compiled graphics and resources. [sdk-lock.json](../../tools/lightpad-bridge/sdk-lock.json) pins the external supplier tree.

## Limits

The trial establishes the listed functions on this setup. It does not establish sample-accurate audible continuity, musical input quality, long-duration streaming, sleep/wake, a complete crash/rollback rehearsal, nested-group insertion or every manual-edit/cancellation race in real Live. Offline tests cover many such state changes but do not replace physical trials. The [release contract](stable-release.md) records the remaining scope and known observation limits.

The frozen [working baseline](working-baseline.md) and [old host baseline](current-host-baseline.md) describe earlier checkpoints. Their older manifest requirements and expected pacing failure do not apply to the maintained production source.
