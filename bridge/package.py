#!/usr/bin/env python3
"""Compile a matched set of pad programs and stage an app; never install/start it."""
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
BUNDLE_ID = "net.xiphe.lightpad-bridge"
INCLUDE = re.compile(r'^\s*#include\s+"([^"\n]+)"\s*(?://[^\n]*)?$', re.MULTILINE)



def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sign_bundle(app):
    # Signing only the linker output leaves Info.plist unbound and UN authorization fails.
    # Seal the completed bundle, then leave every bundled file unchanged.
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--identifier", BUNDLE_ID, str(app)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)],
                   check=True, capture_output=True, text=True)


def source_files(source, entry_name):
    """Collect a confined relative include tree before compiling packaged copies."""
    source = source.resolve()
    root = source.parent
    files = {entry_name: source}
    visiting = set()
    visited = set()

    def visit(path):
        if path in visiting:
            raise ValueError("Cyclic Littlefoot include: " + path.name)
        if path in visited:
            return
        visiting.add(path)
        text = path.read_text()
        # Remove comments before recognizing directives; no include in a comment is a dependency.
        text = re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.DOTALL)
        matches = list(INCLUDE.finditer(text))
        if len(matches) != len(re.findall(r'^\s*#include\b', text, re.MULTILINE)):
            raise ValueError("Only quoted relative Littlefoot includes are supported: " + path.name)
        for match in matches:
            name = match.group(1)
            relative = Path(name)
            if (relative.is_absolute() or '..' in relative.parts or '\\' in name
                    or relative.suffix != '.littlefoot'):
                raise ValueError("Include must stay inside its source tree: " + name)
            # The SDK searches the entry directory before directories of earlier includes.
            candidates = [root / relative, path.parent / relative]
            available = []
            for candidate in candidates:
                if candidate.is_file():
                    if candidate.resolve() != candidate or not candidate.is_relative_to(root):
                        raise ValueError("Symlink or escaping Littlefoot include: " + name)
                    if candidate not in available:
                        available.append(candidate)
            if not available:
                raise ValueError("Missing Littlefoot include: " + name)
            if len(available) > 1:
                raise ValueError("Ambiguous Littlefoot include: " + name)
            dependency = available[0]
            destination = dependency.relative_to(root).as_posix()
            if destination in files and files[destination] != dependency:
                raise ValueError("Conflicting Littlefoot include destination: " + destination)
            files[destination] = dependency
            visit(dependency)
        visiting.remove(path)
        visited.add(path)

    visit(source)
    return files


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
            compiler="blocksctl", build_metadata=None, status_program=None,
            audio_performance=False, patch_program=None):
    binary, controller, metronome, output = (
        Path(p).resolve() for p in (binary, controller, metronome, output))
    notices = [Path(p).resolve() for p in notices]
    status_program = Path(status_program).resolve() if status_program is not None else None
    patch_program = Path(patch_program).resolve() if patch_program is not None else None
    if audio_performance and (patch_program is None or status_program is None):
        raise ValueError("Audio performance requires both patch and status programs")
    if patch_program is not None and not audio_performance:
        raise ValueError("Patch program requires Audio performance mode")
    if patch_program is not None and not patch_program.is_file():
        raise ValueError("Patch program must exist")
    if status_program is not None and not status_program.is_file():
        raise ValueError("Status program must exist")
    if not all(p.is_file() for p in (binary, controller, metronome)):
        raise ValueError("Native binary and both device sources must exist")
    if not re.fullmatch(r"[A-Za-z0-9]{16}", device):
        raise ValueError("Explicit 16-character device serial required")
    if output.suffix != ".app" or output.exists():
        raise ValueError("Output must be a new .app path")
    if len({p.name for p in notices}) != len(notices) or any(not p.is_file() for p in notices):
        raise ValueError("Notice files must exist and have unique filenames")

    entries = {"main": (controller, "controller.littlefoot"),
               "metronome": (metronome, "metronome.littlefoot")}
    if patch_program is not None:
        entries["patch"] = (patch_program, "patch.littlefoot")
    if status_program is not None:
        entries["status"] = (status_program, "status.littlefoot")
    trees = {name: source_files(path, entry) for name, (path, entry) in entries.items()}
    sources = {}
    for files in trees.values():
        for destination, source in files.items():
            if destination in sources and sources[destination].read_bytes() != source.read_bytes():
                raise ValueError("Conflicting Littlefoot include destination: " + destination)
            sources[destination] = source
    source_data = {destination: source.read_bytes() for destination, source in sources.items()}
    # Compile isolated packaged copies, so source includes cannot depend on the checkout.
    # This never discovers hardware or MIDI and leaves no partial app on compile failure.
    with tempfile.TemporaryDirectory(prefix="lightpad-package-") as scratch:
        source_root = Path(scratch) / "sources"
        for destination, data in source_data.items():
            target = source_root / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        programs = {}
        for name, (_, entry) in entries.items():
            programs[name] = compile_program(source_root / entry, Path(scratch) / (name + ".bin"), compiler)
            programs[name]["includes_sha256"] = {
                destination: sha(source_root / destination)
                for destination in sorted(trees[name]) if destination != entry}
    manifest = dict(
        baseline=("Optimistic Audio/MIDI controller with Constants mixers" if audio_performance
                  else "Live-authoritative controller with global connection status"),
        controller_mode="audio-performance" if audio_performance else "legacy",
        protocol="performance/F2" if audio_performance else "18/A8",
        input_binary_sha256=sha(binary), programs=programs,
        code_signing=dict(mode="ad-hoc bundle", identifier=BUNDLE_ID),
        automatic_login_start=False, saved_default_writes=False,
        firmware_writes=False,
        native_sources={p.name: sha(p) for p in sorted(
            [p for p in (HERE / "native").iterdir() if p.is_file()]
            + list((HERE / "tests").glob("*_test.cpp")))},
    )
    if audio_performance:
        manifest['performance_contract'] = 2
    if build_metadata is not None:
        manifest["build"] = build_metadata
    resources = output / "Contents/Resources"
    resources.mkdir(parents=True)
    executable = output / "Contents/MacOS/lightpad-bridge"
    executable.parent.mkdir()
    shutil.copy2(binary, executable)
    executable.chmod(0o755)
    for destination, data in source_data.items():
        target = resources / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    plist = dict(CFBundleName="Lightpad Bridge", CFBundleDisplayName="Lightpad Bridge",
                 CFBundleIdentifier=BUNDLE_ID,
                 CFBundleExecutable="lightpad-bridge", CFBundlePackageType="APPL",
                 CFBundleShortVersionString="0.2.0", CFBundleVersion="2",
                 LSUIElement=True, LSMultipleInstancesProhibited=True,
                 LightpadBridgeDeviceSerial=device,
                 LightpadBridgeControllerMode=manifest["controller_mode"])
    (output / "Contents/Info.plist").write_bytes(plistlib.dumps(plist))
    if notices:
        notice_root = resources / "notices"
        notice_root.mkdir()
        for path in notices:
            shutil.copy2(path, notice_root / path.name)
    (resources / "build-info.json").write_text(json.dumps(manifest, indent=2) + "\n")
    try:
        sign_bundle(output)
    except BaseException:
        # A failed signature must not leave an apparently finished candidate.
        shutil.rmtree(output)
        raise
    return output, manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--controller", required=True, type=Path)
    parser.add_argument("--metronome", required=True, type=Path)
    parser.add_argument("--device", required=True)
    parser.add_argument("--status-program", type=Path)
    parser.add_argument("--audio-performance", action="store_true")
    parser.add_argument("--patch-program", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--compiler", default="blocksctl")
    parser.add_argument("--notice", action="append", type=Path, default=[])
    args = parser.parse_args()
    app, info = package(args.binary, args.controller, args.metronome, args.device,
                        args.output, args.notice, args.compiler, status_program=args.status_program,
                        audio_performance=args.audio_performance, patch_program=args.patch_program)
    print(json.dumps({"app": str(app), "packaged_binary_sha256": sha(app / "Contents/MacOS/lightpad-bridge"), **info}))
