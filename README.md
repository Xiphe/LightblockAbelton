# Original Lightpad controller, 0.x

This branch preserves the custom ROLI Lightpad program used by older Ableton performances. It sends navigation and recording-control MIDI commands and displays arm/open-close feedback. The current loop-station controller lives on `main`.

The Max for Live companion, Dynamic Navigation Controller by Nick Hyde, is separately obtained commercial software and is not included. Restore your own copy for Sets that use it. Keep the original Live Set, MIDI mappings, routing and audio dependencies separately too.

## Preserved files

- `looping-block.littlefoot` is the self-contained pad program, including the last working layout and recording-control edits.
- `looping-block.lfproject` is the original BLOCKS Code project, preserved byte for byte.
- `SHA256SUMS` verifies these two files.

Run `shasum -a 256 -c SHA256SUMS` from this checkout before restoring. The editor project contains historical absolute paths and a missing `reference.littlefoot` entry. Open the standalone program directly or relink that metadata; the program itself needs no includes.

## Restore an older performance

1. Stop Lightpad Bridge and its login agent, then close Live and other SDK owners such as BLOCKS Code and Dashboard before uploading. Only one SDK application should own the pad.
2. With the source-built [blocksctl](https://github.com/Xiphe/blocksctl) available, identify your pad and save the original program:

   ```sh
   blocksctl devices --timeout 5
   blocksctl save-default looping-block.littlefoot \
     --device YOUR_16_CHARACTER_SERIAL --timeout 30
   ```

3. Power-cycle the Lightpad and confirm the navigation layout. In a copy of the original Live Set, restore its companion device and mappings. Re-enable the direct BLOCKS control surfaces and raw Lightpad MIDI Track/Remote routes required by that Set. Restart Live after changing control surfaces, then check navigation, arm, recording, stop and feedback before using the original performance Set.

Saving replaces the pad's startup program. A successful upload does not by itself verify persistence or Live mappings. To return to the current controller, follow the setup guide on `main` and restore its startup program and Bridge MIDI routes.
