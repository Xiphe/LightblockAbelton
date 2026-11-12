#!/usr/bin/env python3
"""Build/test the pinned native helper and stage a new app. No runtime mutations."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
spec = importlib.util.spec_from_file_location("lightpad_package", HERE / "package.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


def verify_sdk(sdk):
    lock = json.loads((HERE / "sdk-lock.json").read_text())
    manifest_path = sdk.parent / "manifest.json"
    if not manifest_path.is_file() or packager.sha(manifest_path) != lock["vendor_manifest_sha256"]:
        raise ValueError("Use the blocksctl SDK checkout pinned in sdk-lock.json")
    manifest = json.loads(manifest_path.read_text())
    actual = {"sdk/" + str(p.relative_to(sdk)): packager.sha(p)
              for p in sdk.rglob("*") if p.is_file()}
    if actual != manifest["sha256"]:
        raise ValueError("SDK contents differ from the pinned vendor manifest")
    return lock


def notices(sdk, directory):
    """Retain upstream license texts alongside the locally staged binary."""
    blocksctl = sdk.parents[1]
    files = {
        "ROLI-ISC.txt": sdk / "roli_blocks_basics/LICENSE.txt",
        "GPL-3.0.txt": blocksctl / "LICENSES/GPL-3.0.txt",
        "libcxx.txt": blocksctl / "LICENSES/libcxx.txt",
        "THIRD_PARTY.md": blocksctl / "THIRD_PARTY.md",
        "zlib-README.txt": sdk / "juce_core/zip/zlib/README",
    }
    directory.mkdir(exist_ok=True)
    for name, source in files.items():
        shutil.copy2(source, directory / name)
    excerpts = [(module + "-ISC.txt", sdk / module / (module + ".h"))
                for module in ("juce_core", "juce_events", "juce_audio_basics", "juce_audio_devices")]
    excerpts += [(name + "-GPLv3.txt", sdk / "juce_audio_basics/utilities" / name)
                 for name in ("juce_GenericInterpolator.h", "juce_Interpolators.h",
                              "juce_Interpolators.cpp", "juce_WindowedSincInterpolator.cpp")]
    excerpts.append(("zlib-header.txt", sdk / "juce_core/zip/zlib/zlib.h"))
    for name, source in excerpts:
        text = source.read_text()
        start, end = text.index("/*"), text.index("*/") + 2
        (directory / name).write_text(text[start:end] + "\n")
    return sorted(directory.iterdir())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", required=True, type=Path, help="Pinned blocksctl/vendor/sdk")
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="New .app path")
    parser.add_argument("--device", required=True, help="Exact 16-character Lightpad serial")
    parser.add_argument("--compiler", default="blocksctl")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--status-program", type=Path, default=ROOT / "lightpad/status.littlefoot")
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise ValueError("The background helper currently builds for macOS only")
    sdk, build, output = (p.resolve() for p in (args.sdk, args.build_dir, args.output))
    if output.exists() or output.suffix != ".app":
        raise ValueError("Output must be a new .app path")
    lock = verify_sdk(sdk)
    command = ["cmake", "-S", str(HERE / "native"), "-B", str(build),
               "-DCMAKE_BUILD_TYPE=Release", "-DBLOCKS_SDK_DIR=" + str(sdk)]
    # Select the installed Command Line Tools explicitly when available. This
    # accepts no license terms and leaves the system toolchain settings alone.
    developer = Path(os.environ.get("DEVELOPER_DIR", "/Library/Developer/CommandLineTools"))
    clang = developer / "usr/bin/clang++"
    sysroot = developer / "SDKs/MacOSX.sdk"
    if clang.is_file() and sysroot.is_dir():
        includes = "-isystem " + str(sysroot / "usr/include/c++/v1")
        command += ["-DCMAKE_CXX_COMPILER=" + str(clang),
                    "-DCMAKE_OBJCXX_COMPILER=" + str(clang),
                    "-DCMAKE_OSX_SYSROOT=" + str(sysroot),
                    "-DCMAKE_CXX_FLAGS=" + includes, "-DCMAKE_OBJCXX_FLAGS=" + includes]
        make = developer / "usr/bin/make"
        if make.is_file():
            command.append("-DCMAKE_MAKE_PROGRAM=" + str(make))
    subprocess.run(command, check=True)
    subprocess.run(["cmake", "--build", str(build), "--parallel", str(args.jobs)], check=True)
    subprocess.run(["ctest", "--test-dir", str(build), "--output-on-failure"], check=True)
    compiler_version = subprocess.check_output([args.compiler, "--version"], text=True).strip()
    app, info = packager.package(
        build / "lightpad-bridge", ROOT / "lightpad/main.littlefoot",
        ROOT / "lightpad/metronome.littlefoot", args.device, output,
        notices(sdk, build / "package-notices"), args.compiler,
        build_metadata=dict(sdk=lock, compiler=compiler_version, native_type="Release"),
        status_program=args.status_program)
    print(json.dumps({"app": str(app), "input_binary_sha256": info["input_binary_sha256"],
                      "packaged_binary_sha256": packager.sha(app / "Contents/MacOS/lightpad-bridge"),
                      "programs": info["programs"]}, indent=2))


if __name__ == "__main__":
    main()
