# Lightpad Bridge

One background macOS helper connects the Lightpad to the Python Remote Script in Live. It owns the SDK connection, USB/Bluetooth discovery, temporary program uploads, virtual MIDI ports and local diagnostic notifications. Live owns clips, recording and mixer state. The pad renders and animates locally.

The signed production helper, Live script and login agent are installed with rollback retained, and login startup is activated. The combined idle/status startup passed the power-cycle/touch check. Two accepted USB takes, populated reopening and a fresh Set from the installed template passed. Bluetooth now passes recording, layering, mute, volume/pan and Mode switching from the installed template with USB unplugged. The user also confirmed visible diagnostic notifications after disabling Sleep Focus, with mirroring notifications still off. Core startup, USB, Bluetooth, template and notification checks have passed. See the [release contract](../../docs/integration/stable-release.md) for the agreed workflow and remaining physical checks.

## Everyday use after installation

Run the installed `Lightpad Bridge.app`, or use its approved login agent. Connect the configured standalone Lightpad by USB or Bluetooth MIDI. In Live, select `LightpadController`, with Input and Output both `Lightpad Bridge`. For a fresh project, open **Templates > Lightpad Remote** in Live's browser and save the new Untitled Set. Its installed file is `~/Music/Ableton/User Library/Templates/Lightpad Remote.als`; [the repository copy](../../templates/Lightpad%20Remote.als) contains eight marked groups with one empty child each, four returns and no clips/FileRefs. In another Set, name controller Group Tracks `[R1] Vocals`, `[R2] Guitar`, and so on. The production host discovers the loaded Set directly and has no private manifest/hash dependency.

If macOS has not connected Bluetooth MIDI, connect the Lightpad in Audio MIDI Setup > MIDI Studio > Bluetooth configuration. The helper discovers the OS connection; it does not initiate Bluetooth pairing.

A healthy Bluetooth connection stays active if USB is plugged in; USB is preferred when establishing a new connection. Do not run BLOCKS Code, Dashboard or another SDK uploader against the same pad simultaneously. Program changes preserve virtual ports and Live's music; they reset touch contacts and reseed graphics.

The login agent is installed and activated for startup and crash restart. New takes use Live's default Audio Tracks; audio input selection remains in Live. A recording gesture while transport is stopped silently does nothing.

For the single bridge path, the old direct BLOCKS control-surface slots 2 and 3 and raw USB Track/Remote input/output switches were disabled. The original machine's `Preferences.cfg` backup is local-only and is not part of a clone. Back up your own preferences before changing them. Restoring the legacy keyboard controller requires re-enabling its direct BLOCKS MIDI preferences as well as reinstalling the archived device program. Do not restore the whole preferences file while Live is open.

## Readiness and status

The helper distinguishes `program_loaded`, `device_model_ready`, `host_health` and `playable`. Upload completion alone is not usable Live state. Current faults are separate from the retained `last_error`, so a recovered old error does not keep an error screen active.

| Global screen | Display | Tap explanation |
| --- | --- | --- |
| Connecting/recovering or bridge unavailable | Accepted v9 elastic tunnel | Idle gestures affect the animation only. If it persists, check USB/Bluetooth and that the bridge runs. |
| Waiting for Live | White Ableton logo | Open Live and check the two Bridge ports. |
| No remote groups | Orange `[R]` | Add `[R1]` through `[R8]` prefixes to Group Track names. |
| Unexpected failure | Red `!` | Read the observed cause and corrective action. |

The accepted v9 tunnel and hidden-message behavior are integrated from idle commit `bb90f24`. Status states 0, 1 and 5 use that exact renderer; the Live, `[R]` and `!` fault icons remain unchanged. Idle touches never request diagnostic notifications. Ready host health still switches to main. The combined idle/status startup program replaced the saved yellow probe, and the user power-cycle/touch check passed. Main and metronome remain temporary programs, and the legacy navigation controller remains archived and reinstallable.

At cold connection the helper loads status and can seed it without Live. Fresh usable host health returns to main after contacts lift. A three-second health timeout normally opens status, but the last-known active recording prevents that timeout from replacing its recording screen. Mode is inactive during global status. Group-specific action failures produce a local flash; they do not send automatic notifications or open the global screen.

Host heartbeats remain active while pad programs upload. Bluetooth main-program uploads can take longer than the three-second health timeout, so device upload readiness must not gate this separate host lane. Queued heartbeats retain their MIDI receipt times; draining an old heartbeat cannot make an absent Live session appear fresh.

Only a tap on a fault icon requests a local Mac notification. The first explicit request may ask for alert permission. No sound, badge, automatic error notification or remote notification service is used. macOS Focus or notification settings may suppress banners. The same explanation is available on demand without opening MIDI:

```sh
'/path/to/Lightpad Bridge.app/Contents/MacOS/lightpad-bridge' --explain
'/path/to/Lightpad Bridge.app/Contents/MacOS/lightpad-bridge' --bundle-info
```

The native executable is `Contents/MacOS/lightpad-bridge` inside the fully signed app bundle, registered with Launch Services at its final installed path. The user enabled Lightpad Bridge notifications. Full-bundle signing fixed the earlier incomplete-identity authorization failure. Sleep Focus subsequently hid banners despite authorized requests and delivered records. After disabling Sleep Focus, the user confirmed visible notifications, including with mirroring notifications off. No additional notification code change was needed. Keep Focus and mirroring preferences as desired; when a requested banner is hidden, check those settings or use `--explain`.

## Program and wire contracts

Main/metronome graphics retain protocol 18/A8. The host sends helper-only health on channel 16 as five CCs: CC114 sequence, CC115 state, CC116 reason, CC117 flags and CC118 matching sequence. States are connecting 0, bridge-unavailable 1, Live-unavailable 2, no-groups 3, failure 4 and ready 5. Flags indicate recording-busy and safe-modal state. The host cannot declare a physical bridge failure; the helper/device determine it locally.

The status program receives `m0 = 0x53540000 | state | (reason << 8)`, `m1 = 0`, header `0xA8 | (3 << 8) | (8 << 10)`, every 500 ms. Its fault-icon CC107 diagnostic taps and CC106 held-contact count bypass Live's stale-gesture gate. Startup status does not require permission from an unreachable Live script. Metronome switches still require fresh Live permission and no held contacts or active recording/cleanup.

Short Mode release toggles main/metronome after contacts lift. A hold of at least 400 ms emits a reserved input-selection request; release after a hold does not also act as a short click. Input selection remains a placeholder.

## Build and stage offline

Requirements: macOS, Git, Python 3.9 or newer, CMake and Apple Command Line Tools. Install missing tools before building, for example `xcode-select --install` for Apple's tools and `brew install cmake` if you use Homebrew. BLOCKS Code, Dashboard and a ROLI account are not needed.

Run from a fresh controller checkout. The following creates a sibling `blocksctl` checkout at the revision in [sdk-lock.json](sdk-lock.json). Its 685-file SDK is vendored directly; there is no submodule or separate SDK download.

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
python3 tools/lightpad-bridge/build.py \
  --sdk ../blocksctl/vendor/sdk \
  --compiler ../blocksctl/build/blocksctl \
  --build-dir /tmp/lightpad-production-build \
  --device YOUR_16_CHARACTER_SERIAL \
  --output '/tmp/Lightpad Bridge.app'
```

Use new build/output paths if those paths already contain another build. The explicit compiler flags use the installed Command Line Tools without changing global Xcode selection. No pip packages are needed. Keep the whole pinned dependency checkout: the bridge also needs its vendor manifest and license notices, so a downloaded compiler or copied SDK folder alone is insufficient. Adding its build directory to this shell's PATH lets compiled-device tests find `blocksctl`. The bridge build checks the vendor manifest, all SDK files and the inline connection hash before compiling. The dependency's [build guide](https://github.com/Xiphe/blocksctl/blob/6612968f3651704870041a397bae466226d0d7af/docs/build.md) explains other supported toolchains.

Find the pad's exact 16-character serial with `../blocksctl/build/blocksctl devices --timeout 5` while other SDK owners are closed. Discovery accesses the device; compilation itself is offline. Use that serial and a new output path. The command builds Release, runs five native CTests, compiles all three programs, enforces each 7,200-byte budget and packages supplier notices. The separate status source defaults to `prototypes/controller/lightpad/status.littlefoot`; `--status-program SOURCE` selects an alternative reviewed asset. Packaging copies `idle-animation.littlefoot` and `idle-statements-prototype.littlefoot` beside it and records their hashes in build information. `package.py --help` describes staging from an existing native binary.

Quoted relative `.littlefoot` includes are copied recursively into the bundle and recorded with individual hashes in `build-info.json`. Packaging compiles those isolated copies rather than relying on the source checkout. Includes must stay within their entry source directory; absolute paths, parent traversal, symlinks, cycles and conflicting destinations are rejected. Shared includes with identical content are copied once.

Packaging signs the completed app bundle with a local ad-hoc signature and verifies its seal. The linker signature alone leaves `Info.plist` unbound. `build-info.json` records `input_binary_sha256` for the native build input; CLI output separately reports `packaged_binary_sha256` after signing. Installation-plan hashes cover the complete signed candidate. Do not edit bundled files after signing.

Build/staging opens no MIDI, launches no app and performs no installation, firmware or saved-default write. Running the staged app is a separate action.

Current checked program budgets:

| Program | Code + heap | Free |
| --- | ---: | ---: |
| Main | 7,106 | 94 |
| Metronome | 5,331 | 1,869 |
| Status + idle animation | 5,320 | 1,880 |

## Matched installation and rollback

The current installed baseline passed startup, USB/Bluetooth recording, template reopening and visible diagnostic notifications. [The tracked validation summary](../../docs/integration/validation-summary.md) records that evidence. On another machine, perform the steps below. Each installation writes its own receipt and retains replaced files for rollback; the original machine's private receipts are not clone dependencies.

Prepare a plan from the build above without changing installed files or login settings. Run from the controller checkout and choose a new plan directory if this one already exists:

```sh
python3 tools/lightpad-bridge/install.py stage \
  --app '/tmp/Lightpad Bridge.app' \
  --script "$PWD/prototypes/controller/live/LightpadController" \
  --output /tmp/lightpad-install-plan
```

The plan contains the app, complete production Live script and optional login agent. It verifies the native app identity, status program, matching health support and `LIVE_STATE_MODE = True`, and rejects the prototype's private experiment paths. Default targets are `~/Applications/Lightpad Bridge.app`, `~/Music/Ableton/User Library/Remote Scripts/LightpadController`, and `~/Library/LaunchAgents/net.xiphe.lightpad-bridge.plist`. Use `--no-login` when deliberately staging without login startup.

Install the saved startup separately while Live, Lightpad Bridge, BLOCKS Code and Dashboard are closed. If the bridge login agent is already loaded, unload it before taking the SDK connection:

```sh
launchctl bootout "gui/$(id -u)/net.xiphe.lightpad-bridge"
```

For a first installation with no agent loaded, skip that command. Then run from the controller checkout:

```sh
../blocksctl/build/blocksctl save-default prototypes/controller/lightpad/status.littlefoot \
  --device YOUR_16_CHARACTER_SERIAL --timeout 30
```

Keep `idle-animation.littlefoot` and `idle-statements-prototype.littlefoot` beside `status.littlefoot`; the compiler resolves those includes. This writes the startup program, not firmware. A successful command means the save was requested, so power-cycle the pad and confirm the idle/touch behavior before continuing. Temporary main/metronome uploads do not replace this saved startup. [Legacy restoration](../../docs/integration/legacy-restore.md) gives the old-program alternative.

Apply only after Live and the running helper are closed:

```sh
python3 tools/lightpad-bridge/install.py apply /tmp/lightpad-install-plan/install-plan.json
```

For a first installation, leave `--activate-login` off so you can register the final bundle before starting it. Apply writes the default login-agent file without loading it. For an already registered installation, `--activate-login` can explicitly start the helper during apply. Login startup uses RunAtLoad, restarts failed exits and throttles restart attempts.

Register the completed signed app at its final installed path so macOS can identify its notification requests. With the default installation target:

```sh
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -f "$HOME/Applications/Lightpad Bridge.app"
```

Use your chosen app target if you staged another location. If you staged the default login agent, load it after registration:

```sh
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/net.xiphe.lightpad-bridge.plist"
```

This starts the helper and enables login startup. With `--no-login`, open the installed app manually instead. Allow Lightpad Bridge notifications when requested, and check Focus if an explicit diagnostic tap produces no banner. The `--explain` command remains available without a notification banner.

Apply verifies staged/current hashes, prepares all copies before replacing any root and retains a receipt plus replaced files under `~/Library/Application Support/Lightpad Bridge/revisions/`. Rollback also requires Live/helper stopped and refuses changed files:

```sh
python3 tools/lightpad-bridge/install.py rollback /absolute/revisions/REVISION/receipt.json
```

Rollback preserves the newer files and reports when an earlier login agent needs reloading. Neither apply nor rollback changes Live MIDI/audio preferences, writes the saved startup program, saves recordings or flashes firmware. The separate startup-status installation and user power-cycle check have now completed.

## Checks and evidence limits

```sh
python3 -m unittest discover -s tools/lightpad-bridge -p 'test_*.py' -v
python3 verification-tools/test_status_device.py
python3 verification-tools/test_status_idle.py
```

The native suite has five passing CTests, packaging/install has 21 passing tests, and status has twelve checks plus five differential v9 comparisons. The [tracked validation summary](../../docs/integration/validation-summary.md) records all 842 offline checks and bounded hardware results. Your build generates hashes in `Contents/Resources/build-info.json`; your install generates a rollback receipt. The former urgent-feedback pacing failure is now a strict passing regression at the unchanged four-frame-per-90-ms limit. Offline logic checks do not measure physical latency or audio.

Checkpoint `35e2a8d` passed bounded USB/Bluetooth trials, and `1d31013` added the accepted metronome UI. Their [working baseline](../../docs/integration/working-baseline.md) and [hashes](../../prototypes/controller/live/baseline-sha256.json) remain historical. They do not validate this production engine, health lane, notification system, installer, sleep/wake or long-duration workloads.
