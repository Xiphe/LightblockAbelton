# Native helper

Use the [bridge guide](../README.md) for the pinned SDK, build, signing, installation, startup and rollback. `bridge/build.py` configures this CMake project and runs the native CTests before packaging all three device programs.

Native sources own connection discovery, virtual ports, program uploads, Mode recognition, host-health supervision and diagnostic notifications. Live owns musical state. See [architecture](../../ARCHITECTURE.md) for the runtime boundary and [validation](../../VALIDATION.md) for evidence and limits.

## Audio performance block

`--audio-performance` selects the matched local-interaction client and `live/PerformanceController` adapter. Pass the performance main/modal sources explicitly; legacy production and `--optimistic-fader-spike` remain independent paths. The [wire contract](performance_protocol.md) records the semantic frames and state ownership.

The main thread sends an admitted Live command before the next graphics packet. The reconciler retains one Native flight,16 discrete queued operations and coalesced parameter/fact slots. Complete terminal results have reserved bounded storage. A timeout reports uncertainty and never blindly replays recording. SDK feedback retry retains the result or marks the latest fact dirty; it never re-executes a Native command. Program swaps retain command state and reseed cached facts, while actual connection generations invalidate pending mutations.

Run CTest, then set `PERFORMANCE_REPLAY` to the built `performance_replay` executable when running `bridge/tests/test_audio_performance_path.py`. This joins the compiled Littlefoot client, actual C++ reconciler and Python backend with fake Native IO. It verifies the common execution path, not firmware latency or real audio capture.
