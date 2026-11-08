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

The fixture pins source hashes, binary hashes, resource sizes and nine LED scenes from the tested programs. Checks cover the real 7,200-byte code-plus-heap limit, handshake and readiness, atomic feedback, malformed records and replay, captured touch roles, blocked main-screen metronome input, modal button bounds, Close cancellation and stale/nonoverview behavior. Future intentional device changes must update these reference fixtures after review.

The harness is an independent Python transcription of the pinned ROLI Littlefoot runner. It uses a fixed 800-byte globals/stack reserve, validates bytecode and memory access, and supplies fake clock, MIDI and graphics natives. Unknown instructions, invalid addresses and out-of-range writes fail. The ROLI permission notice remains in the harness source.

LED fixtures capture the fake renderer's output from the compiled programs; they are not photographs or optical measurements. Native graphics rounding, physical callback duration, USB/Bluetooth delivery and touch latency require device trials. The [working baseline](../docs/integration/working-baseline.md) records those trials separately.
