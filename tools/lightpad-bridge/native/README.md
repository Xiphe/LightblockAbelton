# Native helper sources

These are the source-identical native inputs of the user-tested short/long Mode-button helper. Prefer the checkout-level [build and packaging command](../README.md), which verifies the complete pinned SDK, compiles both pad programs, runs the tests and includes supplier notices. It stages an app without launching or installing it.

For a native-only build with a configured Apple toolchain:

```sh
cmake -S tools/lightpad-bridge/native -B /tmp/lightpad-native-build \
  -DCMAKE_BUILD_TYPE=Release -DBLOCKS_SDK_DIR=/path/to/blocksctl/vendor/sdk
cmake --build /tmp/lightpad-native-build --parallel 4
ctest --test-dir /tmp/lightpad-native-build --output-on-failure
```

On this Mac, `../build.py` explicitly selects the installed Command Line Tools compiler, SDK and C++ includes, leaving global Xcode settings alone. The SDK is statically linked; no SDK files are modified. `Transport` derives from the exact pinned inline MIDI connection definition because the SDK makes three unchecked casts to that type. CMake fails if that definition changes.

The runtime CLI requires an exact serial and main source, with the modal source explicitly supplied:

```sh
lightpad-bridge --device EXACT_16_CHAR_ID \
  --source /absolute/main.littlefoot --modal-source /absolute/metronome.littlefoot \
  --status-file /absolute/status.json --lock-file /absolute/bridge.lock
```

Replace the serial placeholder with the device's 16-character ID. Normal packaged launches supply no automatic trial cycles. The helper owns SDK discovery and temporary uploads, the Mode button and modal permission handshake, stable virtual MIDI ports, framing, checksums and reconnection. The Python Live script still owns gestures, audio and Session state. The pad keeps local rendering and interpolation.

The feedback transport is protocol 18/A8. Whole typed state/clock/UI units become at most three Native events in one SDK packet. Raw inputs are forwarded independently of graphics acknowledgments. Modal entry requires fresh host permission on helper-only CC109; CC109 grants and CC108 denies with the same nonce. These permission messages never reach the device. Device model-ready CC108 markers are instrumentation, not optical timestamps.

Short release acts immediately. At 400 ms a held Mode button emits one reserved input-selection request; its release emits no short action. Hello/reconnect, program changes and detach cancel recognition. No input-selection screen is implemented in this baseline.

The four CTests cover protocol framing and codec vectors, topology/transport policy including save-default refusal, modal contact observation and previous click-model cases, and the active short/long recognizer. They do not open hardware or establish physical latency. The working sources were tested separately over USB and Bluetooth by the user. Broader power/sleep and long-duration reliability remain follow-up work.
