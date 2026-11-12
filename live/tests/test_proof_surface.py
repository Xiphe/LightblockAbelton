"""Startup contract with Live's module/class enum layout and no native writes."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class Surface:
    def __init__(self, instance):
        self._instance = instance

    def song(self):
        return self._instance.song

    def log_message(self, message):
        self._instance.logs.append(message)


class Driver:
    def __init__(self, song, manifest, q_bar, monitor_off, emit):
        self.enabled = False
        self.monitor_off = monitor_off

    def dispatch(self, command):
        raise AssertionError("startup must not dispatch a command")


class Mailbox:
    def __init__(self, directory, dispatch, emit):
        self.driver_disabled = True


class ProofSurfaceStartupTests(unittest.TestCase):
    def test_native_track_class_enum_reaches_disabled_mailbox_without_commands(self):
        native = SimpleNamespace(Song=SimpleNamespace(Quantization=SimpleNamespace(q_bar=4)),
            Track=SimpleNamespace(Track=SimpleNamespace(monitoring_states=SimpleNamespace(OFF=2))))
        path = Path(__file__).resolve().parents[1] / "LightpadController" / "proof_surface.py"
        spec = importlib.util.spec_from_file_location("LightpadController.proof_surface_startup_test", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"Live": native,
                "_Framework.ControlSurface": SimpleNamespace(ControlSurface=Surface)}):
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix="lightpad-startup-") as directory:
            Path(directory, "fixture-manifest.json").write_text(json.dumps({"fixture": "fake"}))
            with patch.multiple(module, PROOF_DIRECTORY=directory, AudioProofDriver=Driver, Mailbox=Mailbox):
                instance = SimpleNamespace(song=object(), logs=[])
                surface = module.AudioProofSurface(instance)
                self.assertIsNotNone(surface._mailbox, instance.logs)
                self.assertFalse(surface._driver.enabled)
                self.assertEqual(surface._driver.monitor_off, 2)
                self.assertTrue(surface._mailbox.driver_disabled)


if __name__ == "__main__":
    unittest.main()
