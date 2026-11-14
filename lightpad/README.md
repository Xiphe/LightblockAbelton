# Device programs

The Lightpad renders and animates locally on a 15 by 15 LED grid. Live interprets gestures and owns musical state; the [bridge](../bridge/README.md) loads programs and keeps its MIDI ports stable. [UX.md](UX.md) records current interactions and pixel geometry.

| Source | Role | Code + heap | Free of 7,200 bytes |
| --- | --- | ---: | ---: |
| `main.littlefoot` | Group overview, held mixer and recording controls | 7,187 | 13 |
| `metronome.littlefoot` | Full-screen meter, click and tap tempo | 5,365 | 1,835 |
| `patch.littlefoot` | Group input assignments and held source names | 3,939 | 3,261 |
| `status.littlefoot` | Connection status and standalone idle | 5,320 | 1,880 |
| `idle-animation.littlefoot` | Shared touchable tunnel, included by status | Included above | |
| `idle-statements-prototype.littlefoot` | Shared hidden-message assets/interaction | Included above | |

Only one entry program runs at a time. Each has its own memory budget. Separate modal programs leave room for local animation and immediate touch feedback; occasional screen uploads preserve Live playback and virtual ports.

Status is saved as the pad's startup program. Main, metronome and patch selection use temporary uploads. Follow the [startup installation steps](../bridge/README.md#install) rather than running an uploader alongside the helper. Keep both idle includes beside status; they are source dependencies, not separate programs to upload.

## Idle interaction

One finger bends the tunnel, pressure changes motion/distortion, and two fingers pull its ends separately. Three simultaneous fingers change the pattern; four return to the first. Touch influence joins and releases over two seconds, while established movement follows directly.

Touch exactly two opposite corners to preview hidden messages. Either diagonal works. Hold for two seconds to latch the mode; release sooner to return. During preview both contacts may move through the outer three-pixel edge band; entering the interior cancels it. After both fingers lift, short taps cycle messages. Use the same opposite-corner preview/hold to return to the animation. Idle touches send no diagnostic requests.

The message set is FCK/AFD, rainbow flag, trans flag and BLM. Fault states instead show the white Ableton logo, orange `[R]` or red `!`; tapping a fault icon requests a Mac explanation.

## Tests

From the checkout root with the pinned source-built `blocksctl` on PATH:

```sh
python3 lightpad/tests/test_current_device.py
python3 lightpad/tests/test_fader_fill.py
python3 lightpad/tests/test_constants_device.py
python3 lightpad/tests/test_patch_device.py
python3 lightpad/tests/test_status_device.py
python3 lightpad/tests/test_status_idle.py
python3 lightpad/tests/test_failure_flash.py
python3 lightpad/tests/test_recording_dots.py
python3 lightpad/tests/test_countdown_dots.py
python3 lightpad/tests/test_marker_animation.py
python3 lightpad/tests/test_metronome_layout.py
python3 lightpad/tests/test_metronome_swing.py
```

The 104 checks compile actual Littlefoot bytecode and execute it in `tests/littlefoot_harness.py` with fake graphics, clock, touch and MIDI natives. They open no device, MIDI port, Live process or app. They cover snapshots, contact context, Constants mixers, monitor and no-source previews, patch catalog/held names and real publisher/pacer initialization, fractional fader fill, failure flash, dots/pages, marker animation, metronome geometry/swing and status/idle behavior. The idle comparison fixture is `tests/fixtures/idle-fractal-prototype.littlefoot`; it is test data, not another runtime program.

`tests/fixtures/current_device.json`, `status_device.json` and `patch_device.json` pin intentional source/binary/resource and scene changes. Review a rendering change before updating fixtures. The VM checks opcodes, bounds and its fixed 800-byte globals/stack reserve; this reserve is not extra program memory.

To test staged main/metronome bytecode without recompiling:

```sh
python3 lightpad/tests/test_current_device.py \
  --main-binary /path/to/main.bin \
  --metronome-binary /path/to/metronome.bin
```

The harness does not prove LED optics, native curve rasterization, physical callback time, Bluetooth latency or audio quality. See [validation](../VALIDATION.md).
