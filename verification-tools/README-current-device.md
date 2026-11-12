# Current compiled-device checks

These checks compile maintained Littlefoot sources and execute their actual bytecode under `littlefoot_harness.py`. They use fake graphics, time, MIDI and touch natives. They do not open MIDI ports, discover a device, start apps, upload a program or write a saved default. Private experiments are not dependencies.

The production candidate has separate main, metronome and status programs, including the accepted v9 tunnel and hidden messages. The signed helper/host are installed with login activated and rollback retained. Startup, USB recording, populated reopening and fresh-template loading passed. The user confirmed Bluetooth recording, layering, mute, volume/pan and Mode switching. The user confirmed visible diagnostic notifications after disabling Sleep Focus; mirroring notifications remain off. These runtime observations are separate from the offline suites below.

## Commands

Run from the checkout root with source-built `blocksctl` on `PATH`:

```sh
python3 verification-tools/test_current_device.py
python3 verification-tools/test_status_device.py
python3 verification-tools/test_status_idle.py
python3 verification-tools/test_failure_flash.py
python3 verification-tools/test_recording_dots.py
python3 verification-tools/test_countdown_dots.py
python3 verification-tools/test_marker_animation.py
python3 verification-tools/test_metronome_layout.py
python3 verification-tools/test_metronome_swing.py
```

To inspect an independently staged main/metronome pair without recompiling, supply both paths:

```sh
python3 verification-tools/test_current_device.py \
  --main-binary /path/to/main.bin \
  --metronome-binary /path/to/metronome.bin
```

`fixtures/current_device.json` pins main/metronome source/binary hashes, resource sizes and 19 LED scenes. `fixtures/status_device.json` separately pins the fault masks/colors, integrated idle behavior and status bytecode. Update fixtures only after reviewing an intentional source/rendering change. The approved fresh-unavailable snapshot now renders an unassigned tile blank; stale feedback retains the established X. The separate local action flash is host-owned and tested without adding tiny group icons or notifications.

## Current resource bounds

| Program | Code | Heap | Total | Free of 7,200 |
| --- | ---: | ---: | ---: | ---: |
| Main | 6,574 | 532 | 7,106 | 94 |
| Metronome | 4,751 | 580 | 5,331 | 1,869 |
| Status + idle | 4,888 | 432 | 5,320 | 1,880 |

The VM also enforces a fixed 800-byte globals/stack reserve. That reserve is not extra program capacity. Unknown opcodes, invalid addresses and overflow fail the harness. Its supplier notice is retained.

## What the checks cover

The main suite covers handshake/readiness, atomic snapshots, malformed records/replay, captured contact roles, blocked main-row metronome input, modal button geometry and expired/nonoverview feedback. The status suite covers the white Ableton logo, orange `[R]` and red `!` masks, accepted idle rendering for states 0/1/5, standalone boot, helper-only atomic events, stale-status fallback and fault-icon diagnostic touch/held-contact messages. Five differential checks compare the tunnel and hidden-message behavior directly with accepted v9, including the absence of idle diagnostic taps. Packaging retains both adjacent idle include files and their hashes. A readiness marker is model evidence, not optical or musical readiness.

Recording tests cover all displayed counts 0–111, exact take binding, first-dot timing and bounded center/perimeter geometry. At actual start the top-left dot is already the first bar; after one complete bar the second appears opposite it. For completed count C, the perimeter has (C modulo 8)+1 dots and the center has floor(C/8) dots, stacked upward from y13. The host keeps the unbounded count while the display caps at 111. Bar metadata is captured at actual recording start; changing meter mid-take does not reinterpret those elapsed bars.

Countdown tests cover the retained actual target, current Live meter and due-time invalidation. For remaining bars R, the wire count is clamp(R−1,0,111). Above eight remaining bars, eight perimeter dots stay visible while red fill drains an eight-bar page. The last eight or fewer bars use one-bar drains and decreasing dots. The last bar locally clamps empty; only observed Native recording switches to forward take-local progress. Scoped view encoding allows the exact eight-bar page without weakening ordinary/global clock validation.

Marker positions slide/fade locally over 200 ms; first/new-take markers appear immediately. Identical heartbeats do not restart animation. Fast retargets use the previous target geometry. The real project bar supplies border flashes independently of marker placement. Long base loops never paint their full set of markers over recording/countdown.

Metronome checks cover seven-column fields with the center gap, confirmed white digits over the held-field raster, two-LED detents, hysteresis, double-tap reset, colored Click switch, monochrome Tap and the shared per-repaint phase sample that prevents opposite-edge flashes. They preserve the accepted earlier geometry while testing the current sources.

## Host, pacing and release validation

```sh
python3 -m unittest discover -s prototypes/controller/live/tests -v
python3 -m unittest discover -s tools/lightpad-bridge -p 'test_*.py' -v
```

The host source includes Live-state discovery/reopen/allocation/adoption tests, partial-touch batching, health and receipt-aware local flash tests. The host baseline has 729 passing tests, including eleven integrated flash-delivery tests and 27 pacing tests. The native helper has five passing CTests and package/install now has 21 passing Python tests. Integrated status has 17 checks: twelve status and five differential v9 checks. The other suites retain twelve canonical, six local-flash, fourteen recording, ten countdown, eleven marker-animation, fourteen metronome-layout and three metronome-swing passes. That totals 87 compiled checks and 842 offline checks across host, package/install, native and compiled suites. The [tracked validation summary](../docs/integration/validation-summary.md) records identifying hashes, current physical observations and limits. Rebuilds generate their own app build information; installs generate local rollback receipts. Neither requires ignored experiment files.

The former pacing expected failure has been fixed. Strict synthetic workloads retain clock gaps below one second and snapshot gaps below two, within the existing four-frame rolling 90 ms budget. Recorded clock gaps are 0.72–0.84 seconds across those workloads. An old baseline's expected-failure statement must not be applied to the current source.

These are logic/resource/scheduling checks. The harness does not prove native graphics rounding, physical callback time, display atomicity, audio quality, USB/Bluetooth latency, actual insertion into empty Live groups, macOS notification delivery, sleep/wake or endurance. See the [release contract](../docs/integration/stable-release.md) for physical checks still required and the [working baseline](../docs/integration/working-baseline.md) for dated historical trials. Historical USB/Bluetooth success is not a current-build Bluetooth pass. The separate [runtime record](../docs/integration/validation-summary.md) contains current physical observations and unresolved checks.
