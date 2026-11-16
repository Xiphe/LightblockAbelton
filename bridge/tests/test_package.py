"""Package the real compiled pad sources offline; never launch the staged app."""
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location("lightpad_package", HERE.parent / "package.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="lightpad-package-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.binary = self.root / "helper"
        self.binary.write_bytes(b"staging-test-only-not-executable")
        self.controller = ROOT / "lightpad/main.littlefoot"
        self.metronome = ROOT / "lightpad/metronome.littlefoot"
        # Most packaging tests use a non-executable byte fixture. Real bundle signing
        # and its identity seal are verified separately with an actual Mach-O below.
        self.signer_patch = patch.object(package, "sign_bundle")
        self.signer = self.signer_patch.start()
        self.addCleanup(self.signer_patch.stop)

    @unittest.skipUnless(shutil.which("blocksctl"), "offline blocksctl compiler required")
    def test_package_contains_both_exact_sources_and_modal_launcher(self):
        target, manifest = package.package(
            self.binary, self.controller, self.metronome, "TESTSERIAL000001",
            self.root / "Lightpad Bridge.app")
        resources = target / "Contents/Resources"
        self.assertEqual((resources / "controller.littlefoot").read_bytes(), self.controller.read_bytes())
        self.assertEqual((resources / "metronome.littlefoot").read_bytes(), self.metronome.read_bytes())
        executable=target / "Contents/MacOS/lightpad-bridge"
        self.assertEqual(executable.read_bytes(), self.binary.read_bytes())
        self.assertTrue(executable.stat().st_mode & 0o111)
        self.assertFalse((resources / "lightpad-bridge").exists())
        self.assertEqual(plistlib.loads((target / "Contents/Info.plist").read_bytes())["CFBundleExecutable"], executable.name)
        self.assertEqual(plistlib.loads((target / "Contents/Info.plist").read_bytes())[
            "LightpadBridgeDeviceSerial"], "TESTSERIAL000001")
        for name in ("main", "metronome"):
            self.assertLessEqual(manifest["programs"][name]["total"], package.DEVICE_BUDGET)
            self.assertEqual(manifest["programs"][name]["free"], package.DEVICE_BUDGET-manifest["programs"][name]["total"])
        self.assertFalse(manifest["saved_default_writes"])
        self.assertFalse(manifest["automatic_login_start"])
        self.assertEqual(manifest["input_binary_sha256"], package.sha(self.binary))
        self.assertEqual(manifest["code_signing"]["identifier"], package.BUNDLE_ID)
        self.signer.assert_called_once_with(target)
        self.assertEqual(json.loads((resources / "build-info.json").read_text()), manifest)
        self.assertNotIn(str(ROOT), (resources / "build-info.json").read_text())
        with self.assertRaises(ValueError):
            package.package(self.binary, self.controller, self.metronome,
                            "TESTSERIAL000001", target)

    @unittest.skipUnless(shutil.which("blocksctl"), "offline blocksctl compiler required")
    def test_separate_status_program_is_compiled_and_packaged(self):
        status=ROOT / "lightpad/status.littlefoot"
        self.assertTrue(status.is_file(), "canonical status renderer required for production package")
        target,manifest=package.package(self.binary,self.controller,self.metronome,"TESTSERIAL000001",
            self.root / "Status.app",status_program=status)
        self.assertEqual((target / "Contents/Resources/status.littlefoot").read_bytes(),status.read_bytes())
        self.assertEqual(manifest["programs"]["status"]["source_sha256"],package.sha(status))
        for name, digest in manifest["programs"]["status"]["includes_sha256"].items():
            self.assertEqual(package.sha(target / "Contents/Resources" / name), digest)
        # Recompiling only the bundle resources must work without source checkout paths.
        isolated = self.root / "isolated-resources"
        shutil.copytree(target / "Contents/Resources", isolated)
        result = package.compile_program(isolated / "status.littlefoot", self.root / "status.bin")
        self.assertEqual(result["binary_sha256"], manifest["programs"]["status"]["binary_sha256"])
        self.assertLessEqual(manifest["programs"]["status"]["total"],package.DEVICE_BUDGET)
        self.assertFalse(manifest["saved_default_writes"])

    @unittest.skipUnless(shutil.which("blocksctl"), "offline blocksctl compiler required")
    def test_audio_mode_packages_four_matched_programs_without_checkout_dependency(self):
        lightpad = ROOT / "lightpad"
        target, manifest = package.package(
            self.binary, lightpad / "performance-main.littlefoot",
            lightpad / "performance-metronome.littlefoot", "TESTSERIAL000001",
            self.root / "Audio.app", audio_performance=True,
            patch_program=lightpad / "performance-patch.littlefoot",
            status_program=lightpad / "status.littlefoot")
        self.assertEqual(manifest["controller_mode"], "audio-performance")
        self.assertEqual(set(manifest["programs"]), {"main", "metronome", "patch", "status"})
        info = plistlib.loads((target / "Contents/Info.plist").read_bytes())
        self.assertEqual(info["LightpadBridgeControllerMode"], manifest["controller_mode"])
        resources = self.root / "isolated-audio"
        shutil.copytree(target / "Contents/Resources", resources)
        for key, entry in (("main", "controller"), ("metronome", "metronome"),
                           ("patch", "patch"), ("status", "status")):
            result = package.compile_program(resources / (entry + ".littlefoot"),
                                             self.root / (key + ".bin"))
            self.assertEqual(result["binary_sha256"], manifest["programs"][key]["binary_sha256"])
            self.assertLessEqual(result["total"], package.DEVICE_BUDGET)
        self.assertNotIn(str(ROOT), json.dumps(manifest))

    def test_audio_mode_requires_matching_patch_and_status_before_creating_app(self):
        target = self.root / "Never.app"
        for kwargs in ({"audio_performance": True},
                       {"audio_performance": True, "patch_program": self.controller},
                       {"patch_program": self.controller},
                       {"audio_performance": True, "patch_program": self.root / "missing",
                        "status_program": self.controller}):
            with self.assertRaises(ValueError):
                package.package(self.binary, self.controller, self.metronome,
                                "TESTSERIAL000001", target, **kwargs)
            self.assertFalse(target.exists())

    @unittest.skipUnless(os.environ.get("LIGHTPAD_BRIDGE_BINARY") and shutil.which("blocksctl"),
                         "built bridge binary and offline compiler required")
    def test_signed_app_starts_with_matched_audio_defaults_without_midi(self):
        # --bundle-info exits before GUI initialization, locking, SDK or MIDI discovery.
        self.signer_patch.stop()
        lightpad = ROOT / "lightpad"
        target, _ = package.package(
            Path(os.environ["LIGHTPAD_BRIDGE_BINARY"]), lightpad / "performance-main.littlefoot",
            lightpad / "performance-metronome.littlefoot", "TESTSERIAL000001",
            self.root / "Audio.app", audio_performance=True,
            patch_program=lightpad / "performance-patch.littlefoot",
            status_program=lightpad / "status.littlefoot")
        executable = target / "Contents/MacOS/lightpad-bridge"
        info = json.loads(subprocess.check_output([str(executable), "--bundle-info"], text=True))
        self.assertEqual(info["controller_mode"], "audio-performance")
        self.assertEqual(info["serial"], "TESTSERIAL000001")
        resources = target / "Contents/Resources"
        for key, name in (("controller", "controller"), ("metronome", "metronome"),
                          ("patch", "patch"), ("status_program", "status")):
            self.assertEqual(Path(info[key]), resources / (name + ".littlefoot"))
        self.assertEqual(Path(info["status_file"]), Path.home() /
                         "Library/Application Support/Lightpad Bridge/status.json")
        # Legacy packages retain their original defaults, without implicit patch mode.
        legacy, _ = package.package(Path(os.environ["LIGHTPAD_BRIDGE_BINARY"]), self.controller,
                                    self.metronome, "TESTSERIAL000001", self.root / "Legacy.app")
        info = json.loads(subprocess.check_output(
            [str(legacy / "Contents/MacOS/lightpad-bridge"), "--bundle-info"], text=True))
        self.assertEqual(info["controller_mode"], "legacy")
        self.assertEqual(info["patch"], "")

    def test_missing_modal_and_invalid_serial_create_no_app(self):
        target = self.root / "Never.app"
        with self.assertRaises(ValueError):
            package.package(self.binary, self.controller, self.root / "missing", "TESTSERIAL000001", target)
        with self.assertRaises(ValueError):
            package.package(self.binary, self.controller, self.metronome, "short", target)
        self.assertFalse(target.exists())

    def test_recursive_include_tree_is_confined_and_cycles_are_rejected(self):
        source = self.root / "status.littlefoot"
        source.write_text('#include "part.littlefoot"\nvoid repaint() {}\n')
        part = self.root / "part.littlefoot"
        part.write_text('#include "nested/leaf.littlefoot"\n')
        (self.root / "nested").mkdir()
        leaf = self.root / "nested/leaf.littlefoot"
        leaf.write_text('int n;\n')
        self.assertEqual(set(package.source_files(source, "status.littlefoot")),
                         {"status.littlefoot", "part.littlefoot", "nested/leaf.littlefoot"})
        for invalid in ('../elsewhere.littlefoot', '/tmp/arbitrary.littlefoot', 'part.txt'):
            source.write_text('#include "' + invalid + '"\n')
            with self.assertRaises(ValueError):
                package.source_files(source, "status.littlefoot")
        source.write_text('#include "part.littlefoot"\n')
        part.write_text('#include "status.littlefoot"\n')
        with self.assertRaisesRegex(ValueError, "Cyclic"):
            package.source_files(source, "status.littlefoot")
        part.unlink()
        part.symlink_to(leaf)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            package.source_files(source, "status.littlefoot")

    def test_conflicting_program_dependency_destinations_leave_no_app(self):
        other = self.root / "other"
        other.mkdir()
        main = self.root / "main.littlefoot"
        modal = other / "modal.littlefoot"
        for entry in (main, modal):
            entry.write_text('#include "shared.littlefoot"\nvoid repaint() {}\n')
            (entry.parent / "shared.littlefoot").write_text('int ' + entry.stem + ';\n')
        target = self.root / "Conflict.app"
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            package.package(self.binary, main, modal, "TESTSERIAL000001", target)
        self.assertFalse(target.exists())

    @unittest.skipUnless(shutil.which("blocksctl"), "offline blocksctl compiler required")
    def test_nested_includes_compile_without_original_source_tree(self):
        source_tree = self.root / "original"
        source_tree.mkdir()
        (source_tree / "nested").mkdir()
        source = source_tree / "entry.littlefoot"
        source.write_text('#include "part.littlefoot"\nvoid repaint() { fillPixel(n, 0, 0xffffff); }\n')
        (source_tree / "part.littlefoot").write_text('#include "nested/leaf.littlefoot"\n')
        (source_tree / "nested/leaf.littlefoot").write_text('int n;\n')
        target, manifest = package.package(self.binary, source, self.metronome,
            "TESTSERIAL000001", self.root / "Nested.app")
        shutil.rmtree(source_tree)
        resources = target / "Contents/Resources"
        result = package.compile_program(resources / "controller.littlefoot", self.root / "nested.bin")
        self.assertEqual(result["binary_sha256"], manifest["programs"]["main"]["binary_sha256"])
        self.assertEqual(set(manifest["programs"]["main"]["includes_sha256"]),
                         {"part.littlefoot", "nested/leaf.littlefoot"})

    @unittest.skipUnless(sys.platform == "darwin", "macOS bundle signing required")
    def test_real_bundle_signature_binds_identity_and_info_plist(self):
        self.signer_patch.stop()
        app = self.root / "Signed.app"
        (app / "Contents/MacOS").mkdir(parents=True)
        (app / "Contents/Resources").mkdir()
        executable = app / 'Contents/MacOS/lightpad-bridge'
        shutil.copyfile('/usr/bin/true', executable)
        executable.chmod(0o755)
        info = app / 'Contents/Info.plist'
        info.write_bytes(plistlib.dumps(dict(CFBundleIdentifier=package.BUNDLE_ID,
            CFBundleExecutable='lightpad-bridge', CFBundlePackageType='APPL')))
        (app / 'Contents/Resources/fixture').write_text('sealed resource')
        package.sign_bundle(app)
        details = subprocess.run(['/usr/bin/codesign', '-dvv', str(app)],
            check=True, capture_output=True, text=True).stderr
        self.assertIn('Identifier=' + package.BUNDLE_ID, details)
        self.assertNotIn('Info.plist=not bound', details)
        self.assertNotIn('linker-signed', details)
        info.write_bytes(plistlib.dumps(dict(CFBundleIdentifier='wrong.identity',
            CFBundleExecutable='lightpad-bridge', CFBundlePackageType='APPL')))
        result = subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(app)],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, 'Info.plist must be part of the signature seal')

    @unittest.skipUnless(shutil.which("blocksctl"), "offline blocksctl compiler required")
    def test_signature_failure_removes_incomplete_candidate(self):
        self.signer.side_effect = RuntimeError('signature failed')
        target = self.root / 'Failed.app'
        with self.assertRaisesRegex(RuntimeError, 'signature failed'):
            package.package(self.binary, self.controller, self.metronome, 'TESTSERIAL000001', target)
        self.assertFalse(target.exists())

    def test_compiler_budget_gate_includes_heap(self):
        fake = self.root / "compiler"
        # A valid-length output with 7,100 code + 101 heap is still oversized.
        fake.write_text("#!/usr/bin/env python3\nimport pathlib,struct,sys\nb=bytearray(7100)\nstruct.pack_into('<5H',b,0,0,7100,0,0,101)\npathlib.Path(sys.argv[-1]).write_bytes(b)\n")
        fake.chmod(0o755)
        target = self.root / "Never.app"
        with self.assertRaisesRegex(ValueError, "budget"):
            package.package(self.binary, self.controller, self.metronome,
                            "TESTSERIAL000001", target, compiler=str(fake))
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
