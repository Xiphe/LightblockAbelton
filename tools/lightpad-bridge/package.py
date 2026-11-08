#!/usr/bin/env python3
"""Compile both pad programs and stage a new background app; never install/start it."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import shutil
import struct
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
DEVICE_BUDGET = 7200
LAUNCHER = '''#!/bin/zsh
set -eu
bundle_root="${0:A:h:h}"
support_root="$HOME/Library/Application Support/Lightpad Bridge"
log_root="$HOME/Library/Logs/Lightpad Bridge"
mkdir -p "$support_root" "$log_root"
device_serial=$(/usr/bin/plutil -extract LightpadBridgeDeviceSerial raw -o - "$bundle_root/Info.plist")
exec "$bundle_root/Resources/lightpad-bridge" --device "$device_serial" --source "$bundle_root/Resources/controller.littlefoot" --modal-source "$bundle_root/Resources/metronome.littlefoot" --status-file "$support_root/status.json" --lock-file "$support_root/bridge.lock" >>"$log_root/bridge.log" 2>&1
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compile_program(source, binary, compiler="blocksctl"):
    subprocess.run([str(compiler), "compile", str(source), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    data = binary.read_bytes()
    if len(data) < 10:
        raise ValueError("Invalid compiled program header: " + source.name)
    _, code, _, globals_count, heap = struct.unpack_from("<5H", data)
    if code != len(data) or code + heap > DEVICE_BUDGET:
        raise ValueError("Program exceeds device budget or has an invalid size: " + source.name)
    return dict(code=code, heap=heap, total=code + heap,
                free=DEVICE_BUDGET - code - heap, globals_bytes=globals_count * 4,
                source_sha256=sha(source), binary_sha256=sha(binary))


def package(binary, controller, metronome, device, output, notices=(),
            compiler="blocksctl", build_metadata=None):
    binary, controller, metronome, output = (
        Path(p).resolve() for p in (binary, controller, metronome, output))
    notices = [Path(p).resolve() for p in notices]
    if not all(p.is_file() for p in (binary, controller, metronome)):
        raise ValueError("Native binary and both device sources must exist")
    if not re.fullmatch(r"[A-Za-z0-9]{16}", device):
        raise ValueError("Explicit 16-character device serial required")
    if output.suffix != ".app" or output.exists():
        raise ValueError("Output must be a new .app path")
    if len({p.name for p in notices}) != len(notices) or any(not p.is_file() for p in notices):
        raise ValueError("Notice files must exist and have unique filenames")

    # Compile before creating the app. This never discovers hardware or MIDI.
    with tempfile.TemporaryDirectory(prefix="lightpad-package-") as scratch:
        programs = {
            "main": compile_program(controller, Path(scratch) / "main.bin", compiler),
            "metronome": compile_program(metronome, Path(scratch) / "metronome.bin", compiler),
        }
    manifest = dict(
        baseline="side-button temporary modals, short release / 400 ms hold",
        protocol="18/A8", binary_sha256=sha(binary), programs=programs,
        automatic_login_start=False, saved_default_writes=False,
        firmware_writes=False,
        native_sources={p.name: sha(p) for p in sorted((HERE / "native").iterdir()) if p.is_file()},
    )
    if build_metadata is not None:
        manifest["build"] = build_metadata
    resources = output / "Contents/Resources"
    resources.mkdir(parents=True)
    executable = output / "Contents/MacOS/Lightpad Bridge"
    executable.parent.mkdir()
    executable.write_text(LAUNCHER)
    executable.chmod(0o755)
    shutil.copy2(binary, resources / "lightpad-bridge")
    (resources / "lightpad-bridge").chmod(0o755)
    shutil.copy2(controller, resources / "controller.littlefoot")
    shutil.copy2(metronome, resources / "metronome.littlefoot")
    plist = dict(CFBundleName="Lightpad Bridge", CFBundleDisplayName="Lightpad Bridge",
                 CFBundleIdentifier="net.xiphe.lightpad-bridge",
                 CFBundleExecutable="Lightpad Bridge", CFBundlePackageType="APPL",
                 CFBundleShortVersionString="0.1.0", CFBundleVersion="1",
                 LSUIElement=True, LSMultipleInstancesProhibited=True,
                 LightpadBridgeDeviceSerial=device)
    (output / "Contents/Info.plist").write_bytes(plistlib.dumps(plist))
    if notices:
        notice_root = resources / "notices"
        notice_root.mkdir()
        for path in notices:
            shutil.copy2(path, notice_root / path.name)
    (resources / "build-info.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return output, manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--controller", required=True, type=Path)
    parser.add_argument("--metronome", required=True, type=Path)
    parser.add_argument("--device", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--compiler", default="blocksctl")
    parser.add_argument("--notice", action="append", type=Path, default=[])
    args = parser.parse_args()
    app, info = package(args.binary, args.controller, args.metronome, args.device,
                        args.output, args.notice, args.compiler)
    print(json.dumps({"app": str(app), **info}))
