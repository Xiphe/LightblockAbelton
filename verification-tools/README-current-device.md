# Current compiled-device checks

Run from the checkout root with the source-built `blocksctl` compiler on `PATH`:

```sh
python3 verification-tools/test_current_device.py
```

The command compiles `prototypes/controller/lightpad/main.littlefoot` and `metronome.littlefoot` into a temporary directory, then executes their actual bytecode through `littlefoot_harness.py`. It opens no MIDI ports, applications or SDK connection and does not upload a program. Static fixtures and the maintained pure host timing encoder are its only project inputs. Private experiment directories are not required.

For an independently staged pair, supply both binaries explicitly:

```sh
python3 verification-tools/test_current_device.py \
  --main-binary /path/to/main.bin \
  --metronome-binary /path/to/metronome.bin
```

The fixture pins source hashes, binary hashes, resource sizes and ten LED scenes. Checks cover the real 7,200-byte code-plus-heap limit, handshake and readiness, atomic feedback, malformed records and replay, captured touch roles, blocked main-screen metronome input, fullscreen modal button bounds and stale/nonoverview behavior. Future intentional device changes must update these reference fixtures after review.

Run `python3 verification-tools/test_metronome_layout.py` for the fullscreen meter checks: centered confirmed white digits always visible over the raster, including during holds and external actual-value changes; captured controls; two-LED detents with return hysteresis; host/device parity; and pending-feedback expiry. Nonselected raster lines reuse the exact idle background colors, with a bright selected line and black elsewhere except for the digit overlay. The checks cover equal-width fields separated by a black center column, five-row buttons, the colored Click switch and plain monochrome Tap button, and preserve the existing main LED scenes. The current modal has 1,869 bytes free. Hannes accepted the digit/raster refinement and double-tap reset over USB, then reported that the current version works well over Bluetooth on 9 November 2026.

Run `python3 verification-tools/test_metronome_swing.py` for endpoint continuity in both compiled programs. The fake native clock advances during repaint to expose a one-frame opposite-edge flash at odd-meter bar boundaries. The fix samples one phase for both parity tracking and swing drawing. Ordinary 4/4 endpoints and clock refreshes also pass; the reported 4/4 flash was not reproduced offline. After the fix the user's physical USB flash check reported "looks good". The later general Bluetooth feedback is also positive; these checks do not prove atomic display refresh on the device.

The harness is an independent Python transcription of the pinned ROLI Littlefoot runner. It uses a fixed 800-byte globals/stack reserve, validates bytecode and memory access, and supplies fake clock, MIDI and graphics natives. Unknown instructions, invalid addresses and out-of-range writes fail. The ROLI permission notice remains in the harness source.

LED fixtures capture the fake renderer's output from the compiled programs; they are not photographs or optical measurements. Native graphics rounding, physical callback duration, USB/Bluetooth delivery and touch latency require device trials. The [working baseline](../docs/integration/working-baseline.md) records those trials separately.
