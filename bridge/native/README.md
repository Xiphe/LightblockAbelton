# Native helper

Use the [bridge guide](../README.md) for the pinned SDK, build, signing, installation, startup and rollback. `bridge/build.py` configures this CMake project and runs the five native CTests before packaging all three device programs.

Native sources own connection discovery, virtual ports, program uploads, Mode recognition, host-health supervision and diagnostic notifications. Live owns musical state. See [architecture](../../ARCHITECTURE.md) for the runtime boundary and [validation](../../VALIDATION.md) for evidence and limits.
