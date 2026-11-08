"""Package the real compiled pad sources offline; never launch the staged app."""
import importlib.util
import json
from pathlib import Path
import plistlib
import shutil
import struct
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location("lightpad_package", HERE / "package.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="lightpad-package-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.binary = self.root / "helper"
        self.binary.write_bytes(b"staging-test-only-not-executable")
        self.controller = ROOT / "prototypes/controller/lightpad/main.littlefoot"
        self.metronome = ROOT / "prototypes/controller/lightpad/metronome.littlefoot"

    @unittest.skipUnless(shutil.which("blocksctl"), "offline blocksctl compiler required")
    def test_package_contains_both_exact_sources_and_modal_launcher(self):
        target, manifest = package.package(
            self.binary, self.controller, self.metronome, "TESTSERIAL000001",
            self.root / "Lightpad Bridge.app")
        resources = target / "Contents/Resources"
        self.assertEqual((resources / "controller.littlefoot").read_bytes(), self.controller.read_bytes())
        self.assertEqual((resources / "metronome.littlefoot").read_bytes(), self.metronome.read_bytes())
        self.assertEqual((resources / "lightpad-bridge").read_bytes(), self.binary.read_bytes())
        launcher = (target / "Contents/MacOS/Lightpad Bridge").read_text()
        self.assertIn('--modal-source "$bundle_root/Resources/metronome.littlefoot"', launcher)
        self.assertNotIn("--trial-cycles", launcher)
        self.assertEqual(plistlib.loads((target / "Contents/Info.plist").read_bytes())[
            "LightpadBridgeDeviceSerial"], "TESTSERIAL000001")
        self.assertEqual(manifest["programs"]["main"]["total"], 6279)
        self.assertEqual(manifest["programs"]["metronome"]["total"], 4319)
        self.assertFalse(manifest["saved_default_writes"])
        self.assertFalse(manifest["automatic_login_start"])
        self.assertEqual(json.loads((resources / "build-info.json").read_text()), manifest)
        self.assertNotIn(str(ROOT), (resources / "build-info.json").read_text())
        with self.assertRaises(ValueError):
            package.package(self.binary, self.controller, self.metronome,
                            "TESTSERIAL000001", target)

    def test_missing_modal_and_invalid_serial_create_no_app(self):
        target = self.root / "Never.app"
        with self.assertRaises(ValueError):
            package.package(self.binary, self.controller, self.root / "missing", "TESTSERIAL000001", target)
        with self.assertRaises(ValueError):
            package.package(self.binary, self.controller, self.metronome, "short", target)
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
