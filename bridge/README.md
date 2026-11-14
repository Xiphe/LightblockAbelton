# Lightpad Bridge

The macOS helper owns one Lightpad SDK connection, USB/Bluetooth discovery, temporary program uploads, stable virtual MIDI ports and diagnostic notifications. Live owns musical state; the pad draws locally. See [architecture](../ARCHITECTURE.md) and [validation](../VALIDATION.md).

## Build

Requirements: macOS, Git, Python 3.9 or newer, CMake and Apple Command Line Tools. No pip packages, ROLI apps or ROLI account are needed. Install missing tools first, for example `xcode-select --install` and, if you use Homebrew, `brew install cmake`.

Run from a fresh controller checkout. [sdk-lock.json](sdk-lock.json) pins the whole external `blocksctl` repository, whose SDK is vendored directly with no submodule download:

```sh
git clone https://github.com/Xiphe/blocksctl.git ../blocksctl
git -C ../blocksctl checkout 6612968f3651704870041a397bae466226d0d7af
python3 ../blocksctl/scripts/verify_vendor.py
cmake -S ../blocksctl -B ../blocksctl/build -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER=/Library/Developer/CommandLineTools/usr/bin/clang++ \
  -DCMAKE_OBJCXX_COMPILER=/Library/Developer/CommandLineTools/usr/bin/clang++ \
  -DCMAKE_OSX_SYSROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk \
  '-DCMAKE_CXX_FLAGS=-isystem /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include/c++/v1' \
  '-DCMAKE_OBJCXX_FLAGS=-isystem /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include/c++/v1' \
  -DCMAKE_MAKE_PROGRAM=/Library/Developer/CommandLineTools/usr/bin/make
cmake --build ../blocksctl/build --parallel 4
ctest --test-dir ../blocksctl/build --output-on-failure
export PATH="$PWD/../blocksctl/build:$PATH"
```

The explicit flags use the installed Command Line Tools without changing global Xcode selection. Keep the whole dependency checkout: building the bridge also needs `vendor/manifest.json` and license notices. A compiler binary or copied SDK folder alone is insufficient. PATH lets device tests find `blocksctl`; the [dependency build guide](https://github.com/Xiphe/blocksctl/blob/6612968f3651704870041a397bae466226d0d7af/docs/build.md) covers other toolchains.

With Lightpad Bridge, BLOCKS Code, Dashboard and other SDK owners closed, find the pad's exact 16-character serial:

```sh
../blocksctl/build/blocksctl devices --timeout 5
```

Discovery accesses the device. The following build itself is offline; replace the serial and use new build/output paths:

```sh
python3 bridge/build.py \
  --sdk ../blocksctl/vendor/sdk \
  --compiler ../blocksctl/build/blocksctl \
  --build-dir /tmp/lightpad-production-build \
  --device YOUR_16_CHARACTER_SERIAL \
  --output '/tmp/Lightpad Bridge.app'
```

The build verifies all 685 vendor files and the inline connection hash, runs five native CTests, compiles all four pad programs, enforces their individual 7,200-byte budgets and packages supplier notices. `--status-program SOURCE` selects another reviewed status entry point; the default is `lightpad/status.littlefoot`.

Packaging resolves quoted relative Littlefoot includes, copies them into the bundle and records their hashes. Includes must stay within their source directory; absolute paths, parent traversal, symlinks, cycles and conflicting destinations are rejected. Status needs both `idle-animation.littlefoot` and `idle-statements-prototype.littlefoot`.

The completed app receives a local ad-hoc signature and strict seal verification. Do not edit bundled files afterward. `Contents/Resources/build-info.json` records source, include, SDK/compiler and native input hashes; packaging reports the post-signing executable hash separately. Build/staging starts no app, opens no MIDI and writes no firmware or saved startup program.

## Install

Close Live and the running helper before applying or rolling back. If the helper login agent is loaded, unload it first; skip this on a first installation:

```sh
launchctl bootout "gui/$(id -u)/net.xiphe.lightpad-bridge"
```

Prepare a new installation plan from the checkout root. Staging changes neither installed files nor login settings:

```sh
python3 bridge/install.py stage \
  --app '/tmp/Lightpad Bridge.app' \
  --script "$PWD/live/LightpadController" \
  --output /tmp/lightpad-install-plan
```

The plan validates the signed app identity, all four programs, matching host health support and normal Live-state configuration. Defaults are `~/Applications/Lightpad Bridge.app`, `~/Music/Ableton/User Library/Remote Scripts/LightpadController` and `~/Library/LaunchAgents/net.xiphe.lightpad-bridge.plist`. Use a new plan directory if one exists. `--no-login` omits the login agent; `install.py stage --help` lists custom destinations.

Save the pad's standalone startup while all SDK owners are closed:

```sh
../blocksctl/build/blocksctl save-default lightpad/status.littlefoot \
  --device YOUR_16_CHARACTER_SERIAL --timeout 30
```

Keep the two idle includes beside status. This replaces the saved startup program, not firmware. CLI success means the save was requested; fully power-cycle the pad and confirm idle/touch behavior before continuing. Main, patch selector and metronome are temporary uploads and leave this saved startup intact.

Apply the matched app/script/login files:

```sh
python3 bridge/install.py apply /tmp/lightpad-install-plan/install-plan.json
```

For first installation, leave `--activate-login` off until the app is registered at its final location:

```sh
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -f "$HOME/Applications/Lightpad Bridge.app"
```

Use your chosen app target if different. Then load the default login agent:

```sh
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/net.xiphe.lightpad-bridge.plist"
```

This starts the helper and enables login startup with failed-exit restart. With `--no-login`, open the installed app manually. For an already registered installation, `apply --activate-login` can start the agent during apply.

Restart Live, select the two Bridge ports, install the template and choose audio defaults as described in [Live setup](../live/README.md). The installer does not change Live preferences, install templates, save recordings or write the pad startup.

## Connect and diagnose

A healthy Bluetooth connection stays active when USB is plugged in; USB is preferred when establishing a new connection. If macOS has not connected Bluetooth MIDI, connect the pad in Audio MIDI Setup > MIDI Studio > Bluetooth configuration. The helper discovers OS connections and does not perform pairing. Keep other SDK uploaders closed.

| Display | Action |
| --- | --- |
| Touchable idle tunnel | Wait for loading; if it persists, check the physical connection and helper |
| White Ableton logo | Open Live and check the control surface's Bridge input/output |
| Orange `[R]` | Add a valid `[R1]` through `[R8]` prefix to a Group Track |
| Red `!` | Tap for the observed problem and next action |

Only fault-icon taps request a local Mac notification. Idle touches affect the animation, and Mode is inactive on status. Allow Lightpad Bridge notifications when requested; Focus can suppress a banner. Keep your mirroring/sharing preferences as desired. An explanation is also available without MIDI:

```sh
"$HOME/Applications/Lightpad Bridge.app/Contents/MacOS/lightpad-bridge" --explain
"$HOME/Applications/Lightpad Bridge.app/Contents/MacOS/lightpad-bridge" --bundle-info
```

Fresh usable host health returns to main after contacts lift. A three-second health timeout normally shows status, while a last-known active recording protects its recording screen. Host health continues during device uploads with original receipt timestamps. Program-loaded, fresh device model and playable Live state are separate observations.

## Roll back

Apply verifies staged/current hashes, prepares all copies before replacing roots, and retains replaced files plus a receipt under `~/Library/Application Support/Lightpad Bridge/revisions/`. With Live/helper closed and the agent unloaded, use the receipt from your own installation:

```sh
python3 bridge/install.py rollback /absolute/revisions/REVISION/receipt.json
```

Rollback refuses modified installed files, preserves the newer files and reports when an earlier agent needs reloading. It does not restore Live preferences or the saved pad startup; those are separate choices. Never replace Live's whole preferences file while Live is open.

## Tests

```sh
python3 -m unittest discover -s bridge/tests -p 'test_*.py' -v
```

`build.py` runs the five native CTests. [Device checks](../lightpad/README.md#tests) exercise compiled programs without MIDI or hardware. The [validation summary](../VALIDATION.md) distinguishes offline coverage from bounded physical trials.
