"""Startup isolation for the no-MIDI trial, without a native Live process."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_surface
from LightpadController import configuration as config


class Probe:
    def __init__(self, song, manifest, q_bar, monitor_off, emit=None):
        self.enabled, self.closed, self.polls = False, False, 0
        self.enums = (q_bar, monitor_off)

    def dispatch(self, envelope):
        raise AssertionError("construction/MIDI must not dispatch commands")

    def poll(self):
        self.polls += 1

    def close(self):
        self.closed = True


class ProbeSurfaceTests(unittest.TestCase):
    def test_disabled_startup_advertises_only_probe_ops_and_ignores_midi(self):
        path = Path(__file__).resolve().parents[1] / "LightpadController" / "controller_probe_surface.py"
        spec = importlib.util.spec_from_file_location("LightpadController.probe_surface_startup_test", path)
        module = importlib.util.module_from_spec(spec)
        native = SimpleNamespace(Song=SimpleNamespace(Quantization=SimpleNamespace(q_bar=4)),
            Track=SimpleNamespace(Track=SimpleNamespace(monitoring_states=SimpleNamespace(OFF=2))))
        operations = frozenset(("inspect", "enable", "begin", "cancel", "disable"))
        with patch.dict(sys.modules, {"Live": native,
                "_Framework.ControlSurface": SimpleNamespace(ControlSurface=test_surface.FakeSurface),
                "LightpadController.controller_probe": SimpleNamespace(ControllerProbe=Probe, OPERATIONS=operations)}):
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix="lightpad-probe-startup-") as directory:
            with patch.multiple(config, CONTROLLER_PROBE_DIRECTORY=directory,
                    AUDIO_CONTROLLER_MANIFEST="explicit-manifest", SYNC_UI_EVIDENCE=None), \
                    patch.object(module, "load_audio_configuration", return_value={}):
                instance = SimpleNamespace(song=object(), sent=[], logs=[])
                surface = module.ControllerProbeSurface(instance)
                self.assertIsNotNone(surface._probe, instance.logs)
                self.assertFalse(surface._probe.enabled)
                self.assertEqual(surface._probe.enums, (4, 2))
                state = json.loads(Path(directory, "proof-state.json").read_text())
                self.assertTrue(state["driver_disabled"])
                self.assertEqual(state["operations"], sorted(operations))
                surface.receive_midi((0xB0, 100, 1))
                surface.update_display()
                self.assertEqual(surface._probe.polls, 1)
                self.assertEqual(instance.sent, [])
                surface.disconnect()
                self.assertTrue(surface._probe.closed)


if __name__ == "__main__":
    unittest.main()
