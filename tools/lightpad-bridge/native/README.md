# Native helper sources

Use the [checkout-level build and installation guide](../README.md) for a fresh machine. It verifies the pinned SDK, compiles all three pad programs with their includes, runs five native CTests, signs the app bundle and stages supplier notices. Build/staging does not launch or install the app.

For a native-only build with a configured Apple toolchain:

```sh
cmake -S tools/lightpad-bridge/native -B /tmp/lightpad-native-build \
  -DCMAKE_BUILD_TYPE=Release -DBLOCKS_SDK_DIR=/absolute/blocksctl/vendor/sdk
cmake --build /tmp/lightpad-native-build --parallel 4
ctest --test-dir /tmp/lightpad-native-build --output-on-failure
```

Prefer `build.py` for the tested Command Line Tools configuration and full vendor verification. A native-only executable is not a complete signed app and does not replace packaging/registration for diagnostic notifications.

The raw runtime CLI needs the exact pad serial and all three source entry points:

```sh
lightpad-bridge --device EXACT_16_CHAR_ID \
  --source /absolute/main.littlefoot \
  --modal-source /absolute/metronome.littlefoot \
  --status-source /absolute/status.littlefoot \
  --status-file /absolute/status.json --lock-file /absolute/bridge.lock
```

Keep the adjacent idle include files with status. Normal packaged launches supply these paths from the bundle and use no automatic trial cycles. The helper owns one SDK connection, USB/Bluetooth discovery, temporary program uploads, virtual MIDI ports, Mode switching, host-health supervision and local diagnostic notifications. Live owns gestures, clips, recording and mixer state; the pad draws and interpolates locally.

Graphics retain protocol 18/A8. Whole state/clock/UI units become at most three Native events in one SDK packet. Raw touch inputs are independent of graphics acknowledgments. Modal permission uses helper-only CC109 grant and CC108 denial with a matching nonce. Host health uses CC114 through CC118 and remains active during uploads, preserving each heartbeat's actual MIDI receipt time. Upload duration cannot masquerade as lost Live health, and delayed old heartbeats cannot make a closed Live session fresh.

Short Mode release toggles main/metronome after contacts lift. A hold of at least 400 ms emits one reserved input-selection request; release emits no short action. Status uses the accepted idle renderer or Live/[R]/! fault icons. Fault-icon taps request a Mac explanation; idle touches do not. A ready marker reports device-model state, not optical or musical readiness.

The five native CTests cover protocol/codec, topology/transport policy, modal state, short/long recognition and health transitions. They do not open hardware. [The validation summary](../../../docs/integration/validation-summary.md) records the separate USB/Bluetooth, startup and visible-notification trials plus remaining audio/endurance limits.
